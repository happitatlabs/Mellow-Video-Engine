"""
Mellow-Link - Avatar Router

Endpoints: /avatar/status, /avatar/speak
"""

import logging

from fastapi import APIRouter, Header, HTTPException

from mellow_link import app_state
from mellow_link.services import get_vtuber_relay
from mellow_link.utils import get_avatar_status, DEFAULT_AVATAR_WS_PORT

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Avatar"])


@router.get("/avatar/status")
async def get_avatar_status_endpoint():
    """
    Get VTuber avatar service status.

    Returns connection status, port info, and relay status.
    """
    avatar_status = get_avatar_status(
        port=app_state.settings.avatar_ws_port if app_state.settings else DEFAULT_AVATAR_WS_PORT
    )
    relay = get_vtuber_relay()

    return {
        "avatar_service": avatar_status,
        "relay": relay.get_status() if relay else {"connected": False, "status": "not_initialized"},
        "config": {
            "ws_port": app_state.settings.avatar_ws_port if app_state.settings else DEFAULT_AVATAR_WS_PORT,
            "ws_url": app_state.settings.avatar_ws_url if app_state.settings else "ws://localhost:12393"
        }
    }


@router.post("/avatar/speak")
async def avatar_speak(
    text: str,
    emotion: str = "neutral",
    authorization: str = Header(None)
):
    """
    Send text to VTuber avatar for speech synthesis.

    Only available for authenticated users (admin has priority).
    """
    if not authorization:
        raise HTTPException(status_code=401, detail="Authentication required")

    relay = get_vtuber_relay()
    if not relay:
        raise HTTPException(status_code=503, detail="VTuber relay not initialized")

    if not relay.is_connected:
        raise HTTPException(status_code=503, detail="VTuber not connected")

    success = await relay.send_text(text, emotion=emotion)
    return {"success": success, "text": text[:100], "emotion": emotion}
