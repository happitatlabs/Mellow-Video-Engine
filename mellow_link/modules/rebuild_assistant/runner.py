from __future__ import annotations

import threading

from mellow_link import app_state
from mellow_link.infra.run_events import (
    EVENT_TYPE_DEBUG_ANONYMIZATION_REPORT,
    EVENT_TYPE_LOG,
    EVENT_TYPE_PLAN_CREATED,
    EVENT_TYPE_RUN_FINISHED,
    EVENT_TYPE_RUN_STARTED,
    EVENT_TYPE_TODO_DONE,
    EVENT_TYPE_TODO_STARTED,
    emit_event,
)

from .anonymization_exposure import (
    build_anonymization_debug_payload,
    build_anonymization_summary,
)
from .schemas import RebuildAssetsPayload
from .service import RebuildAssistantService


def start_rebuild_assistant_run(
    run_id: str,
    session_id: str | None,
    *,
    goal: str,
    assets: RebuildAssetsPayload,
    constraints: list[str] | None = None,
    temp_session_id: str | None = None,
) -> None:
    todos = [
        {"todo_id": "B1", "title": "입력 준비", "status": "pending"},
        {"todo_id": "B2", "title": "레거시 분석", "status": "pending"},
        {"todo_id": "B3", "title": "재구성 설계", "status": "pending"},
        {"todo_id": "B4", "title": "초안 생성", "status": "pending"},
        {"todo_id": "B5", "title": "결과 정리", "status": "pending"},
    ]

    def _run() -> None:
        service = RebuildAssistantService()
        temp_context = str(app_state.TEMP_CONTEXT_STORE.get(temp_session_id, "") or "") if temp_session_id else ""
        try:
            emit_event(
                run_id,
                EVENT_TYPE_RUN_STARTED,
                {
                    "user_input": goal[:200],
                    "mode": "module",
                    "session_id": session_id,
                    "temp_session_id": temp_session_id,
                },
            )
            emit_event(run_id, EVENT_TYPE_PLAN_CREATED, {"todos": todos})

            emit_event(run_id, EVENT_TYPE_TODO_STARTED, todos[0])
            prepared = service.prepare_input(
                goal=goal,
                assets=assets,
                constraints=constraints,
                temp_context=temp_context,
                temp_source_ref=temp_session_id,
            )
            if prepared.assembler_output is not None and prepared.anonymization_output is not None:
                anonymization_summary = build_anonymization_summary(prepared.anonymization_output)
                anonymization_debug = build_anonymization_debug_payload(
                    original_input=prepared.assembler_output,
                    anonymization_output=prepared.anonymization_output,
                )
                emit_event(
                    run_id,
                    EVENT_TYPE_DEBUG_ANONYMIZATION_REPORT,
                    anonymization_debug.model_dump(),
                )
                # This log event is the audit-trace minimum for anonymization and is not a debug payload substitute.
                emit_event(
                    run_id,
                    EVENT_TYPE_LOG,
                    {
                        "level": "info",
                        "message": "anonymization complete",
                        "policy_version": anonymization_summary.policy_version,
                        "applied": anonymization_summary.applied,
                        "total_replacements": anonymization_summary.total_replacements,
                        "validation_passed": anonymization_debug.validation.passed,
                    },
                )
            else:
                anonymization_summary = None
            emit_event(
                run_id,
                EVENT_TYPE_TODO_DONE,
                {**todos[0], "detail": "입력 검증, 업로드 문맥 수집, 익명화 결과를 확인했습니다."},
            )

            emit_event(run_id, EVENT_TYPE_TODO_STARTED, todos[1])
            analysis_summary = service.analyze_assets(prepared)
            emit_event(run_id, EVENT_TYPE_LOG, {"level": "info", "message": "legacy analysis complete", "findings": analysis_summary[:3]})
            emit_event(run_id, EVENT_TYPE_TODO_DONE, {**todos[1], "detail": "레거시 구조와 결합 지점을 분석했습니다."})

            emit_event(run_id, EVENT_TYPE_TODO_STARTED, todos[2])
            strategy = service.infer_target_architecture(prepared)
            emit_event(run_id, EVENT_TYPE_LOG, {"level": "info", "message": "rebuild design complete", "strategy": strategy[:3]})
            emit_event(run_id, EVENT_TYPE_TODO_DONE, {**todos[2], "detail": "목표 아키텍처와 레이어별 재구성 방향을 설계했습니다."})

            emit_event(run_id, EVENT_TYPE_TODO_STARTED, todos[3])
            draft = service.build_recomposition_draft(prepared)
            emit_event(
                run_id,
                EVENT_TYPE_LOG,
                {
                    "level": "info",
                    "message": "recomposition draft prepared",
                    "draft_layers": {"database": len(draft.database), "backend": len(draft.backend), "frontend": len(draft.frontend)},
                },
            )
            emit_event(run_id, EVENT_TYPE_TODO_DONE, {**todos[3], "detail": "레이어별 재구성 초안을 생성했습니다."})

            emit_event(run_id, EVENT_TYPE_TODO_STARTED, todos[4])
            result = service.build_result(prepared)
            needs_more_input = bool(result.missing_context or result.confidence < 0.45)
            summary = service.format_user_summary(result, scope_limited=prepared.scope_limited, needs_more_input=needs_more_input)
            emit_event(run_id, EVENT_TYPE_TODO_DONE, {**todos[4], "detail": "구조화 결과와 사용자 요약을 정리했습니다."})
            emit_event(
                run_id,
                EVENT_TYPE_RUN_FINISHED,
                {
                    "success": True,
                    "summary": summary[:4000],
                    "structured_result": result.model_dump(),
                    "primary_feature_mode": prepared.signals.primary_feature_mode,
                    "secondary_feature_mode": prepared.signals.secondary_feature_mode,
                    "confidence": result.confidence,
                    "needs_more_input": needs_more_input,
                    "scope_limited": prepared.scope_limited,
                    "anonymization_summary": anonymization_summary.model_dump() if anonymization_summary else None,
                    "module_id": "rebuild_assistant",
                    "run_kind": "rebuild_plan",
                },
            )
        except Exception as e:
            emit_event(
                run_id,
                EVENT_TYPE_RUN_FINISHED,
                {
                    "success": False,
                    "summary": f"Rebuild assistant failed: {str(e)[:300]}",
                    "module_id": "rebuild_assistant",
                    "run_kind": "rebuild_plan",
                },
            )

    threading.Thread(target=_run, daemon=True).start()
