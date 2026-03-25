"""
Chat Runtime API 라우터.

- POST /runtime/turn: 유저 1턴 처리 (trace_id 생성/전파)
- GET /runtime/status: 운영/디버그 상태 (system_state = IDLE|TEXT|IMAGE|ERROR)

엔진 내부(FSM/GM/Tools) 노출 없음. Engine 내부 ID는 meta.engine_ref optional만.
"""

import logging
from typing import Optional

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from mellow_link.runtime import (
    TurnRequest,
    TurnResponse,
    StatusResponse,
    ErrorBody,
    ErrorDetail,
    get_runtime_adapter,
)
from mellow_link.runtime.adapter import _new_trace_id
from mellow_link.runtime.engine_backed_adapter import GMParseError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/runtime", tags=["Runtime"])


def _get_adapter():
    """Runtime 구현체 주입. 설정에 따라 engine-backed | llm-only."""
    from mellow_link import app_state
    impl = getattr(app_state, "runtime_impl", "engine-backed")
    orch = getattr(app_state, "orchestrator", None)
    return get_runtime_adapter(impl=impl, orchestrator=orch)


@router.post("/turn", response_model=TurnResponse)
async def runtime_turn(req: TurnRequest):
    """
    유저 자유 입력 1턴 처리.
    trace_id는 ingress에서 생성해 adapter에 전파.
    """
    trace_id = _new_trace_id()
    adapter = _get_adapter()
    try:
        return await adapter.turn(req, trace_id=trace_id)
    except GMParseError as e:
        logger.exception("Runtime turn GM parse failed: %s", e)
        return JSONResponse(
            status_code=500,
            content=ErrorBody(error=ErrorDetail(
                code="INTERNAL_ERROR",
                message=f"GM JSON parse failed: {e}",
                trace_id=trace_id,
            )).model_dump(),
        )
    except Exception as e:
        logger.exception("Runtime turn failed: %s", e)
        return JSONResponse(
            status_code=503,
            content=ErrorBody(error=ErrorDetail(
                code="SERVICE_UNAVAILABLE",
                message=str(e),
                trace_id=trace_id,
            )).model_dump(),
        )


@router.get("/status", response_model=StatusResponse)
async def runtime_status():
    """운영자/디버그 최소 읽기 상태."""
    adapter = _get_adapter()
    return await adapter.status()
