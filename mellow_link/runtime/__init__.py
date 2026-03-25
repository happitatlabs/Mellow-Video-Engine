"""
Chat Runtime API 구현.

- 앱 계약: POST /runtime/turn, GET /runtime/status
- Engine-backed: Mellow Engine(Orchestrator/GM) 호출 후 응답 정규화
- LLM-only: 최소 DB + intent/confidence + clarify
"""

from .schemas import (
    TurnRequest,
    TurnResponse,
    TurnPayload,
    TurnState,
    TurnMeta,
    ClarifyPayload,
    GMResult,
    GMSpeaker,
    GMClarify,
    StatusResponse,
    ErrorBody,
    ErrorDetail,
)
from .adapter import RuntimeAdapter, get_runtime_adapter
from .engine_backed_adapter import GMParseError

__all__ = [
    "TurnRequest",
    "TurnResponse",
    "TurnPayload",
    "TurnState",
    "TurnMeta",
    "ClarifyPayload",
    "GMResult",
    "GMSpeaker",
    "GMClarify",
    "StatusResponse",
    "ErrorBody",
    "ErrorDetail",
    "GMParseError",
    "RuntimeAdapter",
    "get_runtime_adapter",
]
