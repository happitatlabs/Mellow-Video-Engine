"""
Engine-backed Runtime: 2단 정식 파이프라인.

1단 GM(Decision): intent/slots/confidence + state_summary, optional tool/observation.
   - JSON만 반환 강제, free/small 모델 고정.
2단 Character Render: GM 결과 → speech/passage/ooc 생성.
   - model_tier(pro)는 여기서만 적용.
"""

import json
import logging
import re
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from .schemas import (
    TurnRequest,
    TurnResponse,
    TurnPayload,
    TurnState,
    TurnMeta,
    ClarifyPayload,
    GMResult,
    StatusResponse,
    StatusRuntime,
    StatusHealth,
    StatusCounters,
)

logger = logging.getLogger(__name__)

# GM JSON 파싱 실패 시 로그에 남길 최대 문자 수
GM_RAW_ANSWER_LOG_MAX_CHARS = 500


def _passage_fallback(speaker_name: str, trace_id: str) -> str:
    """passage가 null/빈 문자열일 때 사용하는 안전 fallback."""
    name = (speaker_name or "캐릭터").strip() or "캐릭터"
    return f"{name}는 잠시 상황을 살피며 다음 행동을 준비했다."


class GMParseError(Exception):
    """GM 단계 JSON 파싱 실패."""
    def __init__(self, message: str, raw_answer: str = ""):
        super().__init__(message)
        self.raw_answer = raw_answer


def _new_trace_id() -> str:
    return f"trc_{datetime.utcnow().strftime('%Y%m%d')}_{uuid.uuid4().hex[:8]}"


def _load_prompt(name: str) -> str:
    """runtime/prompts/<name>.txt 로드."""
    base = Path(__file__).resolve().parent
    path = base / "prompts" / f"{name}.txt"
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8").strip()


def _extract_json_from_content(content: str) -> str:
    """마크다운 코드블록 제거 후 JSON 문자열 반환."""
    text = (content or "").strip()
    # ```json ... ``` or ``` ... ```
    m = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if m:
        return m.group(1).strip()
    return text


def _parse_gm_result(content: str) -> GMResult:
    """LLM 응답 텍스트를 GMResult로 파싱. 실패 시 GMParseError."""
    raw = _extract_json_from_content(content)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        to_log = (content or "")[:GM_RAW_ANSWER_LOG_MAX_CHARS]
        logger.error("[Runtime GM] JSON parse failed: %s. Raw (max %d chars): %s", e, GM_RAW_ANSWER_LOG_MAX_CHARS, to_log)
        raise GMParseError(f"GM JSON parse failed: {e}", raw_answer=content or "")
    if not isinstance(data, dict):
        raise GMParseError("GM result is not a JSON object", raw_answer=content or "")
    try:
        return GMResult.model_validate(data)
    except Exception as e:
        logger.exception("[Runtime GM] GMResult validation failed: %s", e)
        raise GMParseError(str(e), raw_answer=content or "")


