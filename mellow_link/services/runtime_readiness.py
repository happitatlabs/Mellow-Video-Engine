from __future__ import annotations

import logging
import shutil
import uuid
from typing import Any, Dict

import aiohttp

from mellow_link.services.runtime_config import get_comfyui_endpoint, is_media_ai_enabled, load_settings


logger = logging.getLogger(__name__)


def ffmpeg_readiness(settings: Dict[str, Any] | None = None) -> Dict[str, Any]:
    data = settings or load_settings()
    cfg = data.get("ffmpeg", {}) if isinstance(data, dict) else {}
    exe = str(cfg.get("path") or "ffmpeg")
    found = shutil.which(exe) if exe else None
    if found:
        return {"ok": True, "path": found}
    if exe and exe != "ffmpeg":
        return {"ok": False, "path": exe, "message": f"Configured ffmpeg path not found: {exe}"}
    return {"ok": False, "path": exe, "message": "ffmpeg not found on PATH"}


async def check_comfyui_readiness(settings: Dict[str, Any] | None = None) -> Dict[str, Any]:
    endpoint = get_comfyui_endpoint(settings)
    base_url = endpoint["base_url"]
    ws_url = endpoint["ws_url"]
    timeout = aiohttp.ClientTimeout(total=float(endpoint["timeout"]))
    result: Dict[str, Any] = {
        "ok": False,
        "host": endpoint["host"],
        "port": endpoint["port"],
        "base_url": base_url,
        "ws_url": ws_url,
        "http_ok": False,
        "ws_ok": False,
        "message": "",
    }

    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            try:
                async with session.get(f"{base_url}/system_stats") as resp:
                    if resp.status != 200:
                        result["message"] = f"/system_stats returned HTTP {resp.status}"
                        return result
                    result["http_ok"] = True
            except Exception as exc:
                result["message"] = f"/system_stats check failed: {exc}"
                return result

            try:
                ws = await session.ws_connect(f"{ws_url}?clientId=runtime_readiness_{uuid.uuid4().hex[:8]}")
                await ws.close()
                result["ws_ok"] = True
            except Exception as exc:
                result["message"] = f"WebSocket readiness check failed: {exc}"
                return result
    except Exception as exc:
        result["message"] = f"ComfyUI readiness session failed: {exc}"
        return result

    result["ok"] = True
    result["message"] = "ready"
    return result


async def get_runtime_readiness(settings: Dict[str, Any] | None = None) -> Dict[str, Any]:
    data = settings or load_settings()
    media_ai_enabled = is_media_ai_enabled()
    comfy = await check_comfyui_readiness(data) if media_ai_enabled else {
        **get_comfyui_endpoint(data),
        "ok": False,
        "http_ok": False,
        "ws_ok": False,
        "message": "Skipped because ENABLE_MEDIA_AI=0",
    }
    ffmpeg = ffmpeg_readiness(data)
    return {
        "media_ai_enabled": media_ai_enabled,
        "comfyui": comfy,
        "ffmpeg": ffmpeg,
        "ready_for_generation": bool(media_ai_enabled and comfy.get("ok")),
    }


async def assert_media_generation_ready(settings: Dict[str, Any] | None = None) -> Dict[str, Any]:
    readiness = await get_runtime_readiness(settings)
    if not readiness["media_ai_enabled"]:
        raise RuntimeError(
            "ENABLE_MEDIA_AI=0. Image/video generation is disabled. "
            "Set ENABLE_MEDIA_AI=1 before starting web_ui.py or api_server.py."
        )
    comfy = readiness["comfyui"]
    if not comfy.get("http_ok"):
        raise RuntimeError(
            f"ComfyUI HTTP readiness failed at {comfy.get('base_url')}. "
            f"{comfy.get('message')}"
        )
    if not comfy.get("ws_ok"):
        raise RuntimeError(
            f"ComfyUI WebSocket readiness failed at {comfy.get('ws_url', comfy.get('base_url'))}. "
            f"{comfy.get('message')}"
        )
    return readiness
