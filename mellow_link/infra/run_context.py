"""
Run 실행 컨텍스트: 현재 run_id / current_todo_id (Guardian NEED_AI_REVIEW 시 승인 대기용).

AgentBrain에서 설정, ToolForge 등에서 읽음.
"""
from contextvars import ContextVar
from typing import Optional

_run_id_ctx: ContextVar[Optional[str]] = ContextVar("run_id", default=None)
_current_todo_id_ctx: ContextVar[Optional[str]] = ContextVar("current_todo_id", default=None)


def set_run_context(run_id: Optional[str], current_todo_id: Optional[str]) -> None:
    """현재 실행 컨텍스트 설정 (AgentBrain에서 호출)."""
    _run_id_ctx.set(run_id)
    _current_todo_id_ctx.set(current_todo_id)


def get_run_id() -> Optional[str]:
    """현재 run_id (없으면 None)."""
    return _run_id_ctx.get()


def get_current_todo_id() -> Optional[str]:
    """현재 todo_id (없으면 None)."""
    return _current_todo_id_ctx.get()
