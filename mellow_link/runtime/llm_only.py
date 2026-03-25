"""
LLM-only Runtime 구현 골격.

- 최소 저장: chat_sessions, chat_messages, user_world_snapshot (문서 D)
- 경량 GM: intent/confidence 파싱
- confidence < threshold → turn.clarify 반환
- model_tier_requested → model_tier_effective 정책
"""

import logging
import time
import uuid
from datetime import datetime
from typing import Optional

from .schemas import (
    TurnRequest,
    TurnResponse,
    TurnPayload,
    TurnState,
    TurnMeta,
    ClarifyPayload,
    StatusResponse,
    StatusRuntime,
    StatusHealth,
    StatusCounters,
)

logger = logging.getLogger(__name__)


def _new_trace_id() -> str:
    return f"trc_{datetime.utcnow().strftime('%Y%m%d')}_{uuid.uuid4().hex[:8]}"


class LLMOnlyAdapter:
    """
    단순 LLM + 최소 상태 저장.
    - intent/confidence 산출 후 confidence < threshold면 clarify 반환
    - DB: session_id, state_version 일관성 유지
    """

    CLARIFY_THRESHOLD = 0.65  # confidence < 이면 clarify

    def __init__(self):
        self._start_time = datetime.utcnow()
        self._turns_1m = 0
        self._latencies_ms: list = []
        self._session_version: dict = {}  # session_id -> state_version (실제로는 DB)

    async def turn(self, req: TurnRequest, trace_id: Optional[str] = None) -> TurnResponse:
        trace_id = trace_id or _new_trace_id()
        start = time.perf_counter()
        session_id = req.session_id
        state_version = self._session_version.get(session_id, 0) + 1
        self._session_version[session_id] = state_version

        # 1) 입력 저장 (user message) - TODO: DB
        user_text = req.input.text

        # 2) 경량 GM: intent / confidence
        intent, confidence = await self._parse_intent_confidence(user_text)

        # 3) confidence < threshold → clarify 반환
        if confidence < self.CLARIFY_THRESHOLD:
            latency_ms = (time.perf_counter() - start) * 1000
            self._turns_1m += 1
            model_tier_effective = self._resolve_model_tier(req)
            return TurnResponse(
                turn=TurnPayload(
                    id=f"turn_{uuid.uuid4().hex[:8]}",
                    speech="",
                    passage=None,
                    ooc=None,
                    actions=None,
                    clarify=ClarifyPayload(
                        question="의도를 확인해 주세요.",
                        options=[],
                        reason="intent_confidence_low",
                    ),
                ),
                state=TurnState(
                    session_id=session_id,
                    state_version=state_version,
                    system_state="IDLE",
                    model_tier_effective=model_tier_effective,
                ),
                meta=TurnMeta(
                    trace_id=trace_id,
                    runtime_impl="llm-only",
                    latency_ms=round(latency_ms, 2),
                    created_at=datetime.utcnow(),
                    engine_ref=None,
                ),
            )

        # 4) LLM 호출 (최근 N턴 + world snapshot) - TODO: 실제 LLM
        speech = await self._call_llm(session_id, user_text)

        # 5) assistant message 저장, state_version 반영 (위에서 이미 +1)
        latency_ms = (time.perf_counter() - start) * 1000
        self._turns_1m += 1
        self._latencies_ms.append(latency_ms)
        if len(self._latencies_ms) > 1000:
            self._latencies_ms = self._latencies_ms[-500:]

        model_tier_effective = self._resolve_model_tier(req)

        return TurnResponse(
            turn=TurnPayload(
                id=f"turn_{uuid.uuid4().hex[:8]}",
                speech=speech,
                passage=None,
                ooc=None,
                actions=None,
                clarify=None,
            ),
            state=TurnState(
                session_id=session_id,
                state_version=state_version,
                system_state="IDLE",
                model_tier_effective=model_tier_effective,
            ),
            meta=TurnMeta(
                trace_id=trace_id,
                runtime_impl="llm-only",
                latency_ms=round(latency_ms, 2),
                created_at=datetime.utcnow(),
                engine_ref=None,
            ),
        )

    async def _parse_intent_confidence(self, text: str):
        """의도/확신도. 예: smalltalk, character_lore, request_action, unsafe, unknown."""
        # TODO: 경량 분류기 또는 프롬프트
        return "smalltalk", 0.9

    async def _call_llm(self, session_id: str, user_text: str) -> str:
        """최근 N턴 + world snapshot 포함 LLM 호출. TODO: 실제 연동."""
        return f"[LLM-only] 응답 placeholder: {user_text[:50]}"

    def _resolve_model_tier(self, req: TurnRequest) -> str:
        """model_tier_requested → model_tier_effective (권한/쿼터)."""
        requested = None
        if req.context and req.context.model_tier_requested:
            requested = req.context.model_tier_requested
        if requested == "pro":
            # TODO: 권한/쿼터 검사
            return "free"  # 미충족 시 다운그레이드
        return requested or "free"

    async def status(self) -> StatusResponse:
        uptime_sec = (datetime.utcnow() - self._start_time).total_seconds()
        p95 = None
        if self._latencies_ms:
            sorted_ms = sorted(self._latencies_ms)
            idx = int(len(sorted_ms) * 0.95) - 1
            p95 = sorted_ms[max(0, idx)] if idx >= 0 else sorted_ms[0]

        return StatusResponse(
            runtime=StatusRuntime(
                impl="llm-only",
                version="2026.03",
                uptime_sec=uptime_sec,
            ),
            health=StatusHealth(
                system_state="IDLE",
                last_error=None,
                degraded=False,
            ),
            counters=StatusCounters(
                turns_1m=self._turns_1m,
                p95_latency_ms=round(p95, 2) if p95 is not None else None,
                error_rate_1m=0.0,
            ),
            time=datetime.utcnow(),
        )
