from __future__ import annotations

import threading

from mellow_link.infra.run_events import (
    EVENT_TYPE_LOG,
    EVENT_TYPE_PLAN_CREATED,
    EVENT_TYPE_RUN_FINISHED,
    EVENT_TYPE_RUN_STARTED,
    EVENT_TYPE_TODO_DONE,
    EVENT_TYPE_TODO_STARTED,
    emit_event,
)

from .service import SQLAnalyticsService


def start_sql_analytics_run(run_id: str, session_id: str | None, question: str, input_type: str = "natural_language") -> None:
    todos = [
        {"todo_id": "S1", "title": "질문 정규화", "status": "pending"},
        {"todo_id": "S2", "title": "SQL 템플릿 선택", "status": "pending"},
        {"todo_id": "S3", "title": "SQL 실행", "status": "pending"},
        {"todo_id": "S4", "title": "결과 요약", "status": "pending"},
    ]

    def _run() -> None:
        service = SQLAnalyticsService()
        try:
            emit_event(run_id, EVENT_TYPE_RUN_STARTED, {"user_input": question[:200], "mode": "module", "session_id": session_id})
            emit_event(run_id, EVENT_TYPE_PLAN_CREATED, {"todos": todos})
            emit_event(run_id, EVENT_TYPE_TODO_STARTED, todos[0])
            analysis = service.analyze_question(question=question, input_type=input_type)
            intent = analysis["intent"]
            supported = bool(analysis["supported"])
            emit_event(run_id, EVENT_TYPE_LOG, {"level": "info", "message": "sql question classified", "intent": intent})
            emit_event(run_id, EVENT_TYPE_TODO_DONE, {**todos[0], "detail": f"질문 의도를 {intent}로 분류했습니다."})

            emit_event(run_id, EVENT_TYPE_TODO_STARTED, todos[1])
            if supported:
                emit_event(run_id, EVENT_TYPE_TODO_DONE, {**todos[1], "detail": "지원 범위에 맞는 SQL 분석 경로를 선택했습니다."})
            else:
                emit_event(run_id, EVENT_TYPE_TODO_DONE, {**todos[1], "detail": "지원 범위를 벗어난 질문으로 판단해 안내형 결과를 준비합니다."})

            emit_event(run_id, EVENT_TYPE_TODO_STARTED, todos[2])
            if supported:
                emit_event(run_id, EVENT_TYPE_TODO_DONE, {**todos[2], "detail": "기존 SQL 분석 파이프라인을 실행했습니다."})
            else:
                emit_event(run_id, EVENT_TYPE_TODO_DONE, {**todos[2], "detail": "실제 SQL 리스크 판정 대신 지원 범위 안내를 준비했습니다."})

            emit_event(run_id, EVENT_TYPE_TODO_STARTED, todos[-1])
            summary = analysis["summary"]
            emit_event(run_id, EVENT_TYPE_TODO_DONE, {**todos[-1], "detail": "사용자용 분석 리포트를 정리했습니다."})
            emit_event(
                run_id,
                EVENT_TYPE_RUN_FINISHED,
                {
                    "success": True,
                    "summary": str(summary)[:1000],
                    "intent": intent,
                    "supported": supported,
                    "module_id": "sql_analytics",
                    "run_kind": "sql_analysis",
                },
            )
        except Exception as e:
            emit_event(
                run_id,
                EVENT_TYPE_RUN_FINISHED,
                {"success": False, "summary": f"SQL analytics failed: {str(e)[:300]}", "module_id": "sql_analytics", "run_kind": "sql_analysis"},
            )

    threading.Thread(target=_run, daemon=True).start()
