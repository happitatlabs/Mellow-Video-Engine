"""
Null/Local Providers - 폐쇄망 토글 시 외부 API 대체

ENABLE_GUARDIAN_APIS=0, ENABLE_WEB_SEARCH=0, ENABLE_TELEGRAM=0, ENABLE_EDGE_TTS=0 일 때
각 서비스는 no-op 또는 명확한 차단 메시지를 반환하며, 로그에 어떤 토글로 차단됐는지 기록합니다.
"""

import logging

logger = logging.getLogger(__name__)

# 토글 이름 → 한글 설명 (로그용)
_AIRGAP_TOGGLE_NAMES = {
    "ENABLE_GUARDIAN_APIS": "Guardian API (Tower/Verdict/Audit)",
    "ENABLE_WEB_SEARCH": "웹 검색",
    "ENABLE_TELEGRAM": "Telegram 알림",
    "ENABLE_EDGE_TTS": "Edge TTS / VTuber speak",
}


def log_airgap_block(service_name: str, toggle_name: str, detail: str = "") -> None:
    """
    폐쇄망 토글로 인한 차단 시 로그 기록.
    어떤 토글 때문에 차단됐는지 명시.

    Args:
        service_name: 서비스/모듈 이름 (예: "GuardianService", "WebSearchTool")
        toggle_name: 환경변수 이름 (예: "ENABLE_GUARDIAN_APIS")
        detail: 추가 설명 (선택)
    """
    label = _AIRGAP_TOGGLE_NAMES.get(toggle_name, toggle_name)
    msg = f"[AirGap] {service_name}: {toggle_name}=0으로 차단됨 ({label})"
    if detail:
        msg += f". {detail}"
    logger.info(msg)
