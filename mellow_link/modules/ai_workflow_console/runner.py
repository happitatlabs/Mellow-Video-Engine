from __future__ import annotations

import threading
import time

from mellow_link.infra.run_events import (
    EVENT_TYPE_PLAN_CREATED,
    EVENT_TYPE_RUN_FINISHED,
    EVENT_TYPE_RUN_STARTED,
    EVENT_TYPE_TODO_DONE,
    EVENT_TYPE_TODO_STARTED,
    emit_event,
)

from .service import AIWorkflowService


def start_ai_workflow_run(run_id: str, session_id: str | None, task_type: str, prompt: str) -> None:
    todos = [
        {"todo_id": "W1", "title": "작업 큐 적재", "status": "pending"},
        {"todo_id": "W2", "title": "생성 실행", "status": "pending"},
        {"todo_id": "W3", "title": "결과 저장", "status": "pending"},
    ]

    def _run() -> None:
        svc = AIWorkflowService()
        try:
            emit_event(run_id, EVENT_TYPE_RUN_STARTED, {"user_input": prompt[:200], "mode": task_type, "session_id": session_id})
            emit_event(run_id, EVENT_TYPE_PLAN_CREATED, {"todos": todos})
            for todo in todos:
                emit_event(run_id, EVENT_TYPE_TODO_STARTED, todo)
                time.sleep(0.05)
                emit_event(run_id, EVENT_TYPE_TODO_DONE, todo)
            emit_event(
                run_id,
                EVENT_TYPE_RUN_FINISHED,
                {"success": True, "summary": svc.build_summary(task_type, prompt), "module_id": "ai_workflow_console", "run_kind": "workflow_run"},
            )
        except Exception as e:
            emit_event(run_id, EVENT_TYPE_RUN_FINISHED, {"success": False, "summary": f"Workflow run failed: {str(e)[:300]}", "module_id": "ai_workflow_console", "run_kind": "workflow_run"})

    threading.Thread(target=_run, daemon=True).start()
