"""
Agent Run Events - Progress tracking and event emission

이벤트 발행 및 SSE 스트리밍을 위한 헬퍼 함수들.
"""
import json
import logging
import re
import time
import uuid
from datetime import datetime
from typing import Optional, Dict, Any, List
from pathlib import Path

from sqlalchemy.orm import Session
from sqlalchemy import desc

from mellow_link.infra.database import AgentRun, AgentRunEvent, get_db, SessionLocal

logger = logging.getLogger(__name__)

# 이벤트 타입 정의
EVENT_TYPE_RUN_STARTED = "run_started"
EVENT_TYPE_PLAN_CREATED = "plan_created"
EVENT_TYPE_TODO_STARTED = "todo_started"
EVENT_TYPE_TODO_DONE = "todo_done"
EVENT_TYPE_TOOL_STARTED = "tool_started"
EVENT_TYPE_TOOL_DONE = "tool_done"
EVENT_TYPE_LOG = "log"
EVENT_TYPE_RUN_FINISHED = "run_finished"
EVENT_TYPE_ERROR = "error"

# block(차단/대기) 판정용 상수 (초)
STUCK_THRESHOLD_SEC = 30
DISCONNECTED_THRESHOLD_SEC = 60

# 페이로드 크기 제한 (8KB)
MAX_PAYLOAD_SIZE = 8192

# 공통 민감정보 마스킹 (KEY/SECRET/TOKEN/BEARER/Authorization/OPENAI/ANTHROPIC/GOOGLE)
from mellow_link.utils.sensitive_redact import redact_sensitive_data as _redact_keys


