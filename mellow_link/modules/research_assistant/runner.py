from __future__ import annotations

import asyncio
import threading
import time
import logging

from mellow_link import app_state
from mellow_link.config.settings import get_settings
from mellow_link.services.llm_service import LLMServiceError, LLMServiceTimeoutError
from mellow_link.infra.run_events import (
    EVENT_TYPE_LOG,
    EVENT_TYPE_PLAN_CREATED,
    EVENT_TYPE_RUN_FINISHED,
    EVENT_TYPE_RUN_STARTED,
    EVENT_TYPE_TODO_DONE,
    EVENT_TYPE_TODO_STARTED,
    emit_event,
)

from .service import ResearchAssistantService

logger = logging.getLogger(__name__)


class ResearchRunAborted(Exception):
    def __init__(self, stage: str):
        self.stage = stage
        super().__init__(f"Research run aborted at stage={stage}")


def start_research_run(
    run_id: str,
    session_id: str | None,
    question: str,
    context_note: str = "",
    temp_session_id: str | None = None,
) -> None:
    todos = [
        {"todo_id": "R1", "title": "질문 정리", "status": "pending"},
        {"todo_id": "R2", "title": "문서 문맥 수집", "status": "pending"},
        {"todo_id": "R3", "title": "문서 기반 분석", "status": "pending"},
        {"todo_id": "R4", "title": "결과 요약", "status": "pending"},
    ]

    async def _run_async() -> None:
        svc = ResearchAssistantService()
        llm = getattr(app_state, "llm_service", None)
        if not llm:
            raise RuntimeError("LLM service not initialized")
        try:
            from mellow_link.routers.runs import RUN_CONTROL_STATE

            document_context = ""
            if temp_session_id:
                document_context = str(app_state.TEMP_CONTEXT_STORE.get(temp_session_id, "") or "")
            has_document_context = bool(document_context)
            logger.info(
                "[ResearchAssistant] run_id=%s temp_session_id=%s document_chars=%s store_keys=%s",
                run_id,
                temp_session_id,
                len(document_context),
                list(app_state.TEMP_CONTEXT_STORE.keys())[:10],
            )
            model_name = llm.get_model_for_mode("research")
            research_timeout_seconds = float(get_settings().research_timeout)
            question_chars = len(question or "")
            document_chars = len(document_context or "")
            effective_timeout_source = "http_client"

            def _control_state():
                return RUN_CONTROL_STATE.setdefault(
                    run_id,
                    {"paused": False, "abort_requested": False, "running": True},
                )

            def _abort_payload(stage: str) -> dict:
                return {
                    "success": False,
                    "summary": "Run aborted by operator.",
                    "finish_reason": "operator_abort",
                    "abort_requested": True,
                    "abort_handled": True,
                    "abort_stage": stage,
                    "failure_reason": "aborted_by_user",
                    "module_id": "research_assistant",
                    "run_kind": "research_run",
                    "model": model_name,
                    "timeout_seconds": research_timeout_seconds,
                    "effective_timeout_source": effective_timeout_source,
                }

            async def _abort_if_requested(stage: str) -> None:
                state = _control_state()
                if not state.get("abort_requested"):
                    return
                state["running"] = False
                state["paused"] = False
                emit_event(
                    run_id,
                    EVENT_TYPE_LOG,
                    {
                        "level": "warning",
                        "message": f"research run aborted at {stage}",
                        "abort_requested": True,
                        "abort_handled": True,
                        "abort_stage": stage,
                        "failure_reason": "aborted_by_user",
                    },
                )
                emit_event(run_id, EVENT_TYPE_RUN_FINISHED, _abort_payload(stage))
                raise ResearchRunAborted(stage)

            async def _generate_once(attempt_no: int, prompt: str, num_ctx: int, temperature: float) -> tuple[str, int, str]:
                started = time.perf_counter()
                prompt_chars = len(prompt or "")
                logger.info(
                    "[ResearchAssistant] attempt=%s mode=%s model=%s timeout_seconds=%s "
                    "effective_timeout_source=%s question_chars=%s document_chars=%s prompt_chars=%s",
                    attempt_no,
                    "research",
                    model_name,
                    research_timeout_seconds,
                    effective_timeout_source,
                    question_chars,
                    document_chars,
                    prompt_chars,
                )
                emit_event(
                    run_id,
                    EVENT_TYPE_LOG,
                    {
                        "level": "info",
                        "message": f"research attempt {attempt_no}",
                        "attempt": attempt_no,
                        "mode": "research",
                        "model": model_name,
                        "timeout_seconds": research_timeout_seconds,
                        "effective_timeout_source": effective_timeout_source,
                        "question_chars": question_chars,
                        "document_chars": document_chars,
                        "prompt_chars": prompt_chars,
                    },
                )
                try:
                    result = await llm.generate(
                        prompt=prompt,
                        mode="research",
                        context_id=f"research:{run_id}:attempt:{attempt_no}",
                        auto_unload=False,
                        request_timeout_seconds=research_timeout_seconds,
                        options={"num_ctx": num_ctx, "temperature": temperature},
                    )
                    elapsed_ms = round((time.perf_counter() - started) * 1000)
                    content = result.content or ""
                    cause = "empty_output" if not content.strip() else ("weak_output" if svc.is_weak_summary(content) else "ok")
                    logger.info(
                        "[ResearchAssistant] attempt=%s mode=%s cause=%s elapsed_ms=%s model=%s "
                        "timeout_seconds=%s effective_timeout_source=%s prompt_chars=%s document_chars=%s",
                        attempt_no,
                        "research",
                        cause,
                        elapsed_ms,
                        model_name,
                        research_timeout_seconds,
                        effective_timeout_source,
                        prompt_chars,
                        document_chars,
                    )
                    emit_event(
                        run_id,
                        EVENT_TYPE_LOG,
                        {
                            "level": "info",
                            "message": f"research attempt {attempt_no} finished",
                            "attempt": attempt_no,
                            "mode": "research",
                            "elapsed_ms": elapsed_ms,
                            "failure_reason": None if cause == "ok" else cause,
                            "model": model_name,
                            "timeout_seconds": research_timeout_seconds,
                            "effective_timeout_source": effective_timeout_source,
                            "prompt_chars": prompt_chars,
                            "document_chars": document_chars,
                        },
                    )
                    return content, elapsed_ms, cause
                except LLMServiceTimeoutError as e:
                    logger.warning(
                        "[ResearchAssistant] attempt=%s mode=%s cause=timeout elapsed_ms=%s model=%s "
                        "timeout_seconds=%s effective_timeout_source=%s prompt_chars=%s document_chars=%s error=%s",
                        attempt_no,
                        e.mode,
                        e.elapsed_ms,
                        e.model,
                        e.timeout_seconds,
                        e.effective_timeout_source,
                        prompt_chars,
                        document_chars,
                        e,
                    )
                    emit_event(
                        run_id,
                        EVENT_TYPE_LOG,
                        {
                            "level": "warning",
                            "message": f"research attempt {attempt_no} timeout",
                            "attempt": attempt_no,
                            "mode": e.mode,
                            "elapsed_ms": e.elapsed_ms,
                            "failure_reason": "timeout",
                            "model": e.model,
                            "timeout_seconds": e.timeout_seconds,
                            "effective_timeout_source": e.effective_timeout_source,
                            "prompt_chars": prompt_chars,
                            "document_chars": document_chars,
                        },
                    )
                    return "", e.elapsed_ms, "timeout"
                except (asyncio.TimeoutError, LLMServiceError) as e:
                    elapsed_ms = round((time.perf_counter() - started) * 1000)
                    logger.warning(
                        "[ResearchAssistant] attempt=%s mode=%s cause=timeout elapsed_ms=%s model=%s "
                        "timeout_seconds=%s effective_timeout_source=%s prompt_chars=%s document_chars=%s error=%s",
                        attempt_no,
                        "research",
                        elapsed_ms,
                        model_name,
                        research_timeout_seconds,
                        effective_timeout_source,
                        prompt_chars,
                        document_chars,
                        e,
                    )
                    emit_event(
                        run_id,
                        EVENT_TYPE_LOG,
                        {
                            "level": "warning",
                            "message": f"research attempt {attempt_no} timeout",
                            "attempt": attempt_no,
                            "mode": "research",
                            "elapsed_ms": elapsed_ms,
                            "failure_reason": "timeout",
                            "model": model_name,
                            "timeout_seconds": research_timeout_seconds,
                            "effective_timeout_source": effective_timeout_source,
                            "prompt_chars": prompt_chars,
                            "document_chars": document_chars,
                        },
                    )
                    return "", elapsed_ms, "timeout"

            emit_event(
                run_id,
                EVENT_TYPE_RUN_STARTED,
                {
                    "user_input": question[:200],
                    "mode": "research",
                    "session_id": session_id,
                    "temp_session_id": temp_session_id,
                    "has_document_context": has_document_context,
                },
            )
            emit_event(run_id, EVENT_TYPE_PLAN_CREATED, {"todos": todos})
            emit_event(run_id, EVENT_TYPE_TODO_STARTED, todos[0])
            emit_event(
                run_id,
                EVENT_TYPE_TODO_DONE,
                {
                    **todos[0],
                    "detail": "질문과 요청 형식을 분석했습니다.",
                },
            )
            await _abort_if_requested("attempt_1")

            emit_event(run_id, EVENT_TYPE_TODO_STARTED, todos[1])
            context_detail = "업로드된 문서가 없어 일반 리서치 문맥으로 진행합니다."
            if document_context:
                context_detail = f"업로드 문서 문맥 {min(len(document_context), svc.MAX_DOCUMENT_CHARS)}자를 분석 입력으로 반영했습니다."
            emit_event(
                run_id,
                EVENT_TYPE_TODO_DONE,
                {
                    **todos[1],
                    "detail": context_detail,
                },
            )
            await _abort_if_requested("attempt_1")

            emit_event(run_id, EVENT_TYPE_TODO_STARTED, todos[2])
            retry_count = 0
            fallback_used = False
            degraded = False
            failure_reason = None
            attempt_elapsed_ms = []

            await _abort_if_requested("attempt_1")
            primary_prompt = svc.build_prompt(question, context_note, document_context=document_context)
            await _abort_if_requested("attempt_1")
            raw_summary, elapsed_ms, attempt_cause = await _generate_once(1, primary_prompt, num_ctx=3072, temperature=0.2)
            attempt_elapsed_ms.append(elapsed_ms)
            if attempt_cause != "ok":
                failure_reason = attempt_cause
            await _abort_if_requested("attempt_1")

            if not raw_summary or svc.is_weak_summary(raw_summary):
                retry_count = 1
                fallback_used = True
                await _abort_if_requested("attempt_2")
                reduced_prompt = svc.build_reduced_prompt(question, context_note, document_context=document_context)
                await _abort_if_requested("attempt_2")
                raw_summary, elapsed_ms, attempt_cause = await _generate_once(2, reduced_prompt, num_ctx=2048, temperature=0.1)
                attempt_elapsed_ms.append(elapsed_ms)
                if attempt_cause != "ok":
                    failure_reason = attempt_cause
                else:
                    failure_reason = None
                await _abort_if_requested("attempt_2")

            if not raw_summary or svc.is_weak_summary(raw_summary):
                degraded = True
                fallback_used = True
                if not failure_reason:
                    failure_reason = "empty_output"
                raw_summary = ""

            await _abort_if_requested("finalize")
            summary = svc.format_user_summary(
                str(raw_summary),
                question=question,
                has_document_context=has_document_context,
            )
            await _abort_if_requested("finalize")
            emit_event(
                run_id,
                EVENT_TYPE_TODO_DONE,
                {
                    **todos[2],
                    "detail": "문서와 질문을 기준으로 분석 응답을 생성했습니다.",
                },
            )
            emit_event(run_id, EVENT_TYPE_TODO_STARTED, todos[3])
            emit_event(
                run_id,
                EVENT_TYPE_TODO_DONE,
                {
                    **todos[3],
                    "detail": "사용자 콘솔에 표시할 요약 결과를 정리했습니다.",
                },
            )
            await _abort_if_requested("finalize")
            emit_event(
                run_id,
                EVENT_TYPE_RUN_FINISHED,
                {
                    "success": True,
                    "summary": str(summary)[:4000],
                    "fallback_used": fallback_used,
                    "failure_reason": failure_reason,
                    "retry_count": retry_count,
                    "degraded": degraded,
                    "attempt_elapsed_ms": attempt_elapsed_ms,
                    "model": model_name,
                    "module_id": "research_assistant",
                    "run_kind": "research_run",
                },
            )
            _control_state()["running"] = False
        except ResearchRunAborted:
            pass
        except Exception as e:
            failure_summary = svc.format_user_summary(
                "",
                question=question,
                has_document_context=has_document_context,
            )
            emit_event(
                run_id,
                EVENT_TYPE_RUN_FINISHED,
                {
                    "success": True,
                    "summary": f"{failure_summary}\n\n추가 정보\n- 실행 중 오류: {str(e)[:240]}",
                    "fallback_used": True,
                    "failure_reason": "unexpected_error",
                    "retry_count": 0,
                    "degraded": True,
                    "model": model_name if 'model_name' in locals() else None,
                    "timeout_seconds": research_timeout_seconds if 'research_timeout_seconds' in locals() else None,
                    "effective_timeout_source": effective_timeout_source if 'effective_timeout_source' in locals() else None,
                    "module_id": "research_assistant",
                    "run_kind": "research_run",
                },
            )
        finally:
            try:
                from mellow_link.routers.runs import RUN_CONTROL_STATE

                if run_id in RUN_CONTROL_STATE:
                    RUN_CONTROL_STATE[run_id]["running"] = False
                    if RUN_CONTROL_STATE[run_id].get("abort_requested"):
                        RUN_CONTROL_STATE[run_id]["paused"] = False
            except Exception:
                pass
            for attempt_no in (1, 2):
                try:
                    llm.clear_context(f"research:{run_id}:attempt:{attempt_no}")
                except Exception:
                    pass
            try:
                if llm._current_model == model_name:
                    await llm.unload_model()
                    await llm.cleanup_stale_models(current_model=None)
            except Exception as cleanup_error:
                logger.warning("[ResearchAssistant] cleanup failed for run %s: %s", run_id, cleanup_error)

    def _run_thread() -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(_run_async())
        finally:
            loop.close()

    threading.Thread(target=_run_thread, daemon=True).start()
