"""
Media tools boundary.

Creative image/video tools live here; core.agent_tools_creative remains as a
compatibility shim.
"""

import logging

from mellow_link.core.tool_registry import tool
from mellow_link.core.agent_tools_base import (
    _get_security,
    _normalize_read_path,
    _ensure_path_inside_sandbox_for_read,
)

logger = logging.getLogger(__name__)


@tool(category="creative")
async def create_image(prompt: str, width: int = 1024, height: int = 1024) -> str:
    from mellow_link.core.orchestrator import Orchestrator
    from mellow_link.media.schemas import ImageRequest
    from mellow_link.core.states import SystemState

    orchestrator = Orchestrator.get_instance()
    if orchestrator is None:
        return "[Offline] Orchestrator가 초기화되지 않았습니다."

    current_state = orchestrator.get_state()
    logger.info(f"[create_image] 현재 Orchestrator 상태: {current_state.name}")

    image_service = orchestrator.get_service("image")
    if image_service is None:
        return "[Offline] 이미지 서비스가 등록되지 않았습니다. ComfyUI 서버가 실행 중인지 확인하세요."

    if not image_service.is_available():
        service_status = image_service.get_status()
        logger.warning(f"[create_image] ImageService 상태: {service_status}")
        if service_status.name == "DISCONNECTED":
            return "[Offline] 이미지 서비스(ComfyUI)에 연결되지 않았습니다. ComfyUI 서버가 http://localhost:8188에서 실행 중인지 확인하세요."
        if service_status.name == "ERROR":
            return "[Error] 이미지 서비스가 오류 상태입니다. ComfyUI 서버 로그를 확인하세요."
        return f"[Offline] 이미지 서비스가 사용 불가능한 상태입니다. (상태: {service_status.name})"

    if current_state == SystemState.ERROR:
        return "[Error] 시스템이 오류 상태입니다. 시스템을 재시작하거나 오류를 해결한 후 다시 시도하세요."

    try:
        from mellow_link.infra.watchdog import VRAMWatchdog
        if VRAMWatchdog.is_gpu_available():
            temp_watchdog = VRAMWatchdog()
            gpu_info = await temp_watchdog.get_current_usage()
            if gpu_info and gpu_info.usage_percent >= 95.0:
                return f"[VRAM CRITICAL] VRAM 사용량이 {gpu_info.usage_percent:.1f}%로 너무 높습니다. 다른 작업을 종료한 후 다시 시도하세요."
            if gpu_info and gpu_info.usage_percent >= 90.0:
                logger.warning(f"[create_image] VRAM 사용량이 높습니다: {gpu_info.usage_percent:.1f}%")
    except Exception as vram_error:
        logger.debug(f"[create_image] VRAM 체크 실패 (무시): {vram_error}")

    try:
        logger.info("[create_image] 이미지 생성 시작 (VRAM 최적화 적용됨)")
        if current_state != SystemState.IMAGE:
            logger.info(f"[create_image] Orchestrator 상태를 IMAGE로 전환 시도 (현재: {current_state.name})")
            transition_result = await orchestrator.request_state_change(
                SystemState.IMAGE,
                reason="Image generation requested",
                force=False,
            )
            if transition_result.name != "SUCCESS":
                logger.warning(f"[create_image] 상태 전환 실패: {transition_result.name}. 계속 진행합니다.")

        request = ImageRequest(prompt=prompt, width=width, height=height)
        result = await image_service.generate(request)

        output_path = getattr(result, "file_path", None) or getattr(result, "output_path", None)
        if output_path:
            return f"[완료] 이미지 생성됨: {output_path}"
        if hasattr(result, "images") and result.images:
            return f"[완료] 이미지 생성 완료: {len(result.images)}개 이미지 생성됨. 경로: {result.images[0]}"
        return f"[완료] 이미지 생성 완료. 결과: {result}"
    except Exception as e:
        logger.exception("[create_image] failed")
        error_msg = str(e)
        if "connection" in error_msg.lower() or "connect" in error_msg.lower():
            return f"[Error] ComfyUI 서버에 연결할 수 없습니다. 서버가 실행 중인지 확인하세요. (상세: {error_msg})"
        if "timeout" in error_msg.lower():
            return f"[Error] 이미지 생성 시간 초과. ComfyUI 서버가 응답하지 않습니다. (상세: {error_msg})"
        if "vram" in error_msg.lower() or "memory" in error_msg.lower():
            return f"[Error] VRAM 부족으로 이미지 생성 실패. 다른 작업을 종료한 후 다시 시도하세요. (상세: {error_msg})"
        return f"[Error] 이미지 생성 실패: {error_msg}"


@tool(category="creative")
async def animate_image(
    image_path: str,
    motion_bucket_id: int = 127,
    target_duration: float = 12.0,
    loop_mode: str = "boomerang",
    overlap_seconds: float = 0.35,
) -> str:
    from mellow_link.core.orchestrator import Orchestrator
    from mellow_link.media.schemas import VideoRequest

    normalized_path = _normalize_read_path(image_path)
    safe_image = _get_security().resolve_for_read(normalized_path)
    path_err = _ensure_path_inside_sandbox_for_read(safe_image, image_path)
    if path_err:
        logger.critical("\033[91m[PATH_GATE_BLOCKED] animate_image path outside sandbox: '%s'\033[0m", image_path)
        return path_err

    orchestrator = Orchestrator.get_instance()
    if orchestrator is None:
        return "[Offline] Orchestrator가 초기화되지 않았습니다."

    video_service = orchestrator.get_service("video")
    if video_service is None or not getattr(video_service, "is_available", lambda: False)():
        return "[Offline] 비디오 서비스(ComfyUI/SVD)에 연결되지 않았습니다."

    try:
        logger.info("[animate_image] 비디오 생성 시작 (VRAM 최적화 적용됨)")
        req = VideoRequest(
            image_path=str(safe_image),
            motion_bucket_id=motion_bucket_id,
            target_duration=target_duration,
            loop_mode=loop_mode,
            overlap_seconds=overlap_seconds,
            fps=8,
        )
        out_path = await video_service.generate_video(req)
        return f"[완료] 비디오 생성됨: {out_path}"
    except Exception as e:
        logger.exception("[animate_image] failed")
        return f"[Error] 비디오 생성 실패: {e}"


__all__ = ["create_image", "animate_image"]