def _redact_dict_recursive(obj: Any) -> Any:
    """딕셔너리를 재귀 순회하며 문자열에 redact_sensitive_data 적용."""
    if isinstance(obj, dict):
        return {k: _redact_dict_recursive(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_redact_dict_recursive(item) for item in obj]
    if isinstance(obj, str):
        return redact_sensitive_data(obj)
    return obj


def redact_sensitive_data(text: str) -> str:
    """
    민감한 정보 제거/마스킹.
    - KEY/SECRET/TOKEN/BEARER/Authorization/OPENAI/ANTHROPIC/GOOGLE (공통 모듈)
    - 워크스페이스 외부 절대 경로 마스킹
    """
    if not text:
        return text
    result = _redact_keys(text)
    try:
        from mellow_link.config import get_settings
        settings = get_settings()
        workspace_str = str(getattr(settings, "project_root", None) or Path(__file__).resolve().parent.parent.parent)
    except Exception:
        workspace_str = str(Path(__file__).resolve().parent.parent.parent)

    def mask_path(match):
        path_str = match.group(0)
        if workspace_str.lower() in path_str.lower():
            return path_str
        parts = Path(path_str).parts
        if len(parts) > 2:
            return f"{parts[0]}/.../{parts[-1]}"
        return "[REDACTED_PATH]"

    result = re.sub(r'[A-Z]:\\[^\s<>"\'|]+|/(?![\/h])(?![^\s<>"\'|]*://)[^\s<>"\'|]+', mask_path, result)
    return result


def truncate_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    페이로드 크기를 제한하고 긴 필드는 잘라냄.
    """
    payload_str = json.dumps(payload, ensure_ascii=False)
    
    if len(payload_str) <= MAX_PAYLOAD_SIZE:
        return payload
    
    # 크기 초과 시 긴 필드 자르기
    truncated = payload.copy()
    for key, value in truncated.items():
        if isinstance(value, str) and len(value) > 500:
            truncated[key] = value[:500] + "[TRUNCATED]"
        elif isinstance(value, (dict, list)):
            value_str = json.dumps(value, ensure_ascii=False)
            if len(value_str) > 500:
                truncated[key] = "[TRUNCATED_OBJECT]"
    
    # 여전히 크면 전체 메시지 자르기
    final_str = json.dumps(truncated, ensure_ascii=False)
    if len(final_str) > MAX_PAYLOAD_SIZE:
        return {"message": truncated.get("message", "")[:MAX_PAYLOAD_SIZE - 50] + "[TRUNCATED]"}
    
    return truncated


def emit_event(
    run_id: str,
    event_type: str,
    payload: Dict[str, Any],
    db: Optional[Session] = None
) -> bool:
    """
    이벤트를 발행하고 DB에 저장.
    
    Args:
        run_id: 실행 ID
        event_type: 이벤트 타입
        payload: 페이로드 딕셔너리
        db: DB 세션 (None이면 새로 생성)
    
    Returns:
        성공 여부
    """
    try:
        # Redaction 및 크기 제한
        # 딕셔너리를 직접 순회하여 효율성 개선
        payload_redacted = _redact_dict_recursive(payload)
        payload_final = truncate_payload(payload_redacted)
        
        # DB에 저장
        if db is None:
            db = SessionLocal()
            should_close = True
        else:
            should_close = False
        
        try:
            event = AgentRunEvent(
                run_id=run_id,
                ts=time.time(),
                type=event_type,
                payload_json=json.dumps(payload_final, ensure_ascii=False)
            )
            db.add(event)
            
            # Run 상태 업데이트
            run = db.query(AgentRun).filter(AgentRun.run_id == run_id).first()
            if run:
                if event_type == EVENT_TYPE_RUN_STARTED:
                    run.status = "running"
                elif event_type == EVENT_TYPE_RUN_FINISHED:
                    run.status = "completed" if payload_final.get("success", True) else "failed"
                    run.summary = payload_final.get("summary", "")[:4000]  # 사용자 콘솔 표시용 요약은 더 길게 유지
                elif event_type == EVENT_TYPE_ERROR:
                    run.status = "failed"
                run.updated_at = datetime.utcnow()
            
            db.commit()
            return True
        except Exception as inner_e:
            if should_close and db:
                db.rollback()
            raise inner_e
        finally:
            if should_close:
                db.close()
    except Exception as e:
        logger.error(f"[RunEvents] Failed to emit event: {e}", exc_info=True)
        return False


def create_run(
    session_id: Optional[str] = None,
    db: Optional[Session] = None,
    *,
    module_id: str = "engine",
    run_kind: str = "generic",
) -> str:
    """
    새로운 실행을 생성하고 run_id를 반환.
    
    Args:
        session_id: 세션 ID (선택사항)
        db: DB 세션 (None이면 새로 생성)
    
    Returns:
        run_id
    """
    run_id = f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
    
    if db is None:
        db = SessionLocal()
        should_close = True
    else:
        should_close = False
    
    try:
        run = AgentRun(
            run_id=run_id,
            session_id=session_id,
            module_id=(module_id or "engine").strip() or "engine",
            run_kind=(run_kind or "generic").strip() or "generic",
            status="pending"
        )
        db.add(run)
        db.commit()
        return run_id
    finally:
        if should_close:
            db.close()


def get_run_events(
    run_id: str,
    since_ts: Optional[float] = None,
    since_event_id: Optional[int] = None,
    db: Optional[Session] = None
) -> List[Dict[str, Any]]:
    """
    실행 이벤트를 조회.
    
    Args:
        run_id: 실행 ID
        since_ts: 이 타임스탬프 이후의 이벤트만 조회
        since_event_id: 이 이벤트 ID 이후의 이벤트만 조회
        db: DB 세션 (None이면 새로 생성)
    
    Returns:
        이벤트 리스트
    """
    if db is None:
        db = SessionLocal()
        should_close = True
    else:
        should_close = False
    
    try:
        query = db.query(AgentRunEvent).filter(AgentRunEvent.run_id == run_id)
        
        if since_event_id:
            query = query.filter(AgentRunEvent.id > since_event_id)
        elif since_ts:
            query = query.filter(AgentRunEvent.ts > since_ts)
        
        events = query.order_by(AgentRunEvent.ts.asc()).all()
        
        result = []
        for event in events:
            try:
                payload = json.loads(event.payload_json) if event.payload_json else {}
            except json.JSONDecodeError:
                logger.warning(f"[RunEvents] Failed to parse payload JSON for event {event.id}")
                payload = {}
            
            result.append({
                "id": event.id,
                "run_id": event.run_id,
                "ts": event.ts,
                "type": event.type,
                "payload": payload
            })
        
        return result
    except json.JSONDecodeError as e:
        logger.warning(f"[RunEvents] Failed to parse payload JSON: {e}")
        return []
    finally:
        if should_close:
            db.close()


USER_STAGE_STATUS_PENDING = "pending"
USER_STAGE_STATUS_IN_PROGRESS = "in_progress"
USER_STAGE_STATUS_COMPLETED = "completed"
USER_STAGE_STATUS_SKIPPED = "skipped"
USER_STAGE_STATUS_ABORTED = "aborted"

NORMALIZED_STAGE_DEFS = [
    {"id": "V1", "title": "준비"},
    {"id": "V2", "title": "처리"},
    {"id": "V3", "title": "완료"},
]

MODULE_TODO_VIEW_REGISTRY = {
    "engine": [
        {"id": "V1", "title": "준비", "raw_todo_ids": ["T1", "T2"]},
        {"id": "V2", "title": "처리", "raw_todo_ids": ["T3", "T4"]},
        {"id": "V3", "title": "완료", "raw_todo_ids": ["T5", "T6", "T7"]},
    ],
    "research_assistant": [
        {"id": "V1", "title": "준비", "raw_todo_ids": ["R1", "R2"]},
        {"id": "V2", "title": "처리", "raw_todo_ids": ["R3"]},
        {"id": "V3", "title": "완료", "raw_todo_ids": ["R4"]},
    ],
    "rebuild_assistant": [
        {"id": "V1", "title": "준비", "raw_todo_ids": ["B1", "B2"]},
        {"id": "V2", "title": "처리", "raw_todo_ids": ["B3", "B4"]},
        {"id": "V3", "title": "완료", "raw_todo_ids": ["B5"]},
    ],
}


def _todo_status_from_events(events: List[Dict[str, Any]]) -> Dict[str, str]:
    """이벤트 목록에서 raw todo_id별 최종 상태를 계산."""
    status_by_id: Dict[str, str] = {}
    for event in events:
        payload = event.get("payload") or {}
        tid = payload.get("todo_id")
        if tid is None:
            continue
        tid = str(tid).strip()
        if not tid:
            continue
        if event.get("type") == EVENT_TYPE_TODO_STARTED:
            status_by_id[tid] = USER_STAGE_STATUS_IN_PROGRESS
        elif event.get("type") == EVENT_TYPE_TODO_DONE:
            status_by_id[tid] = USER_STAGE_STATUS_COMPLETED
    return status_by_id


def _extract_ordered_raw_todo_ids(raw_todos: List[Dict[str, Any]], events: List[Dict[str, Any]]) -> List[str]:
    ordered_ids: List[str] = []
    for todo in raw_todos:
        tid = str(todo.get("todo_id") or todo.get("id") or "").strip()
        if tid and tid not in ordered_ids:
            ordered_ids.append(tid)
    for event in events:
        payload = event.get("payload") or {}
        tid = str(payload.get("todo_id") or "").strip()
        if tid and tid not in ordered_ids:
            ordered_ids.append(tid)
    return ordered_ids


def _build_final_raw_statuses(
    raw_todos: List[Dict[str, Any]],
    events: List[Dict[str, Any]],
    run_status: Optional[str] = None,
) -> Dict[str, str]:
    status_by_id = _todo_status_from_events(events)
    run_status = (run_status or "").strip().lower()
    ordered_ids = _extract_ordered_raw_todo_ids(raw_todos, events)
    if run_status in ("completed", "failed"):
        close_status = USER_STAGE_STATUS_SKIPPED if run_status == "completed" else USER_STAGE_STATUS_ABORTED
        for tid in ordered_ids:
            if status_by_id.get(tid) != USER_STAGE_STATUS_COMPLETED:
                status_by_id[tid] = close_status
    return status_by_id


def _build_fallback_stage_mapping(raw_todo_ids: List[str]) -> List[Dict[str, Any]]:
    total = len(raw_todo_ids)
    if total == 0:
        return [
            {"id": "V1", "title": "준비", "raw_todo_ids": []},
            {"id": "V2", "title": "처리", "raw_todo_ids": []},
            {"id": "V3", "title": "완료", "raw_todo_ids": []},
        ]
    if total == 1:
        return [
            {"id": "V1", "title": "준비", "raw_todo_ids": []},
            {"id": "V2", "title": "처리", "raw_todo_ids": [raw_todo_ids[0]]},
            {"id": "V3", "title": "완료", "raw_todo_ids": []},
        ]
    if total == 2:
        return [
            {"id": "V1", "title": "준비", "raw_todo_ids": [raw_todo_ids[0]]},
            {"id": "V2", "title": "처리", "raw_todo_ids": [raw_todo_ids[1]]},
            {"id": "V3", "title": "완료", "raw_todo_ids": []},
        ]

    first_cut = max(1, total // 3)
    second_cut = max(first_cut + 1, (2 * total) // 3)
    second_cut = min(second_cut, total - 1)
    return [
        {"id": "V1", "title": "준비", "raw_todo_ids": raw_todo_ids[:first_cut]},
        {"id": "V2", "title": "처리", "raw_todo_ids": raw_todo_ids[first_cut:second_cut]},
        {"id": "V3", "title": "완료", "raw_todo_ids": raw_todo_ids[second_cut:]},
    ]


def _get_normalized_stage_mapping(module_id: str, raw_todos: List[Dict[str, Any]], events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    module_key = (module_id or "engine").strip() or "engine"
    mapping = MODULE_TODO_VIEW_REGISTRY.get(module_key)
    if mapping:
        return [
            {"id": stage["id"], "title": stage["title"], "raw_todo_ids": list(stage.get("raw_todo_ids", []))}
            for stage in mapping
        ]
    return _build_fallback_stage_mapping(_extract_ordered_raw_todo_ids(raw_todos, events))


def _derive_empty_stage_status(stage_id: str, run_status: str, has_any_work: bool, fallback_todo_count: int) -> str:
    if fallback_todo_count == 0:
        if run_status == "completed":
            return USER_STAGE_STATUS_COMPLETED
        if run_status == "failed":
            return USER_STAGE_STATUS_ABORTED if stage_id == "V1" else USER_STAGE_STATUS_PENDING
        if run_status == "running":
            return USER_STAGE_STATUS_IN_PROGRESS if stage_id == "V1" else USER_STAGE_STATUS_PENDING
        return USER_STAGE_STATUS_PENDING
    if fallback_todo_count == 1:
        if stage_id == "V1":
            return USER_STAGE_STATUS_COMPLETED if has_any_work or run_status in ("completed", "failed") else USER_STAGE_STATUS_PENDING
        if stage_id == "V3":
            if run_status == "completed":
                return USER_STAGE_STATUS_COMPLETED
            if run_status == "failed":
                return USER_STAGE_STATUS_ABORTED
            return USER_STAGE_STATUS_PENDING
    if fallback_todo_count == 2 and stage_id == "V3":
        if run_status == "completed":
            return USER_STAGE_STATUS_COMPLETED
        if run_status == "failed":
            return USER_STAGE_STATUS_ABORTED
        return USER_STAGE_STATUS_PENDING
    if run_status == "completed":
        return USER_STAGE_STATUS_COMPLETED
    return USER_STAGE_STATUS_PENDING


def _derive_normalized_stage_status(raw_statuses: List[str], run_status: str) -> str:
    if any(status == USER_STAGE_STATUS_ABORTED for status in raw_statuses):
        return USER_STAGE_STATUS_ABORTED
    if raw_statuses and all(status == USER_STAGE_STATUS_COMPLETED for status in raw_statuses):
        return USER_STAGE_STATUS_COMPLETED
    if any(status == USER_STAGE_STATUS_IN_PROGRESS for status in raw_statuses):
        return USER_STAGE_STATUS_IN_PROGRESS
    if raw_statuses and all(status == USER_STAGE_STATUS_SKIPPED for status in raw_statuses) and run_status == "completed":
        return USER_STAGE_STATUS_SKIPPED
    return USER_STAGE_STATUS_PENDING


def _compute_normalized_progress_percent(stages: List[Dict[str, Any]], run_status: str) -> int:
    weights = {
        USER_STAGE_STATUS_COMPLETED: 1.0,
        USER_STAGE_STATUS_IN_PROGRESS: 0.5,
        USER_STAGE_STATUS_PENDING: 0.0,
        USER_STAGE_STATUS_ABORTED: 0.0,
        USER_STAGE_STATUS_SKIPPED: 1.0 if run_status == "completed" else 0.0,
    }
    if not stages:
        return 0
    average_weight = sum(weights.get((stage.get("status") or USER_STAGE_STATUS_PENDING), 0.0) for stage in stages) / len(stages)
    return round(average_weight * 100)


def build_todos_view(
    module_id: str,
    raw_todos: List[Dict[str, Any]],
    current_todo_id: Optional[str],
    events: List[Dict[str, Any]],
    run_status: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Raw todos(7단계)를 사용자용 3단계 축약 뷰(todos_view)로 변환.
    DB/스키마 변경 없이 조회 시점에만 계산.
    run_status가 completed/failed이면 미완(in_progress, pending) todo를 skipped(성공) 또는 aborted(실패)로 마감.
    실제 완료된 건 completed, run_finished로만 마감된 건 skipped로 구분해 로그/통계 오해 방지.
    """
    run_status = (run_status or "").strip().lower()
    current = str(current_todo_id).strip() if current_todo_id else None
    raw_status_by_id = _build_final_raw_statuses(raw_todos, events, run_status=run_status)
    mapping = _get_normalized_stage_mapping(module_id, raw_todos, events)
    fallback_todo_count = len(_extract_ordered_raw_todo_ids(raw_todos, events)) if (module_id or "engine") not in MODULE_TODO_VIEW_REGISTRY else -1
    has_any_work = bool(events)
    view = []

    for stage in mapping:
        raw_ids = [str(tid).strip() for tid in stage.get("raw_todo_ids", []) if str(tid).strip()]
        if raw_ids:
            raw_statuses = [raw_status_by_id.get(tid, USER_STAGE_STATUS_PENDING) for tid in raw_ids]
            if current and current in raw_ids and USER_STAGE_STATUS_IN_PROGRESS not in raw_statuses and USER_STAGE_STATUS_COMPLETED not in raw_statuses:
                raw_statuses.append(USER_STAGE_STATUS_IN_PROGRESS)
            group_status = _derive_normalized_stage_status(raw_statuses, run_status)
        else:
            group_status = _derive_empty_stage_status(stage["id"], run_status, has_any_work, fallback_todo_count)

        view.append({
            "id": stage["id"],
            "title": stage["title"],
            "status": group_status,
            "raw_todo_ids": raw_ids,
        })

    return view


def build_block_status(
    run_id: str,
    run_status: Optional[str],
    events: List[Dict[str, Any]],
    current_todo_id: Optional[str],
    paused: Optional[bool] = None,
) -> Dict[str, Any]:
    """
    이벤트/상태로 차단(block) 원인을 계산. DB 미사용, 조회 시점에만 계산.
    """
    now = time.time()
    run_status = (run_status or "").strip().lower()
    empty_block = {
        "is_blocked": False,
        "reason_code": "none",
        "message": "",
        "since_ts": None,
        "related": {},
    }

    if run_status in ("completed", "failed"):
        return empty_block

    if paused is True:
        return {
            "is_blocked": True,
            "reason_code": "paused",
            "message": "일시정지 상태입니다.",
            "since_ts": None,
            "related": {},
        }

    # approval_required: PolicyGuardian NEED_AI_REVIEW 시 Operator 승인 대기
    for ev in reversed(events):
        if ev.get("type") == "approval_required":
            payload = ev.get("payload") or {}
            related = {
                "event_id": ev.get("id"),
                "event_type": ev.get("type"),
                "audit_type": payload.get("audit_type"),
                "todo_id": payload.get("todo_id"),
                "file_path": payload.get("file_path"),
                "critique": payload.get("critique"),
                "risk_level": payload.get("risk_level"),
                "risk_score": payload.get("risk_score"),
            }
            return {
                "is_blocked": True,
                "reason_code": "approval_required",
                "message": "승인이 필요합니다.",
                "since_ts": ev.get("ts"),
                "related": related,
            }
        if ev.get("type") == EVENT_TYPE_LOG:
            payload = ev.get("payload") or {}
            msg = (payload.get("message") or payload.get("text") or "").upper()
            if "APPROVAL_REQUIRED" in msg or (payload.get("level") or "").lower() == "approval":
                return {
                    "is_blocked": True,
                    "reason_code": "approval_required",
                    "message": "승인이 필요합니다.",
                    "since_ts": ev.get("ts"),
                    "related": {"event_id": ev.get("id"), "event_type": ev.get("type")},
                }

    # tool_error: 마지막 tool_done success=false 또는 log/error 이벤트
    for ev in reversed(events):
        if ev.get("type") == EVENT_TYPE_TOOL_DONE:
            payload = ev.get("payload") or {}
            if payload.get("success") is False:
                tool_name = payload.get("tool_name") or ""
                msg = (payload.get("message") or payload.get("error") or "도구 실행 실패").strip()
                if len(msg) > 80:
                    msg = msg[:77] + "..."
                related = {
                    "tool_name": tool_name or None,
                    "event_id": ev.get("id"),
                    "event_type": EVENT_TYPE_TOOL_DONE,
                }
                if payload.get("todo_id") is not None:
                    related["todo_id"] = payload.get("todo_id")
                return {
                    "is_blocked": True,
                    "reason_code": "tool_error",
                    "message": "도구 실행 실패: " + (tool_name + " (" + msg + ")" if tool_name else msg).strip(" :"),
                    "since_ts": ev.get("ts"),
                    "related": related,
                }
        if ev.get("type") == EVENT_TYPE_ERROR:
            payload = ev.get("payload") or {}
            msg = (payload.get("message") or "오류").strip()
            if len(msg) > 80:
                msg = msg[:77] + "..."
            return {
                "is_blocked": True,
                "reason_code": "tool_error",
                "message": "도구 실행 실패: " + msg,
                "since_ts": ev.get("ts"),
                "related": {"event_id": ev.get("id"), "event_type": EVENT_TYPE_ERROR},
            }
        if ev.get("type") == EVENT_TYPE_LOG:
            payload = ev.get("payload") or {}
            lvl = (payload.get("level") or "").lower()
            msg = str(payload.get("message") or payload.get("text") or "")
            if lvl == "error" or "[ERROR]" in msg or "[Error]" in msg:
                return {
                    "is_blocked": True,
                    "reason_code": "tool_error",
                    "message": "도구 실행 실패: " + (msg[:80] if len(msg) > 80 else msg) or "로그 오류",
                    "since_ts": ev.get("ts"),
                    "related": {"event_id": ev.get("id"), "event_type": EVENT_TYPE_LOG},
                }

    # stuck: running 전용. last_event_at 기준 N초 이상 무진행 + inflight_todo 존재
    if run_status == "running" and current_todo_id and events:
        last_ts = events[-1].get("ts")
        if last_ts is not None and (now - last_ts) >= STUCK_THRESHOLD_SEC:
            return {
                "is_blocked": True,
                "reason_code": "stuck",
                "message": "진행이 지연되고 있습니다: 마지막 이벤트 후 %d초 이상 무진행" % STUCK_THRESHOLD_SEC,
                "since_ts": last_ts,
                "related": {"todo_id": current_todo_id},
            }

    # disconnected: running인데 마지막 이벤트가 60초 이상 오래됨
    if run_status == "running" and events:
        last_ts = events[-1].get("ts")
        if last_ts is not None and (now - last_ts) >= DISCONNECTED_THRESHOLD_SEC:
            return {
                "is_blocked": True,
                "reason_code": "disconnected",
                "message": "이벤트 수신이 지연되고 있습니다(마지막 이벤트가 오래됨)",
                "since_ts": last_ts,
                "related": {"last_event_id": events[-1].get("id")},
            }

    return empty_block


def get_run_snapshot(run_id: str, db: Optional[Session] = None, paused: Optional[bool] = None) -> Optional[Dict[str, Any]]:
    """
    실행 스냅샷을 조회 (todos + 최근 이벤트).
    
    Args:
        run_id: 실행 ID
        db: DB 세션
    
    Returns:
        스냅샷 딕셔너리 또는 None
    """
    if db is None:
        db = SessionLocal()
        should_close = True
    else:
        should_close = False
    
    try:
        run = db.query(AgentRun).filter(AgentRun.run_id == run_id).first()
        if not run:
            return None
        
        # 최근 이벤트 조회
        events = get_run_events(run_id, db=db)
        
        # Todos 추출: 가장 마지막 plan_created 이벤트의 payload.todos 사용 (재계획/재시작 시 최신 계획 반영)
        todos = []
        for event in reversed(events):
            if event["type"] == EVENT_TYPE_PLAN_CREATED:
                todos = event["payload"].get("todos", [])
                break
        
        # 현재 실행 중인 todo 찾기
        current_todo_id = None
        for event in reversed(events):
            if event["type"] == EVENT_TYPE_TODO_STARTED:
                current_todo_id = event["payload"].get("todo_id")
                break

        # run_finished로 자동 마감된 todo 메타 (로그/통계용, completed로 오해 방지)
        status_by_id_pre = _todo_status_from_events(events)
        all_tids = set(status_by_id_pre.keys())
        for t in todos:
            tid = str(t.get("todo_id") or t.get("id") or "").strip()
            if tid:
                all_tids.add(tid)
        run_st = (run.status or "").strip().lower()
        auto_closed_todos = [
            {"todo_id": tid, "detail": "auto-closed on run_finished"}
            for tid in all_tids
            if run_st in ("completed", "failed") and status_by_id_pre.get(tid) != "completed"
        ]

        # 사용자용 3단계 축약 뷰 (조회 시점 계산, DB 미저장)
        todos_view = build_todos_view(
            getattr(run, "module_id", "engine") or "engine",
            todos,
            current_todo_id,
            events,
            run_status=run.status,
        )
        progress_percent = _compute_normalized_progress_percent(todos_view, run_st)
        
        # 카운터 계산
        tool_calls_count = sum(1 for e in events if e["type"] == EVENT_TYPE_TOOL_STARTED)
        files_explored_count = sum(
            1 for e in events 
            if e["type"] == EVENT_TYPE_TOOL_STARTED 
            and e["payload"].get("tool_name") in ("list_directory", "read_file", "search_files")
        )
        searches_count = sum(
            1 for e in events 
            if e["type"] == EVENT_TYPE_TOOL_STARTED 
            and e["payload"].get("tool_name") in ("rag_search", "web_search")
        )
        
        block = build_block_status(run_id, run.status, events, current_todo_id, paused=paused)
        needs_approval = block.get("reason_code") == "approval_required"
        approval_required = (block.get("related") or {}) if needs_approval else None
        return {
            "run_id": run.run_id,
            "session_id": run.session_id,
            "module_id": getattr(run, "module_id", "engine") or "engine",
            "run_kind": getattr(run, "run_kind", "generic") or "generic",
            "status": run.status,
            "created_at": run.created_at.isoformat() if run.created_at else None,
            "updated_at": run.updated_at.isoformat() if run.updated_at else None,
            "summary": run.summary,
            "todos": todos,
            "todos_view": todos_view,
            "progress_percent": progress_percent,
            "current_todo_id": current_todo_id,
            "counters": {
                "tool_calls": tool_calls_count,
                "files_explored": files_explored_count,
                "searches": searches_count,
            },
            "last_event_id": events[-1]["id"] if events else None,
            "last_event_ts": events[-1]["ts"] if events else None,
            "block": block,
            "needs_approval": needs_approval,
            "approval_required": approval_required,
            "auto_closed_todos": auto_closed_todos,
        }
    finally:
        if should_close:
            db.close()


def get_decision_from_events(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    이벤트 목록에서 mode_decision 이벤트를 찾아 Decision Layer용 요약 반환.
    """
    for event in events:
        if event.get("type") == "mode_decision":
            p = event.get("payload") or {}
            return {
                "initial_mode": p.get("initial_mode", ""),
                "selected_mode": p.get("selected_mode", ""),
                "detected_flags": p.get("detected_flags", []),
                "escalated": p.get("escalated", False),
                "escalation_reason": p.get("escalation_reason"),
            }
    return {
        "initial_mode": "",
        "selected_mode": "",
        "detected_flags": [],
        "escalated": False,
        "escalation_reason": None,
    }


def compute_run_health(
    events: List[Dict[str, Any]],
    run: Optional[Any] = None,
    *,
    p95_threshold_ms: float = 120_000,
    tool_latency_threshold_ms: float = 500,
) -> Dict[str, Any]:
    """
    Run Health Score (0~100) 및 경고 목록 계산.
    규칙: 기본 100 - escalation 10 - tool_error 20 - p95초과 15 - tool latency 10 - sanitizer 15, clamp 0~100.
    """
    score = 100
    warnings: List[str] = []
    decision = get_decision_from_events(events)
    if decision.get("escalated"):
        score -= 10
        warnings.append("Escalation 발생")
    tool_durations: List[float] = []
    has_tool_error = False
    for e in events:
        if e.get("type") == "tool_done":
            p = e.get("payload") or {}
            if not p.get("success", True):
                has_tool_error = True
            dur = p.get("duration_ms")
            if dur is not None:
                tool_durations.append(float(dur))
        if e.get("type") == "sanitizer_result":
            kind = (e.get("payload") or {}).get("kind", "")
            if kind and ("blocked" in kind or "removed" in kind):
                score -= 15
                warnings.append("Sanitizer issue")
                break
    if has_tool_error:
        score -= 20
        warnings.append("Tool error 존재")
    total_ms = None
    for e in reversed(events):
        if e.get("type") == EVENT_TYPE_RUN_FINISHED:
            p = e.get("payload") or {}
            total_ms = p.get("total_infer_ms")
            break
    if total_ms is None and len(events) >= 2:
        total_ms = (events[-1].get("ts", 0) - events[0].get("ts", 0)) * 1000
    if total_ms is not None and total_ms > p95_threshold_ms:
        score -= 15
        warnings.append("p95 초과")
    if tool_durations:
        avg = sum(tool_durations) / len(tool_durations)
        if avg > tool_latency_threshold_ms:
            score -= 10
            warnings.append(f"Tool latency 평균 {int(avg)}ms")
    score = max(0, min(100, score))
    ok_list: List[str] = []
    if not any("Sanitizer" in w for w in warnings):
        ok_list.append("Sanitizer clean")
    return {
        "score": score,
        "level": "green" if score >= 80 else "yellow" if score >= 60 else "red",
        "warnings": warnings,
        "ok": ok_list,
    }
