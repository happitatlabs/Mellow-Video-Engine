"""ENABLE_TELEGRAM=0일 때 사용. send_telegram/send_telegram_and_get_message_id no-op."""
import logging
from typing import Any, Dict, Optional

from mellow_link.adapters.notify.base import NotifyAdapter
from mellow_link.core.null_providers import log_airgap_block

logger = logging.getLogger(__name__)


class NullNotifyAdapter(NotifyAdapter):
    """Telegram 비활성화 시 사용. 전송 no-op, False/None 반환."""

    def send_telegram(
        self,
        text: str,
        parse_mode: str = "HTML",
        reply_markup: Optional[Dict[str, Any]] = None,
        chat_id_override: Optional[str] = None,
    ) -> bool:
        log_airgap_block("NullNotifyAdapter.send_telegram", "ENABLE_TELEGRAM")
        return False

    def send_telegram_and_get_message_id(
        self,
        text: str,
        parse_mode: str = "HTML",
        reply_markup: Optional[Dict[str, Any]] = None,
        chat_id_override: Optional[str] = None,
    ) -> Optional[int]:
        log_airgap_block("NullNotifyAdapter.send_telegram_and_get_message_id", "ENABLE_TELEGRAM")
        return None
