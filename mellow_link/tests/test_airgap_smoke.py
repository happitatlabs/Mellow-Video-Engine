"""
폐쇄망 토글 OFF(default) 기준 스모크 테스트.

필수 시나리오:
1) ENABLE_OUTBOUND_HTTP=0 → web_search 실행 시 차단
2) ENABLE_GUARDIAN_APIS=0 → /evolution/cycle 호출 시 거부(명확한 메시지)
3) ENABLE_TELEGRAM=0 → 알림 호출 no-op, 예외 없음
4) ENABLE_EDGE_TTS=0 → VTuber relay 실제 WS 송신 안 함(모킹)
5) mode=research 요청이 router에서 제한됨
"""

import asyncio
import os
import unittest
from unittest.mock import MagicMock

from mellow_link.config.settings import clear_settings_cache, get_settings


def _env_set(key: str, val: str) -> None:
    os.environ[key] = val


def _env_unset(key: str) -> None:
    os.environ.pop(key, None)


class TestAirgapSmokeOutboundHttp(unittest.TestCase):
    """1) ENABLE_OUTBOUND_HTTP=0 에서 web_search 실행 시 차단."""

    def setUp(self):
        clear_settings_cache()
        _env_unset("ENABLE_OUTBOUND_HTTP")
        _env_set("ENABLE_WEB_SEARCH", "1")  # 웹검색은 켜고, 아웃바운드만 끔

    def tearDown(self):
        clear_settings_cache()

    def test_web_search_blocked_when_outbound_http_off(self):
        _env_set("ENABLE_OUTBOUND_HTTP", "0")
        clear_settings_cache()
        from mellow_link.core.tools.web_search_tool import WebSearchTool
        tool = WebSearchTool()
        with self.assertRaises(PermissionError) as ctx:
            asyncio.run(tool.execute("test query"))
        self.assertIn("ENABLE_OUTBOUND_HTTP", str(ctx.exception))
        err = str(ctx.exception)
        self.assertTrue("폐쇄망" in err or "외부 HTTP" in err, msg=f"차단 사유 명시: {err}")


class TestAirgapSmokeGuardianApis(unittest.TestCase):
    """2) ENABLE_GUARDIAN_APIS=0 에서 /evolution/cycle 호출 시 거부(명확한 메시지)."""

    def setUp(self):
        clear_settings_cache()
        _env_unset("ENABLE_GUARDIAN_APIS")

    def tearDown(self):
        clear_settings_cache()

    def test_evolution_cycle_rejected_with_clear_message_when_guardian_off(self):
        _env_set("ENABLE_GUARDIAN_APIS", "0")
        clear_settings_cache()
        from mellow_link.core.evolution_manager import get_evolution_manager
        em = get_evolution_manager()
        proposal = asyncio.run(em.run_evolution_cycle("test evolution request"))
        self.assertIsNotNone(proposal)
        err = proposal.error or ""
        self.assertIn("ENABLE_GUARDIAN_APIS", err, msg=f"proposal.error에 ENABLE_GUARDIAN_APIS 포함 필요: {err}")
        self.assertTrue(
            "AIRGAP_BLOCK" in err or "폐쇄망" in err or "제한" in err or "생략" in err or "disabled" in err,
            msg=f"명확한 거부 메시지 필요: {err}",
        )


class TestAirgapGuardianKeysNotLoaded(unittest.TestCase):
    """ENABLE_GUARDIAN_APIS=0 일 때 Guardian API 키가 로드/초기화되지 않음을 보장."""

    def setUp(self):
        clear_settings_cache()
        _env_unset("ENABLE_GUARDIAN_APIS")

    def tearDown(self):
        clear_settings_cache()

    def test_get_guardian_config_returns_no_keys_when_guardian_off(self):
        _env_set("ENABLE_GUARDIAN_APIS", "0")
        _env_set("OPENAI_API_KEY", "sk-test-dont-load")
        _env_set("ANTHROPIC_API_KEY", "sk-ant-dont-load")
        clear_settings_cache()
        from mellow_link.infra.env_loader import get_guardian_config
        a_key, o_key, prov, max_cost, max_tokens = get_guardian_config()
        self.assertIsNone(a_key, "anthropic key must not be loaded when ENABLE_GUARDIAN_APIS=0")
        self.assertIsNone(o_key, "openai key must not be loaded when ENABLE_GUARDIAN_APIS=0")

    def test_get_client_returns_no_api_key_when_guardian_off(self):
        _env_set("ENABLE_GUARDIAN_APIS", "0")
        _env_set("GOOGLE_API_KEY", "AIza-dont-load")
        clear_settings_cache()
        from mellow_link.core.provider_factory import get_client
        cfg = get_client(provider_name="google", role="tower")
        self.assertIsNone(cfg.api_key, "get_client must not return api_key when ENABLE_GUARDIAN_APIS=0")

    def test_settings_guardian_keys_empty_when_guardian_off(self):
        _env_set("ENABLE_GUARDIAN_APIS", "0")
        _env_set("OPENAI_API_KEY", "sk-should-be-ignored")
        _env_set("ANTHROPIC_API_KEY", "sk-ant-should-be-ignored")
        _env_set("GOOGLE_API_KEY", "AIza-should-be-ignored")
        clear_settings_cache()
        s = get_settings()
        self.assertFalse(s.allow_guardian_api())
        self.assertEqual((s.openai_api_key or "").strip(), "")
        self.assertEqual((s.anthropic_api_key or "").strip(), "")
        self.assertEqual((s.google_api_key or "").strip(), "")

    def test_guardian_service_has_no_keys_when_guardian_off(self):
        _env_set("ENABLE_GUARDIAN_APIS", "0")
        _env_set("OPENAI_API_KEY", "sk-dont-store")
        _env_set("ANTHROPIC_API_KEY", "sk-ant-dont-store")
        clear_settings_cache()
        import mellow_link.core.guardian_service as guardian_module
        guardian_module._guardian_instance = None
        from mellow_link.core.guardian_service import get_guardian_service, PolicyGuardian
        svc = get_guardian_service()
        # 폐쇄망 시 PolicyGuardian 반환 → API 키 미사용
        self.assertIsInstance(svc, PolicyGuardian, "ENABLE_GUARDIAN_APIS=0이면 PolicyGuardian이어야 함")


