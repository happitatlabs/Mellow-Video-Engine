"""
알림 어댑터 Factory.

ENABLE_TELEGRAM=0이면 NullNotifyAdapter(no-op). ON이면 TelegramNotifyAdapter.
"""
import logging
from typing import Optional

from mellow_link.adapters.notify.base import NotifyAdapter
from mellow_link.adapters.notify.notify_null import NullNotifyAdapter
from mellow_link.adapters.notify.notify_telegram import TelegramNotifyAdapter

logger = logging.getLogger(__name__)

_notify_instance: Optional[NotifyAdapter] = None


def get_notifier() -> NotifyAdapter:
    """
    ENABLE_TELEGRAM=0 → NullNotifyAdapter (no-op, 로그에 ENABLE_TELEGRAM).
    ENABLE_TELEGRAM=1 → TelegramNotifyAdapter (api.telegram.org).
    """
    global _notify_instance
    if _notify_instance is not None:
        return _notify_instance
    try:
        from mellow_link.config.settings import get_settings
        if not get_settings().allow_telegram():
            _notify_instance = NullNotifyAdapter()
            logger.info("[NotifyFactory] Using NullNotifyAdapter (ENABLE_TELEGRAM=0)")
        else:
            _notify_instance = TelegramNotifyAdapter()
            logger.info("[NotifyFactory] Using TelegramNotifyAdapter")
    except Exception as e:
        logger.warning("[NotifyFactory] allow_telegram check failed, defaulting to Null: %s", e)
        _notify_instance = NullNotifyAdapter()
    return _notify_instance
