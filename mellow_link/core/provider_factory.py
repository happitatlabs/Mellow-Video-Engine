"""
Provider Factory - 지능형 리모컨 (Triple Intelligence Chain)

.env의 TOWER_MODEL, VERDICT_MODEL, AUDIT_MODEL과 동기화하여
각 보직(Tower/Verdict/Audit)에 맞는 LLM 클라이언트를 제공합니다.
Tower/Verdict/Audit 각각 일일 비용 한도 지원 (MAX_DAILY_COST_GOOGLE, MAX_DAILY_COST_OPENAI, MAX_DAILY_COST).
"""

import logging
import os
from dataclasses import dataclass
from typing import Optional, Any, Callable, Awaitable, Tuple

logger = logging.getLogger(__name__)

# 역할별 기본 provider 매핑
ROLE_PROVIDER = {"tower": "google", "verdict": "openai", "audit": "anthropic"}

# 대략적 비용 추정 (USD/1K tokens)
_ESTIMATE_COST_PER_1K = {"google": 0.0001, "openai": 0.0005, "anthropic": 0.003}


@dataclass
class ProviderConfig:
    """프로바이더 설정."""
    provider: str
    model: str
    api_key: Optional[str] = None


def _get_settings() -> Any:
    from mellow_link.config.settings import get_settings
    return get_settings()


def _resolve_model(provider: str, model_name: Optional[str], role: Optional[str]) -> str:
    """model_name이 없으면 role에 따라 .env 기본값 할당. os.getenv 우선 (load_dotenv_early 후)."""
    if model_name:
        return model_name
    if role == "tower":
        v = (os.getenv("TOWER_MODEL") or "").strip()
        if v:
            return v
        s = _get_settings()
        return getattr(s, "tower_model", None) or "gemini-2.5-flash"
    if role == "verdict":
        v = (os.getenv("VERDICT_MODEL") or "").strip()
        if v:
            return v
        s = _get_settings()
        return getattr(s, "verdict_model", None) or "gpt-4o-mini"
    if role == "audit":
        fallback = "claude-sonnet-4-20250514"
        v = (os.getenv("AUDIT_MODEL") or "").strip()
        if v:
            if "claude-3-5-sonnet" in v.lower():
                return fallback
            return v
        s = _get_settings()
        raw = getattr(s, "audit_model", None) or fallback
        if raw and "claude-3-5-sonnet" in raw.lower():
            return fallback
        return raw
    return model_name or "gpt-4o-mini"


def _resolve_provider(provider_name: Optional[str], role: Optional[str]) -> str:
    """provider가 없으면 role에 따라 기본값."""
    if provider_name:
        return provider_name.strip().lower()
    if role:
        return ROLE_PROVIDER.get(role, "openai")
    s = _get_settings()
    return getattr(s, "agent_provider", "openai") or "openai"


def get_client(
    provider_name: Optional[str] = None,
    model_name: Optional[str] = None,
    role: Optional[str] = None,
) -> ProviderConfig:
    """
    LLM 클라이언트 설정 반환.
    
    Args:
        provider_name: "google"|"openai"|"anthropic" (None이면 role 기반)
        model_name: 모델명 (None이면 TOWER_MODEL/VERDICT_MODEL/AUDIT_MODEL)
        role: "tower"|"verdict"|"audit" - model_name 생략 시 사용
        
    Returns:
        ProviderConfig (provider, model, api_key)
    """
    from mellow_link.infra.env_loader import load_dotenv_early
    load_dotenv_early()

    provider = _resolve_provider(provider_name, role)
    model = _resolve_model(provider, model_name, role)
    s = _get_settings()

    if not s.allow_guardian_api():
        return ProviderConfig(provider=provider, model=model, api_key=None)

    api_key = None
    if provider in ("google", "gemini"):
        api_key = (s.google_api_key or "").strip() or None
        if not api_key:
            api_key = (os.getenv("GOOGLE_API_KEY") or "").strip() or None
    elif provider == "openai":
        api_key = (s.openai_api_key or "").strip() or None
        if not api_key:
            api_key = (os.getenv("OPENAI_API_KEY") or "").strip() or None
    elif provider == "anthropic":
        api_key = (s.anthropic_api_key or "").strip() or None
        if not api_key:
            api_key = (os.getenv("ANTHROPIC_API_KEY") or "").strip() or None

    return ProviderConfig(provider=provider, model=model, api_key=api_key)


def _check_provider_quota(provider: str) -> Tuple[bool, str]:
    """프로바이더별 일일 비용 한도 확인. (allowed, reason)"""
    s = _get_settings()
    if provider == "google":
        limit = getattr(s, "max_daily_cost_google", 0.0) or 0.0
    elif provider == "openai":
        limit = getattr(s, "max_daily_cost_openai", 0.0) or 0.0
    elif provider == "anthropic":
        limit = getattr(s, "max_daily_cost", 0.0) or 0.0
    else:
        return True, ""
    if limit <= 0:
        return True, ""
    try:
        from mellow_link.infra.memory_database import get_memory_db
        db = get_memory_db()
        usage = db.get_daily_usage(provider)
        if usage["cost"] >= limit:
            return False, f"일일 비용 한도 초과 ({provider}: {usage['cost']:.4f} USD >= {limit} USD)"
    except Exception as e:
        logger.warning("[ProviderFactory] Quota check failed: %s", e)
    return True, ""