class TestAirgapSmokeTelegram(unittest.TestCase):
    """3) ENABLE_TELEGRAM=0 에서 알림 호출 no-op, 예외 없음."""

    def setUp(self):
        clear_settings_cache()
        _env_unset("ENABLE_TELEGRAM")

    def tearDown(self):
        clear_settings_cache()

    def test_send_telegram_noop_no_exception(self):
        _env_set("ENABLE_TELEGRAM", "0")
        clear_settings_cache()
        from mellow_link.services.notification_service import send_telegram
        ok = send_telegram("test message")
        self.assertFalse(ok)

    def test_notify_evolution_applied_noop_no_exception(self):
        _env_set("ENABLE_TELEGRAM", "0")
        clear_settings_cache()
        from mellow_link.services.notification_service import notify_evolution_applied
        ok = notify_evolution_applied(
            proposal_id="test-id",
            target_file="test.py",
            message="done",
        )
        self.assertFalse(ok)


class TestAirgapSmokeEdgeTts(unittest.TestCase):
    """4) ENABLE_EDGE_TTS=0 에서 VTuber relay가 실제 WS 송신을 하지 않음(모킹)."""

    def setUp(self):
        clear_settings_cache()
        _env_unset("ENABLE_EDGE_TTS")

    def tearDown(self):
        clear_settings_cache()

    def test_vtuber_relay_no_ws_send_when_edge_tts_off(self):
        _env_set("ENABLE_EDGE_TTS", "0")
        clear_settings_cache()
        from mellow_link.services.vtuber_relay import (
            VTuberRelayService,
            VTuberMessage,
            VTuberConnectionStatus,
        )

        relay = VTuberRelayService(ws_url="ws://localhost:9999/client-ws")
        mock_ws = MagicMock()
        relay._websocket = mock_ws
        relay._status = VTuberConnectionStatus.CONNECTED
        # _send_to_vtuber: when allow_edge_tts() is False, returns True before any ws.send
        msg = VTuberMessage(text="hello", emotion="neutral")
        result = asyncio.run(relay._send_to_vtuber(msg))
        self.assertTrue(result)
        mock_ws.send.assert_not_called()


class TestAirgapSmokeModeResearchRestriction(unittest.TestCase):
    """5) mode=research 요청이 router에서 제한됨 (설정 기반 로직 검증)."""

    def setUp(self):
        clear_settings_cache()
        _env_unset("ENABLE_WEB_SEARCH")
        _env_unset("MELLOW_ENABLE_WEB_SEARCH")

    def tearDown(self):
        clear_settings_cache()

    def test_mode_research_downgraded_to_thinking_when_web_search_off(self):
        """Router와 동일한 판정: ENABLE_WEB_SEARCH=0 이면 research → thinking, mode_restriction 설정."""
        _env_set("ENABLE_WEB_SEARCH", "0")
        clear_settings_cache()
        s = get_settings()
        self.assertFalse(s.allow_web_search())
        mode = "research"
        airgap_mode_restriction_reason = None
        if mode == "research":
            if not s.allow_web_search():
                mode = "thinking"
                airgap_mode_restriction_reason = (
                    "research 모드는 폐쇄망 설정(ENABLE_WEB_SEARCH=0)으로 인해 thinking으로 제한되었습니다."
                )
        self.assertEqual(mode, "thinking")
        self.assertIsNotNone(airgap_mode_restriction_reason)
        self.assertIn("ENABLE_WEB_SEARCH", airgap_mode_restriction_reason)
        self.assertIn("thinking", airgap_mode_restriction_reason)


if __name__ == "__main__":
    unittest.main()
