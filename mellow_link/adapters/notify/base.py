"""
알림 어댑터 인터페이스.

- NotifyAdapter: Telegram 등 (send_telegram, send_telegram_and_get_message_id).
  ENABLE_TELEGRAM=0이면 NullNotifyAdapter(no-op).
"""
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional


class NotifyAdapter(ABC):
    """알림 전송 어댑터. OFF 시 NullNotifyAdapter(no-op), ON 시 TelegramNotifyAdapter."""

    @abstractmethod
    def send_telegram(
        self,
        text: str,
        parse_mode: str = "HTML",
        reply_markup: Optional[Dict[str, Any]] = None,
        chat_id_override: Optional[str] = None,
    ) -> bool:
        """Telegram으로 메시지 전송. 성공 시 True, 실패/차단 시 False."""
        ...

    @abstractmethod
    def send_telegram_and_get_message_id(
        self,
        text: str,
        parse_mode: str = "HTML",
        reply_markup: Optional[Dict[str, Any]] = None,
        chat_id_override: Optional[str] = None,
    ) -> Optional[int]:
        """Telegram으로 메시지 전송 후 message_id 반환. 실패/차단 시 None."""
        ...