def _record_provider_usage(provider: str, token_count: int, cost: float, endpoint: str = "generate") -> None:
    """프로바이더 사용량 기록."""
    try:
        from mellow_link.infra.memory_database import get_memory_db
        db = get_memory_db()
        db.save_api_usage(provider=provider, endpoint=endpoint, token_count=token_count, cost=cost)
    except Exception as e:
        logger.debug("[ProviderFactory] Record usage failed: %s", e)


def _estimate_cost(prompt: str, response_len: int, provider: str) -> Tuple[int, float]:
    """대략적 토큰/비용 추정 (영어 4자≈1토큰)."""
    inp = max(1, len(prompt) // 4)
    out = max(1, response_len // 4)
    total = inp + out
    rate = _ESTIMATE_COST_PER_1K.get(provider, 0.001)
    cost = (total / 1000.0) * rate
    return total, cost


def estimate_token_cost(prompt: str, response_len: int, provider: str) -> Tuple[int, float]:
    """
    토큰 사용량 및 비용 추정 (공개 API).
    evolution_manager 등에서 리소스 추적용.
    Returns:
        (token_count, cost_usd)
    """
    return _estimate_cost(prompt, response_len, provider)


async def generate_async(
    provider_name: str,
    model_name: str,
    prompt: str,
    api_key: Optional[str] = None,
    max_tokens: int = 4096,
) -> str:
    """
    프로바이더별 비동기 텍스트 생성.
    일일 비용 한도 초과 시 RuntimeError.
    ENABLE_GUARDIAN_APIS=0 이면 RuntimeError (폐쇄망).
    
    Returns:
        생성된 텍스트
    """
    s = _get_settings()
    if not s.allow_guardian_api():
        from mellow_link.core.null_providers import log_airgap_block
        log_airgap_block("ProviderFactory.generate_async", "ENABLE_GUARDIAN_APIS", "Evolution(Tower/Verdict/Audit) 실행 불가")
        raise RuntimeError(
            "ENABLE_GUARDIAN_APIS=0. Evolution(Tower/Verdict/Audit) 실행 불가. 폐쇄망 모드입니다."
        )

    provider = (provider_name or "openai").strip().lower()
    if provider == "gemini":
        provider = "google"
    key = api_key or get_client(provider_name, model_name).api_key

    allowed, reason = _check_provider_quota(provider)
    if not allowed:
        raise RuntimeError(reason)

    result = ""
    if provider == "google":
        result = await _generate_gemini(model_name, prompt, key, max_tokens)
    elif provider == "openai":
        result = await _generate_openai(model_name, prompt, key, max_tokens)
    elif provider == "anthropic":
        result = await _generate_anthropic(model_name, prompt, key, max_tokens)
    else:
        raise ValueError(f"Unsupported provider: {provider}")

    tokens, cost = _estimate_cost(prompt, len(result or ""), provider)
    _record_provider_usage(provider, tokens, cost, "generate_async")
    return result


async def _generate_gemini(model: str, prompt: str, api_key: Optional[str], max_tokens: int) -> str:
    """Gemini API 호출."""
    import asyncio
    print(f"DEBUG: Calling model with name: {model} (provider=gemini)")
    if not api_key:
        raise ValueError(
            "GOOGLE_API_KEY가 설정되지 않았습니다. .env 파일에 GOOGLE_API_KEY를 추가하거나 "
            "환경 변수 GOOGLE_API_KEY를 설정하세요. (Tower 단계)"
        )

    def _sync_call() -> str:
        try:
            import google.generativeai as genai
            genai.configure(api_key=api_key)
            model_obj = genai.GenerativeModel(model)
            cfg = {"max_output_tokens": max_tokens}
            response = model_obj.generate_content(prompt, generation_config=cfg)
            return (response.text or "").strip()
        except ImportError:
            try:
                from google import genai as gm
                client = gm.Client(api_key=api_key)
                r = client.models.generate_content(model=model, contents=prompt)
                return (getattr(r, "text", None) or str(r) or "").strip()
            except ImportError:
                raise ImportError("Install google-generativeai for Gemini (pip install google-generativeai)")

    return await asyncio.to_thread(_sync_call)


async def _generate_openai(model: str, prompt: str, api_key: Optional[str], max_tokens: int) -> str:
    """OpenAI API 호출."""
    print(f"DEBUG: Calling model with name: {model} (provider=openai)")
    if not api_key:
        raise ValueError("OPENAI_API_KEY required")
    from openai import AsyncOpenAI
    client = AsyncOpenAI(api_key=api_key)
    r = await client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=max_tokens,
        temperature=0.3,
    )
    return (r.choices[0].message.content or "").strip()


async def _generate_anthropic(model: str, prompt: str, api_key: Optional[str], max_tokens: int) -> str:
    """Anthropic API 호출."""
    print(f"DEBUG: Calling model with name: {model} (provider=anthropic)")
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY required")
    from anthropic import AsyncAnthropic
    client = AsyncAnthropic(api_key=api_key)
    r = await client.messages.create(
        model=model,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3,
    )
    text = ""
    for b in r.content:
        if hasattr(b, "text"):
            text += b.text
    return text.strip()
