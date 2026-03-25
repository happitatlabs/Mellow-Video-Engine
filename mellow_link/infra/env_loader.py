"""
Env Loader - 환경 변수 로드 및 Guardian API 키 제공

.env에서 ANTHROPIC_API_KEY, OPENAI_API_KEY, GOOGLE_API_KEY 등 tripe-chain 키를 로드합니다.
Tower/Verdict/Audit 호출 전 load_dotenv_early()를 호출하여 os.environ에 키를 확실히 등록합니다.
"""

import logging
import os
from pathlib import Path
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

_DOTENV_LOADED = False


def load_dotenv_early() -> None:
    """
    .env 파일을 1회 로드하여 os.environ에 등록.
    삼권분립 파이프라인(Tower/Verdict/Audit) 초기화 전 반드시 호출.

    - override=False: 이미 설정된 OS 환경변수는 덮어쓰지 않음.
    - 프로젝트 루트/ mellow_link/ 하위 .env 모두 시도.
    """
    global _DOTENV_LOADED
    if _DOTENV_LOADED:
        return
    _DOTENV_LOADED = True
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    base_path = (
        os.environ.get("MELLOW_LINK_PROJECT_ROOT")
        or os.environ.get("PROJECT_ROOT")
        or str(Path(__file__).resolve().parents[1])  # mellow_link/
    )
    load_dotenv(dotenv_path=os.path.join(base_path, ".env"), override=False)
    load_dotenv(dotenv_path=os.path.join(base_path, "mellow_link", ".env"), override=False)


def get_guardian_config() -> Tuple[Optional[str], Optional[str], str, float, int]:
    """
    Guardian Agents API 설정 반환.
    ENABLE_GUARDIAN_APIS=0 이면 키를 로드하지 않고 (None, None, ...) 반환.

    Returns:
        (anthropic_api_key, openai_api_key, provider, max_daily_cost, max_daily_tokens)
        provider: "anthropic" | "openai"
    """
    try:
        from mellow_link.config.settings import get_settings
        settings = get_settings()
        if not settings.allow_guardian_api():
            return None, None, "anthropic", 0.0, 0
        anthropic = (settings.anthropic_api_key or "").strip() or None
        openai_key = (settings.openai_api_key or "").strip() or None
        provider = (settings.guardian_provider or "anthropic").strip().lower()
        if provider not in ("anthropic", "openai"):
            provider = "anthropic"
        max_cost = getattr(settings, "max_daily_cost", 0.0) or 0.0
        max_tokens = getattr(settings, "max_daily_tokens", 0) or 0
        return anthropic, openai_key, provider, float(max_cost), int(max_tokens)
    except Exception as e:
        logger.debug(f"[EnvLoader] Failed to get guardian config: {e}")
        return None, None, "anthropic", 0.0, 0
