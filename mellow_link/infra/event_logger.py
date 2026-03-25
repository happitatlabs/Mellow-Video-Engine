# mellow_link/infra/event_logger.py

"""
Event Logger (JSONL + DB Version)
- Saves events to both JSONL file and SQLite database.
- All data files are stored inside mellow_link/data/ folder.
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional, Any

# Force events directory to be inside mellow_link/data/events/
# __file__ = mellow_link/infra/event_logger.py
# .parent = mellow_link/infra/
# .parent = mellow_link/
_MELLOW_LINK_DIR = Path(__file__).parent.parent
_FORCED_DATA_DIR = _MELLOW_LINK_DIR / "data"
EVENTS_DIR = _FORCED_DATA_DIR / "events"
EVENTS_DIR.mkdir(parents=True, exist_ok=True)
EVENTS_FILE = EVENTS_DIR / "events.jsonl"

logger = logging.getLogger(__name__)

# [SBMA][INTENT] intent 표준 키
SBMA_INTENTS = {
    "edit", "regenerate", "thumbs_up", "thumbs_down",
    "report", "abort", "stop", "delete_message",
}

def _save_event_to_db(
    event_type: str,
    message: str,
    session_id: Optional[int],
    message_id: Optional[int],
    user_id: Optional[int],
    context_metadata: str
) -> bool:
    """
    Save event to database using EventLog model.
    Returns True on success, False on failure.
    """
    try:
        from .database import SessionLocal, EventLog

        db = SessionLocal()
        try:
            event = EventLog(
                event_type=event_type,
                session_id=session_id,
                message_id=message_id,
                user_id=user_id,
                message=message,
                context_metadata=context_metadata,
                processed=False
            )
            db.add(event)
            db.commit()
            return True
        except Exception as e:
            db.rollback()
            logger.warning(f"[Event] DB save failed: {e}")
            return False
        finally:
            db.close()
    except ImportError as e:
        logger.warning(f"[Event] Could not import database module: {e}")
        return False


def log_event(
    event_type: str,
    message: str,
    session_id: Optional[str] = None,  # int -> str 변경 (UUID 호환)
    task_id: Optional[str] = None,     # message_id -> task_id 매핑 가능
    user_id: Optional[str] = None,
    context_metadata: Optional[Dict] = None
) -> bool:
    timestamp = datetime.utcnow().isoformat()
    meta: Dict[str, Any] = context_metadata or {}

    # Intent Normalization
    if event_type == "intent":
        if "intent" not in meta or not meta["intent"]:
            meta["intent"] = "unknown"
        else:
            meta["intent"] = str(meta["intent"]).strip().lower()

    event_record = {
        "event_type": event_type,
        "timestamp": timestamp,
        "session_id": session_id,
        "task_id": task_id,
        "user_id": user_id,
        "message": message,
        "context_metadata": meta
    }

    jsonl_ok = False
    db_ok = False

    try:
        # 1. JSONL 파일 저장 (Append Only)
        with open(EVENTS_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(event_record, ensure_ascii=False) + "\n")
        jsonl_ok = True

        # 2. DB 저장 (EventLog 테이블에 INSERT)
        # Convert string IDs to int for DB (None if not numeric)
        db_session_id = int(session_id) if session_id and session_id.isdigit() else None
        db_message_id = int(task_id) if task_id and task_id.isdigit() else None
        db_user_id = int(user_id) if user_id and user_id.isdigit() else None

        db_ok = _save_event_to_db(
            event_type=event_type,
            message=message,
            session_id=db_session_id,
            message_id=db_message_id,
            user_id=db_user_id,
            context_metadata=json.dumps(meta, ensure_ascii=False)
        )

        if jsonl_ok and db_ok:
            logger.info(f"📝 [Event] {event_type}: {message[:50]}... (JSONL+DB)")
        elif jsonl_ok:
            logger.info(f"📝 [Event] {event_type}: {message[:50]}... (JSONL only)")

        return jsonl_ok  # Return True if at least JSONL succeeded

    except Exception as e:
        logger.error(f"❌ [Event] Failed to log event: {e}")
        return False

# 호환성을 위한 래퍼 함수
def log_intent(intent: str, message: str = "", **kwargs) -> bool:
    return log_event(
        event_type="intent",
        message=message,
        context_metadata={"intent": intent, **kwargs}
    )