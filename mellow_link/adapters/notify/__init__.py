"""알림 어댑터 (Notify). ENABLE_TELEGRAM=0이면 Null, ON이면 Telegram."""
from mellow_link.adapters.notify.base import NotifyAdapter
from mellow_link.adapters.notify.factory import get_notifier

__all__ = ["NotifyAdapter", "get_notifier"]
