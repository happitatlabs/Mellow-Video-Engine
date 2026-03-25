"""
Runtime Adapter 프로토콜 및 구현체 진입점.

- RuntimeAdapter: turn 처리 + status 조회 (계약)
- Engine-backed: 2단 파이프라인(GM → Character Render)은 engine_backed_adapter.py
- trace_id: ingress에서 생성, 전 하위 호출에 전파
"""

import logging
from abc import ABC, abstractmethod
from typing import Optional

from .schemas import TurnRequest, TurnResponse, StatusResponse
from .engine_backed_adapter import EngineBackedAdapter, _new_trace_id, GMParseError

logger = logging.getLogger(__name__)


class RuntimeAdapter(ABC):
    """Chat Runtime 계약. 구현체: engine-backed / llm-only."""

    @abstractmethod
    async def turn(self, req: TurnRequest, trace_id: Optional[str] = None) -> TurnResponse:
        """1턴 처리. trace_id 없으면 ingress에서 생성한 값 전달."""
        ...

    @abstractmethod
    async def status(self) -> StatusResponse:
        """운영/디버그 상태."""
        ...


def get_runtime_adapter(impl: str = "engine-backed", orchestrator=None):
    """구현체 선택: engine-backed(2단 파이프라인) | llm-only."""
    if impl == "llm-only":
        from .llm_only import LLMOnlyAdapter
        return LLMOnlyAdapter()
    return EngineBackedAdapter(orchestrator=orchestrator)