class EngineBackedAdapter:
    """
    2단 파이프라인: GM(Decision) → Character Render.
    - GM: run_gm_step() — 항상 free/small, JSON만.
    - Render: run_character_render_step() — model_tier 적용.
    """

    def __init__(self, orchestrator=None, run_id_factory=None):
        self._orchestrator = orchestrator
        self._run_id_factory = run_id_factory or (lambda: str(uuid.uuid4()))
        self._start_time: Optional[datetime] = None
        self._turns_1m: int = 0
        self._latencies_ms: list = []
        self._gm_prompt: Optional[str] = None
        self._character_prompt: Optional[str] = None

    def _get_llm_service(self):
        if not self._orchestrator:
            return None
        return self._orchestrator.get_service("llm")

    def _get_gm_prompt(self) -> str:
        if self._gm_prompt is None:
            self._gm_prompt = _load_prompt("gm_prompt")
        return self._gm_prompt or "Output only a JSON object with keys: speaker, intent, confidence, slots, state_summary, observation, needs_clarify, clarify. No other text."

    def _get_character_prompt(self) -> str:
        if self._character_prompt is None:
            self._character_prompt = _load_prompt("character_prompt")
        return self._character_prompt or "Output only JSON with keys: speech, passage, ooc. No other text."

    async def run_gm_step(self, req: TurnRequest, trace_id: str) -> GMResult:
        """
        1단: GM(Decision). JSON만 반환, free/small 고정. model_tier_requested 무시.
        """
        llm = self._get_llm_service()
        if not llm:
            raise RuntimeError("LLM service not available")
        user_text = req.input.text
        character_id = (req.context.character_id or "default") if req.context else "default"
        system_prompt = self._get_gm_prompt()
        user_prompt = f"character_id: {character_id}\nuser message: {user_text}"
        # GM은 항상 fast(free/small)
        result = await llm.generate(
            prompt=user_prompt,
            system_prompt=system_prompt,
            mode="fast",
            tools=None,
        )
        content = getattr(result, "content", "") or ""
        return _parse_gm_result(content)

    async def run_character_render_step(
        self,
        gm_result: GMResult,
        req: TurnRequest,
        model_tier_effective: str,
        trace_id: str,
    ) -> tuple[str, str, Optional[str]]:
        """
        2단: Character Render. model_tier_effective가 pro면 pro(thinking) 모델 사용.
        user_action을 GM에서 전달받아 passage에 반영 요청.
        Returns (speech, passage, ooc). passage는 항상 비빈 문자열(필요 시 fallback).
        """
        speaker_name = (gm_result.speaker and gm_result.speaker.name) or "캐릭터"
        fallback_passage = _passage_fallback(speaker_name, trace_id)

        llm = self._get_llm_service()
        if not llm:
            return "(캐릭터 응답을 생성할 수 없습니다.)", fallback_passage, None
        user_text = req.input.text
        user_action = gm_result.user_action
        character_id = (req.context.character_id or "default") if req.context else "default"
        gm_json = gm_result.model_dump_json(exclude_none=True)
        system_prompt = self._get_character_prompt()
        user_prompt = (
            f"GM result (JSON):\n{gm_json}\n\n"
            f"User message: {user_text}\n"
            f"user_action: {json.dumps(user_action, ensure_ascii=False) if user_action else 'null'}\n"
            f"character_id: {character_id}\n"
            f"model_tier_effective: {model_tier_effective}\n\n"
            "Output character reply as JSON (speech, passage, ooc). passage is required and must be at least one sentence."
        )
        mode = "thinking" if model_tier_effective == "pro" else "fast"
        result = await llm.generate(
            prompt=user_prompt,
            system_prompt=system_prompt,
            mode=mode,
            tools=None,
        )
        content = getattr(result, "content", "") or ""
        raw = _extract_json_from_content(content)
        try:
            data = json.loads(raw)
            speech = (data.get("speech") or "").strip() or "(응답 없음)"
            passage_raw = data.get("passage")
            passage = (passage_raw if isinstance(passage_raw, str) else "").strip()
            if not passage:
                logger.warning("passage fallback used", extra={"trace_id": trace_id})
                passage = fallback_passage
            ooc = data.get("ooc")
            return speech, passage, ooc
        except json.JSONDecodeError:
            return (content.strip() or "(응답 없음)")[:2000], fallback_passage, None

    async def turn(self, req: TurnRequest, trace_id: Optional[str] = None) -> TurnResponse:
        trace_id = trace_id or _new_trace_id()
        start = time.perf_counter()
        session_id = req.session_id
        state_version = 1
        model_tier_requested = (req.context.model_tier_requested or "auto").strip().lower() if req.context else "auto"
        # pro 요청 시에만 pro 허용 (권한/쿼터는 TODO)
        model_tier_effective = "pro" if model_tier_requested == "pro" else "free"
        engine_ref: Optional[str] = None
        turn_id = f"turn_{uuid.uuid4().hex[:8]}"

        try:
            # 1단: GM
            gm_result = await self.run_gm_step(req, trace_id)
            engine_ref = self._run_id_factory()

            # needs_clarify면 Character Render 호출하지 않고 clarify만 반환 (passage는 fallback으로 항상 채움)
            if gm_result.needs_clarify and gm_result.clarify:
                clarify_payload = ClarifyPayload(
                    question=gm_result.clarify.question,
                    options=gm_result.clarify.options,
                    reason="intent_confidence_low",
                )
                speech = (gm_result.clarify.question or "").strip() or "확인해 주세요."
                speaker_name = (gm_result.speaker and gm_result.speaker.name) or "캐릭터"
                passage_clarify = _passage_fallback(speaker_name, trace_id)
                latency_ms = (time.perf_counter() - start) * 1000
                self._turns_1m += 1
                self._latencies_ms.append(latency_ms)
                if len(self._latencies_ms) > 1000:
                    self._latencies_ms = self._latencies_ms[-500:]
                return TurnResponse(
                    turn=TurnPayload(
                        id=turn_id,
                        speech=speech,
                        passage=passage_clarify,
                        ooc=None,
                        actions=None,
                        clarify=clarify_payload,
                    ),
                    state=TurnState(
                        session_id=session_id,
                        state_version=state_version,
                        system_state=self._system_state_str(),
                        model_tier_effective=model_tier_effective,
                    ),
                    meta=TurnMeta(
                        trace_id=trace_id,
                        runtime_impl="engine-backed",
                        latency_ms=round(latency_ms, 2),
                        created_at=datetime.utcnow(),
                        engine_ref=engine_ref,
                    ),
                )

            # 2단: Character Render (model_tier 적용)
            speech, passage, ooc = await self.run_character_render_step(
                gm_result, req, model_tier_effective, trace_id
            )
        except GMParseError as e:
            logger.error("[Runtime] GM parse failed: %s", e)
            raise
        except Exception as e:
            logger.exception("Engine turn failed: %s", e)
            raise

        latency_ms = (time.perf_counter() - start) * 1000
        self._turns_1m += 1
        self._latencies_ms.append(latency_ms)
        if len(self._latencies_ms) > 1000:
            self._latencies_ms = self._latencies_ms[-500:]

        # passage는 항상 비빈 문자열 (run_character_render_step에서 이미 fallback 적용)
        return TurnResponse(
            turn=TurnPayload(
                id=turn_id,
                speech=speech or "",
                passage=passage or _passage_fallback("캐릭터", trace_id),
                ooc=ooc,
                actions=None,
                clarify=None,
            ),
            state=TurnState(
                session_id=session_id,
                state_version=state_version,
                system_state=self._system_state_str(),
                model_tier_effective=model_tier_effective,
            ),
            meta=TurnMeta(
                trace_id=trace_id,
                runtime_impl="engine-backed",
                latency_ms=round(latency_ms, 2),
                created_at=datetime.utcnow(),
                engine_ref=engine_ref,
            ),
        )

    def _system_state_str(self) -> str:
        if not self._orchestrator:
            return "IDLE"
        from mellow_link.core.states import SystemState
        s = self._orchestrator.get_state()
        return s.name if isinstance(s, SystemState) else "IDLE"

    async def status(self) -> StatusResponse:
        uptime_sec = 0.0
        if self._start_time:
            uptime_sec = (datetime.utcnow() - self._start_time).total_seconds()
        self._start_time = self._start_time or datetime.utcnow()
        system_state = self._system_state_str()
        last_error = None
        if self._orchestrator and hasattr(self._orchestrator, "_metrics"):
            me = self._orchestrator._metrics.get("last_error")
            last_error = me if isinstance(me, str) else None
        p95 = None
        if self._latencies_ms:
            sorted_ms = sorted(self._latencies_ms)
            idx = int(len(sorted_ms) * 0.95) - 1
            p95 = sorted_ms[max(0, idx)] if idx >= 0 else sorted_ms[0]
        return StatusResponse(
            runtime=StatusRuntime(impl="engine-backed", version="2026.03", uptime_sec=uptime_sec),
            health=StatusHealth(system_state=system_state, last_error=last_error, degraded=False),
            counters=StatusCounters(
                turns_1m=self._turns_1m,
                p95_latency_ms=round(p95, 2) if p95 is not None else None,
                error_rate_1m=0.0,
            ),
            time=datetime.utcnow(),
        )
