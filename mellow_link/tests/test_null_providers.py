"""
Null/Local Provider 동작 스모크 테스트.

ENABLE_* = 0 일 때 각 서비스가 no-op 또는 명확한 차단 메시지로 동작하는지 검증.
"""

import asyncio
import os
import unittest

from mellow_link.config.settings import clear_settings_cache, get_settings


def _env_off(key: str):
    if key in os.environ:
        os.environ.pop(key)


def _env_on(key: str, val: str = "1"):
    os.environ[key] = val


class TestNullProvidersWebSearch(unittest.TestCase):
    def setUp(self):
        clear_settings_cache()
        _env_off("ENABLE_WEB_SEARCH")
        _env_off("MELLOW_ENABLE_WEB_SEARCH")

    def tearDown(self):
        clear_settings_cache()

    def test_web_search_execute_raises_when_disabled(self):
        _env_on("ENABLE_WEB_SEARCH", "0")
        _env_on("ENABLE_OUTBOUND_HTTP", "1")
        clear_settings_cache()
        import mellow_link.adapters.search.factory as search_factory
        search_factory._search_instance = None
        from mellow_link.core.tools.web_search_tool import WebSearchTool
        tool = WebSearchTool()
        with self.assertRaises(PermissionError) as ctx:
            asyncio.run(tool.execute("test query"))
        self.assertIn("ENABLE_WEB_SEARCH=0", str(ctx.exception))


def _clear_notify_factory():
    import mellow_link.adapters.notify.factory as factory
    factory._notify_instance = None


class TestNullProvidersTelegram(unittest.TestCase):
    def setUp(self):
        clear_settings_cache()
        _env_off("ENABLE_TELEGRAM")

    def tearDown(self):
        clear_settings_cache()

    def test_send_telegram_noop_when_disabled(self):
        _env_on("ENABLE_TELEGRAM", "0")
        clear_settings_cache()
        _clear_notify_factory()
        from mellow_link.services.notification_service import send_telegram
        ok = send_telegram("test")
        self.assertFalse(ok)

    def test_send_telegram_and_get_message_id_returns_none_when_disabled(self):
        _env_on("ENABLE_TELEGRAM", "0")
        clear_settings_cache()
        _clear_notify_factory()
        from mellow_link.services.notification_service import send_telegram_and_get_message_id
        mid = send_telegram_and_get_message_id("test")
        self.assertIsNone(mid)


class TestNullProvidersGuardian(unittest.TestCase):
    def setUp(self):
        clear_settings_cache()
        _env_off("ENABLE_GUARDIAN_APIS")

    def tearDown(self):
        clear_settings_cache()

    def test_guardian_audit_insight_returns_noop_when_disabled(self):
        _env_on("ENABLE_GUARDIAN_APIS", "0")
        clear_settings_cache()
        from mellow_link.infra.memory_database import BehaviorInsight
        from mellow_link.core.guardian_service import get_guardian_service
        guardian = get_guardian_service()
        insight = BehaviorInsight(
            id="test-id",
            pattern_type="failure_pattern",
            finding="test",
            recommendation="rec",
            confidence=0.9,
        )
        result = asyncio.run(guardian.audit_insight(insight))
        self.assertFalse(result.guardian_actually_ran)
        # PolicyGuardian 사용 시 critique에 ENABLE_GUARDIAN_APIS=0 또는 PolicyGuardian 명시
        self.assertTrue(
            "ENABLE_GUARDIAN_APIS=0" in result.critique or "PolicyGuardian" in result.critique,
            msg=f"critique={result.critique!r}",
        )

    def test_generate_async_raises_when_guardian_disabled(self):
        _env_on("ENABLE_GUARDIAN_APIS", "0")
        clear_settings_cache()
        from mellow_link.core.provider_factory import generate_async
        with self.assertRaises(RuntimeError) as ctx:
            asyncio.run(generate_async("openai", "gpt-4o-mini", "hello", api_key="dummy"))
        self.assertIn("ENABLE_GUARDIAN_APIS=0", str(ctx.exception))


class TestNullProvidersLogHelper(unittest.TestCase):
    def test_log_airgap_block_does_not_raise(self):
        from mellow_link.core.null_providers import log_airgap_block
        log_airgap_block("TestService", "ENABLE_WEB_SEARCH", "detail")
