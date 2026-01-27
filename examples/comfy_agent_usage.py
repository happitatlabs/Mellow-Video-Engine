"""
ComfyVideoAgent Usage Example
=============================

ComfyUI API를 통해 LTX-Video 및 SVD 비디오를 생성하는 예제 스크립트.

Prerequisites:
1. ComfyUI 서버가 localhost:8188에서 실행 중이어야 합니다.
2. 워크플로우 JSON 파일이 workflows/ 디렉토리에 있어야 합니다.
3. 필요한 모델이 ComfyUI에 설치되어 있어야 합니다.

Usage:
    python examples/comfy_agent_usage.py
"""

import logging
from pathlib import Path
import sys

# 프로젝트 루트를 Python 경로에 추가
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from modules.comfy_video_agent import (
    ComfyVideoAgent,
    ComfyConfig,
    VideoGenerationParams,
    WorkflowType,
)

# 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
)

logger = logging.getLogger(__name__)


def progress_callback(current: int, total: int, node_id: str) -> None:
    """진행률 콜백 함수."""
    percent = (current / total) * 100 if total > 0 else 0
    print(f"  Progress: {current}/{total} ({percent:.1f}%) - Node: {node_id}")


def example_ltx_video():
    """LTX-Video 생성 예제."""
    print("\n" + "=" * 60)
    print("LTX-Video Generation Example")
    print("=" * 60)

    # 설정
    config = ComfyConfig(
        host="127.0.0.1",
        port=8188,
        ltx_workflow_path=project_root / "workflows" / "ltx_video_example_api.json",
        execution_timeout=600.0,  # 10분
    )

    # 에이전트 생성
    agent = ComfyVideoAgent(config)

    try:
        # 1. WebSocket 연결 (중요: 반드시 먼저 연결!)
        print("\n[1] Connecting to ComfyUI WebSocket...")
        if not agent.connect():
            print("ERROR: Failed to connect to ComfyUI")
            return

        print(f"    Connected! Client ID: {agent.client_id}")

        # 2. 비디오 생성 파라미터
        params = VideoGenerationParams(
            prompt=(
                "Vast misty mountain range at dawn, layers of blue-gray peaks "
                "fading into fog, golden light breaking through dramatic clouds, "
                "ancient pine forest in foreground, cinematic wide shot, "
                "atmospheric depth, no humans, masterpiece quality"
            ),
            negative_prompt="humans, people, faces, text, watermark, blurry",
            num_frames=97,  # 약 4초 @ 24fps
            fps=24,
            width=768,
            height=512,
            output_prefix="mellow_ltx",
            output_dir=project_root / "output",
        )

        # 3. 비디오 생성
        print("\n[2] Generating video...")
        print(f"    Prompt: {params.prompt[:60]}...")
        print(f"    Frames: {params.num_frames}")

        result = agent.generate_video(
            workflow_type=WorkflowType.LTX_VIDEO,
            params=params,
            progress_callback=progress_callback,
        )

        # 4. 결과 출력
        print("\n[3] Result:")
        if result.success:
            print(f"    SUCCESS!")
            print(f"    Prompt ID: {result.prompt_id}")
            print(f"    Execution Time: {result.execution_time:.2f}s")
            print(f"    Output Files:")
            for f in result.output_files:
                print(f"      - {f}")
        else:
            print(f"    FAILED: {result.error_message}")

    finally:
        # 5. 정리
        print("\n[4] Cleanup...")
        agent.disconnect()
        print("    Done!")


def example_svd_video():
    """SVD (Stable Video Diffusion) 생성 예제."""
    print("\n" + "=" * 60)
    print("SVD (Stable Video Diffusion) Generation Example")
    print("=" * 60)

    # 테스트 이미지 경로 (실제 이미지로 교체 필요)
    test_image = project_root / "test_assets" / "input_image.png"

    if not test_image.exists():
        print(f"\nWARNING: Test image not found: {test_image}")
        print("Please provide an input image for SVD generation.")
        print("Creating placeholder directory...")
        test_image.parent.mkdir(parents=True, exist_ok=True)
        return

    # 설정
    config = ComfyConfig(
        host="127.0.0.1",
        port=8188,
        svd_workflow_path=project_root / "workflows" / "svd_example_api.json",
    )

    agent = ComfyVideoAgent(config)

    try:
        # 연결
        print("\n[1] Connecting to ComfyUI WebSocket...")
        if not agent.connect():
            print("ERROR: Failed to connect")
            return

        # SVD 파라미터
        params = VideoGenerationParams(
            source_image_path=test_image,
            motion_bucket_id=127,  # 1-255, 높을수록 움직임 증가
            augmentation_level=0.0,
            output_prefix="mellow_svd",
            output_dir=project_root / "output",
        )

        # 생성
        print("\n[2] Generating video from image...")
        print(f"    Source: {test_image.name}")
        print(f"    Motion: {params.motion_bucket_id}")

        result = agent.generate_video(
            workflow_type=WorkflowType.SVD,
            params=params,
            progress_callback=progress_callback,
        )

        # 결과
        print("\n[3] Result:")
        if result.success:
            print(f"    SUCCESS! Generated in {result.execution_time:.2f}s")
            for f in result.output_files:
                print(f"    - {f}")
        else:
            print(f"    FAILED: {result.error_message}")

    finally:
        agent.disconnect()


def example_system_check():
    """시스템 상태 확인 예제."""
    print("\n" + "=" * 60)
    print("ComfyUI System Check")
    print("=" * 60)

    config = ComfyConfig(host="127.0.0.1", port=8188)
    agent = ComfyVideoAgent(config)

    # 시스템 상태 조회 (WebSocket 연결 필요 없음)
    print("\n[1] System Stats:")
    stats = agent.get_system_stats()
    if stats:
        devices = stats.get("devices", [])
        for i, device in enumerate(devices):
            print(f"    GPU {i}: {device.get('name', 'Unknown')}")
            vram = device.get("vram_total", 0)
            vram_free = device.get("vram_free", 0)
            print(f"      VRAM: {vram_free / 1e9:.1f} / {vram / 1e9:.1f} GB free")
    else:
        print("    Failed to get system stats (is ComfyUI running?)")

    # 큐 상태
    print("\n[2] Queue Status:")
    queue = agent.get_queue_status()
    if queue:
        running = queue.get("queue_running", [])
        pending = queue.get("queue_pending", [])
        print(f"    Running: {len(running)} jobs")
        print(f"    Pending: {len(pending)} jobs")
    else:
        print("    Failed to get queue status")


def main():
    """메인 함수."""
    print("\n" + "=" * 60)
    print(" Mellow-Video-Engine: ComfyVideoAgent Examples")
    print("=" * 60)

    # 1. 시스템 체크
    example_system_check()

    # 2. LTX-Video 예제 (워크플로우 파일 필요)
    # example_ltx_video()

    # 3. SVD 예제 (이미지 + 워크플로우 파일 필요)
    # example_svd_video()

    print("\n" + "=" * 60)
    print(" Examples completed!")
    print("=" * 60)
    print("\nTo run video generation examples:")
    print("  1. Ensure ComfyUI is running on localhost:8188")
    print("  2. Place workflow JSON files in workflows/")
    print("  3. Uncomment example_ltx_video() or example_svd_video()")
    print()


if __name__ == "__main__":
    main()
