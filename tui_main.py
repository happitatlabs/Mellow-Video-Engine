#!/usr/bin/env python3
"""
DEPRECATED LEGACY ENTRY POINT
=============================
This launcher targets the deprecated Textual UI under `ui/`.

Mellow-Video-Engine TUI Entry Point
====================================

Textual 기반 터미널 UI 애플리케이션 실행 진입점.

Usage:
    python tui_main.py [OPTIONS]

Options:
    --workflows-dir PATH    워크플로우 디렉토리 (default: ./workflows)
    --output-dir PATH       출력 디렉토리 (default: ./output)
    --config PATH           설정 파일 경로 (default: ./config/settings.yaml)
    --debug                 디버그 모드 활성화

Examples:
    # 기본 실행
    python tui_main.py

    # 커스텀 디렉토리
    python tui_main.py --workflows-dir ./my_workflows --output-dir ./my_output

    # 디버그 모드
    python tui_main.py --debug
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path


def setup_logging(debug: bool = False) -> None:
    """로깅 설정."""
    level = logging.DEBUG if debug else logging.INFO

    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
        handlers=[
            logging.FileHandler("mellow_tui.log", encoding="utf-8"),
        ],
    )

    # Textual 내부 로거 레벨 조정
    logging.getLogger("textual").setLevel(logging.WARNING)
    logging.getLogger("websocket").setLevel(logging.WARNING)


def parse_args() -> argparse.Namespace:
    """명령줄 인수 파싱."""
    parser = argparse.ArgumentParser(
        description="Mellow-Video-Engine TUI Application",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    parser.add_argument(
        "--workflows-dir",
        type=Path,
        default=Path("workflows"),
        help="워크플로우 JSON 파일이 있는 디렉토리",
    )

    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("output"),
        help="생성된 비디오 저장 디렉토리",
    )

    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config/settings.yaml"),
        help="설정 파일 경로",
    )

    parser.add_argument(
        "--debug",
        action="store_true",
        help="디버그 모드 활성화",
    )

    return parser.parse_args()


def check_dependencies() -> bool:
    """필수 의존성 확인."""
    missing = []

    try:
        import textual
    except ImportError:
        missing.append("textual")

    try:
        import requests
    except ImportError:
        missing.append("requests")

    try:
        import websocket
    except ImportError:
        missing.append("websocket-client")

    if missing:
        print("Missing dependencies:")
        for dep in missing:
            print(f"  - {dep}")
        print("\nInstall with: pip install " + " ".join(missing))
        return False

    return True


def main() -> int:
    """메인 함수."""
    args = parse_args()

    # 의존성 확인
    if not check_dependencies():
        return 1

    # 로깅 설정
    setup_logging(args.debug)

    # 디렉토리 생성
    args.workflows_dir.mkdir(parents=True, exist_ok=True)
    args.output_dir.mkdir(parents=True, exist_ok=True)

    # 앱 실행
    from ui.app import MellowApp

    app = MellowApp(
        config_path=args.config,
        workflows_dir=args.workflows_dir,
        output_dir=args.output_dir,
    )

    app.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
