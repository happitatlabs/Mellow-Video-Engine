"""
Evolution 서비스 Factory.

게이트 순서 (FAIL-CLOSED):
  1) env 하드 게이트: ENABLE_GUARDIAN_APIS=0 → DisabledEvolutionService (AIRGAP_BLOCK)
  2) 어댑터 소프트 게이트: ENABLE_EVOLUTION_ADAPTER=0 → DisabledEvolutionService (ADAPTER_DISABLED)
  그 외 → RealEvolutionService
"""
import logging
from typing import Optional

from mellow_link.core.evolution_service import (
    DisabledEvolutionService,
    EvolutionServiceBase,
    RealEvolutionService,
)

logger = logging.getLogger(__name__)

_evolution_service_instance: Optional[EvolutionServiceBase] = None


def get_evolution_service() -> EvolutionServiceBase:
    """
    1차: ENABLE_GUARDIAN_APIS=0 이면 Disabled (AIRGAP_BLOCK).
    2차: ENABLE_EVOLUTION_ADAPTER=0 이면 Disabled (ADAPTER_DISABLED).
    그 외 RealEvolutionService. 싱글톤 유지.
    """
    global _evolution_service_instance
    if _evolution_service_instance is not None:
        return _evolution_service_instance
    try:
        from mellow_link.config.settings import get_settings
        s = get_settings()
        if not s.allow_guardian_api():
            _evolution_service_instance = DisabledEvolutionService(
                default_code="AIRGAP_BLOCK",
                default_message="ENABLE_GUARDIAN_APIS=0. Guardian API 비활성화(폐쇄망). Evolution 실행 불가.",
            )
            logger.info("[EvolutionFactory] Using DisabledEvolutionService (AIRGAP_BLOCK)")
            return _evolution_service_instance
        if not getattr(s, "enable_evolution_adapter", False):
            _evolution_service_instance = DisabledEvolutionService(
                default_code="ADAPTER_DISABLED",
                default_message="ENABLE_EVOLUTION_ADAPTER=0. Evolution 어댑터 비활성화.",
            )
            logger.info("[EvolutionFactory] Using DisabledEvolutionService (ADAPTER_DISABLED)")
            return _evolution_service_instance
        _evolution_service_instance = RealEvolutionService()
        logger.info("[EvolutionFactory] Using RealEvolutionService")
    except Exception as e:
        logger.warning("[EvolutionFactory] Gate check failed, defaulting to Disabled: %s", e)
        _evolution_service_instance = DisabledEvolutionService(
            default_code="POLICY_DISABLED",
            default_message=f"설정 오류로 Evolution 비활성: {e!s}"[:200],
        )
    return _evolution_service_instance


def reset_evolution_service_cache() -> None:
    """
    싱글톤 캐시 리셋. 테스트/설정 리로드 후 get_evolution_service()가 재판정하도록 할 때 호출.
    """
    global _evolution_service_instance
    _evolution_service_instance = None
    logger.debug("[EvolutionFactory] Cache reset.")
