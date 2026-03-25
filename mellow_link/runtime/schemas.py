"""
Chat Runtime API - Pydantic 스키마 (OpenAPI 스펙과 1:1).

- system_state: IDLE | TEXT | IMAGE | ERROR (FSM 기반)
- actions: nullable (챗봇 범위 혼선 방지)
"""

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# ----- Request -----

class TurnRequestUser(BaseModel):
    id: str


class TurnRequestInput(BaseModel):
    text: str
    locale: Optional[str] = None
    channel: Optional[str] = None


class TurnRequestContext(BaseModel):
    character_id: Optional[str] = None
    model_tier_requested: Optional[str] = None  # free | pro | auto
    client_turn_id: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


class TurnRequest(BaseModel):
    session_id: str
    user: TurnRequestUser
    input: TurnRequestInput
    context: Optional[TurnRequestContext] = None


# ----- GM 단계 결과 (2단 파이프라인 1단) -----

class GMSpeaker(BaseModel):
    id: Optional[str] = None
    name: Optional[str] = None


class GMClarify(BaseModel):
    question: Optional[str] = None
    options: Optional[List[str]] = None


class GMResult(BaseModel):
    """GM(Decision) 단계 출력. JSON만 반환, speech 미생성."""
    speaker: Optional[GMSpeaker] = None
    intent: str = "OPEN"  # OPEN|TALK|LOOK|SMALLTALK|...
    confidence: float = 0.0
    slots: Optional[Dict[str, Any]] = None
    state_summary: Optional[str] = None
    observation: Optional[Dict[str, Any]] = None
    user_action: Optional[str] = None  # 유저 행동 추출 (예: *나는 손으로 문을 가리켰다.*)
    needs_clarify: bool = False
    clarify: Optional[GMClarify] = None


# ----- Response (정규화 포맷) -----

class ClarifyPayload(BaseModel):
    question: Optional[str] = None
    options: Optional[List[str]] = None
    reason: Optional[str] = None  # e.g. intent_confidence_low


class TurnPayload(BaseModel):
    id: str
    speech: str = ""
    passage: Optional[str] = None
    ooc: Optional[str] = None
    actions: Optional[List[Any]] = None  # nullable
    clarify: Optional[ClarifyPayload] = None


class TurnState(BaseModel):
    session_id: str
    state_version: int
    system_state: str = "IDLE"  # IDLE | TEXT | IMAGE | ERROR
    model_tier_effective: str = "free"  # free | pro


class TurnMeta(BaseModel):
    trace_id: str
    runtime_impl: str  # engine-backed | llm-only
    latency_ms: Optional[float] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    engine_ref: Optional[str] = None  # Engine 내부 ID optional 노출


class TurnResponse(BaseModel):
    turn: TurnPayload
    state: TurnState
    meta: TurnMeta


# ----- GET /runtime/status -----

class StatusRuntime(BaseModel):
    impl: str
    version: str
    uptime_sec: float


class StatusHealth(BaseModel):
    system_state: str  # IDLE | TEXT | IMAGE | ERROR
    last_error: Optional[str] = None
    degraded: bool = False


class StatusCounters(BaseModel):
    turns_1m: Optional[int] = None
    p95_latency_ms: Optional[float] = None
    error_rate_1m: Optional[float] = None


class StatusResponse(BaseModel):
    runtime: StatusRuntime
    health: StatusHealth
    counters: Optional[StatusCounters] = None
    time: datetime = Field(default_factory=datetime.utcnow)


# ----- 에러 바디 -----

class ErrorDetail(BaseModel):
    code: str
    message: str
    trace_id: Optional[str] = None


class ErrorBody(BaseModel):
    error: ErrorDetail
