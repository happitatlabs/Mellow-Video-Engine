"""
Compatibility wrappers for creative tools.

Tool registration still happens from this module because the agent tool test
suite reloads this path to rebuild the registry.
"""

import logging

from mellow_link.core.tool_registry import tool

logger = logging.getLogger(__name__)


@tool(category="creative")
async def create_image(prompt: str, width: int = 1024, height: int = 1024) -> str:
    from mellow_link.media.tools import create_image as media_create_image

    return await media_create_image(prompt=prompt, width=width, height=height)


@tool(category="creative")
async def animate_image(
    image_path: str,
    motion_bucket_id: int = 127,
    target_duration: float = 12.0,
    loop_mode: str = "boomerang",
    overlap_seconds: float = 0.35,
) -> str:
    from mellow_link.media.tools import animate_image as media_animate_image

    return await media_animate_image(
        image_path=image_path,
        motion_bucket_id=motion_bucket_id,
        target_duration=target_duration,
        loop_mode=loop_mode,
        overlap_seconds=overlap_seconds,
    )


@tool(category="avatar")
async def speak(text: str, emotion: str = "neutral") -> str:
    from mellow_link.services.vtuber_relay import get_vtuber_relay
    from mellow_link import app_state

    if not app_state.settings or app_state.settings.vtuber_relay_enabled != 1:
        return "VTuber Relay가 비활성화되어 있습니다."

    relay = get_vtuber_relay()
    if relay is None or not relay.is_connected:
        return "후후, 아바타와의 회선이 끊겨 있어. 연결을 기다려야 하겠군."

    try:
        success = await relay.send_text(text=text, emotion=emotion)
        return "아바타에게 전달 완료. 잘 들리고 있을 거야." if success else "흥, 전송이 막혔어. 딜러가 바쁜 모양이야."
    except Exception as e:
        logger.exception("[speak] failed")
        return f"후후, 아바타 전송 중 예상 밖의 변수가 생겼군: {e}"


__all__ = ["create_image", "animate_image", "speak"]
