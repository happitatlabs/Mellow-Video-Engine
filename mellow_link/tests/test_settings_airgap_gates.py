"""
폐쇄망 Feature Flags 및 Gate API 단위/스모크 테스트.

- env 기반 플래그 기본값 OFF(폐쇄망 안전)
- Gate: allow_outbound_http(), allow_web_search(), allow_guardian_api(), allow_telegram(), allow_edge_tts()
- api_host 기본 127.0.0.1 유지(보안 핫픽)
"""

import os
import unittest

from mellow_link.config.settings import (
    get_settings,
    clear_settings_cache,
    Settings,
)


class TestSettingsAirgapGates(unittest.TestCase):
    def setUp(self):
        clear_settings_cache()
        self._saved_env = {}
        for key in (
            "ENABLE_OUTBOUND_HTTP",
            "ENABLE_WEB_SEARCH",
            "MELLOW_ENABLE_WEB_SEARCH",
            "ENABLE_GUARDIAN_APIS",
            "ENABLE_TELEGRAM",
            "ENABLE_EDGE_TTS",
            "MELLOW_API_HOST",
            "SERVER_HOST",
        ):
            if key in os.environ:
                self._saved_env[key] = os.environ.pop(key, None)

    def tearDown(self):
        for key in list(os.environ.keys()):
            if key in (
                "ENABLE_OUTBOUND_HTTP",
                "ENABLE_WEB_SEARCH",
                "MELLOW_ENABLE_WEB_SEARCH",
                "ENABLE_GUARDIAN_APIS",
                "ENABLE_TELEGRAM",
                "ENABLE_EDGE_TTS",
                "MELLOW_API_HOST",
                "SERVER_HOST",
            ):
                os.environ.pop(key, None)
        for key, val in self._saved_env.items():
            if val is not None:
                os.environ[key] = val
        clear_settings_cache()

    def test_defaults_airgap_safe(self):
        """폐쇄망 기본값: 모든 Gate OFF."""
        s = get_settings()
        self.assertFalse(s.allow_outbound_http(), "allow_outbound_http default OFF")
        self.assertFalse(s.allow_web_search(), "allow_web_search default OFF")
        self.assertFalse(s.allow_guardian_api(), "allow_guardian_api default OFF")
        self.assertFalse(s.allow_telegram(), "allow_telegram default OFF")
        self.assertFalse(s.allow_edge_tts(), "allow_edge_tts default OFF")

    def test_api_host_default_127(self):
        """서버 기본 바인딩 127.0.0.1 유지 (보안 핫픽)."""
        s = get_settings()
        self.assertEqual(s.api_host, "127.0.0.1", "api_host default 127.0.0.1")
        self.assertEqual(s.server_host, "127.0.0.1", "server_host default 127.0.0.1")

    def test_allow_outbound_http_env_1(self):
        os.environ["ENABLE_OUTBOUND_HTTP"] = "1"
        clear_settings_cache()
        s = get_settings()
        self.assertTrue(s.allow_outbound_http())

    def test_allow_outbound_http_env_0(self):
        os.environ["ENABLE_OUTBOUND_HTTP"] = "0"
        clear_settings_cache()
        s = get_settings()
        self.assertFalse(s.allow_outbound_http())

    def test_allow_web_search_env_1(self):
        os.environ["ENABLE_WEB_SEARCH"] = "1"
        clear_settings_cache()
        s = get_settings()
        self.assertTrue(s.allow_web_search())

    def test_allow_guardian_api_env_true(self):
        os.environ["ENABLE_GUARDIAN_APIS"] = "true"
        clear_settings_cache()
        s = get_settings()
        self.assertTrue(s.allow_guardian_api())

    def test_allow_telegram_env_1(self):
        os.environ["ENABLE_TELEGRAM"] = "1"
        clear_settings_cache()
        s = get_settings()
        self.assertTrue(s.allow_telegram())

    def test_allow_edge_tts_env_1(self):
        os.environ["ENABLE_EDGE_TTS"] = "1"
        clear_settings_cache()
        s = get_settings()
        self.assertTrue(s.allow_edge_tts())

    def test_gate_methods_exist(self):
        """Gate API 메서드 존재 및 bool 반환."""
        s = get_settings()
        self.assertIsInstance(s.allow_outbound_http(), bool)
        self.assertIsInstance(s.allow_web_search(), bool)
        self.assertIsInstance(s.allow_guardian_api(), bool)
        self.assertIsInstance(s.allow_telegram(), bool)
        self.assertIsInstance(s.allow_edge_tts(), bool)


if __name__ == "__main__":
    unittest.main()
