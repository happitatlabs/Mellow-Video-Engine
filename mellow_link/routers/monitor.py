"""
Mellow-Link - Monitor Flow Router (Admin-Only)

Endpoints: /monitor/flow, /monitor/flow/view, /monitor/flow/detail/{event_id}
"""

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse

from mellow_link.dependencies import get_admin_user_required, get_admin_user_for_flow_view

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/monitor",
    tags=["Monitor"],
    dependencies=[Depends(get_admin_user_required)]
)


@router.get("/flow")
async def monitor_flow(
    minutes: int = Query(30, ge=1, le=1440, description="최근 N분 이내"),
    limit: int = Query(50, ge=1, le=200, description="반환 최대 이벤트 수"),
):
    """시스템 '생각의 흐름' 타임라인 (어드민 전용)."""
    from mellow_link.infra.memory_database import get_memory_db
    db = get_memory_db()
    events = db.get_monitor_flow_timeline(since_minutes=minutes, limit=limit)
    return {"events": events}


# NOTE: /monitor/flow/view and /monitor/flow/detail/{event_id} use a different
# dependency (get_admin_user_for_flow_view) that supports query-param auth.
# They are registered on the main app directly in main.py since they need
# different dependencies than the router-level dependency.
