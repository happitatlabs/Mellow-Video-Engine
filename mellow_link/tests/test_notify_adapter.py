"""
알림 어댑터 정책 차단 검증.

ENABLE_TELEGRAM=0일 때 get_notifier().send_telegram() → False,
send_telegram_and_get_message_id() → None, 로그에 ENABLE_TELEGRAM 포함.
"""
import logging
import os
import unittest

from mellow_link.config.settings import clear_settings_cache, get_settings


def _env_set(key: str, val: str) -> None:
    os.environ[key] = val


def _env_unset(key: str) -> None:
    os.environ.pop(key, None)


def _clear_notify_factory() -> None:
    import mellow_link.adapters.notify.factory as factory
    factory._notify_instance = None


class TestNotifyAdapterBlock(unittest.TestCase):
    def setUp(self):
        clear_settings_cache()
        _env_unset("ENABLE_TELEGRAM")
        _env_set("ENABLE_TELEGRAM", "0")
        clear_settings_cache()
        _clear_notify_factory()

    def tearDown(self):
        clear_settings_cache()
        _env_unset("ENABLE_TELEGRAM")
        _clear_notify_factory()

    def test_get_notifier_send_telegram_returns_false_when_disabled(self):
        from mellow_link.adapters.notify import get_notifier
        adapter = get_notifier()
        self.assertFalse(get_settings().allow_telegram())
        ok = adapter.send_telegram("test")
        self.assertFalse(ok)

    def test_get_notifier_send_telegram_and_get_message_id_returns_none_when_disabled(self):
        from mellow_link.adapters.notify import get_notifier
        adapter = get_notifier()
        mid = adapter.send_telegram_and_get_message_id("test")
        self.assertIsNone(mid)

    def test_null_notify_logs_enable_telegram(self):
        """ENABLE_TELEGRAM=0일 때 NullNotifyAdapter 호출 시 로그에 ENABLE_TELEGRAM 포함."""
        import mellow_link.adapters.notify.factory as factory
        factory._notify_instance = None
        _env_set("ENABLE_TELEGRAM", "0")
        clear_settings_cache()

        log_capture: list = []
        handler = logging.Handler()
        handler.setLevel(logging.DEBUG)
        handler.emit = lambda r: log_capture.append(r.getMessage())
        logger = logging.getLogger("mellow_link.core.null_providers")
        logger.setLevel(logging.DEBUG)
        logger.addHandler(handler)
        try:
            from mellow_link.adapters.notify import get_notifier
            get_notifier().send_telegram("test")
            self.assertTrue(
                any("ENABLE_TELEGRAM" in m for m in log_capture),
                msg=f"Expected ENABLE_TELEGRAM in log messages: {log_capture}",
            )
        finally:
            logger.removeHandler(handler)
            logger.setLevel(logging.NOTSET)
