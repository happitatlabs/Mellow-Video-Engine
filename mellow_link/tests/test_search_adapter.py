"""
검색 어댑터 정책 차단 검증.

ENABLE_WEB_SEARCH=0 또는 ENABLE_OUTBOUND_HTTP=0일 때 get_search().search() 호출 시
PermissionError 및 메시지에 플래그명 포함 여부 검증.
"""
import asyncio
import os
import unittest

from mellow_link.config.settings import clear_settings_cache, get_settings


def _env_set(key: str, val: str) -> None:
    os.environ[key] = val


def _env_unset(key: str) -> None:
    os.environ.pop(key, None)


def _clear_search_factory() -> None:
    import mellow_link.adapters.search.factory as factory
    factory._search_instance = None


class TestSearchAdapterBlock(unittest.TestCase):
    def setUp(self):
        clear_settings_cache()
        _env_unset("ENABLE_WEB_SEARCH")
        _env_unset("ENABLE_OUTBOUND_HTTP")
        _env_set("ENABLE_WEB_SEARCH", "0")
        _env_set("ENABLE_OUTBOUND_HTTP", "1")
        clear_settings_cache()
        _clear_search_factory()

    def tearDown(self):
        clear_settings_cache()
        _env_unset("ENABLE_WEB_SEARCH")
        _env_unset("ENABLE_OUTBOUND_HTTP")

    def test_get_search_search_raises_when_web_search_disabled(self):
        from mellow_link.adapters.search import get_search
        adapter = get_search()
        self.assertFalse(get_settings().allow_web_search())
        with self.assertRaises(PermissionError) as ctx:
            asyncio.run(adapter.search("test query"))
        self.assertIn("ENABLE_WEB_SEARCH", str(ctx.exception))
        self.assertIn("0", str(ctx.exception))

    def test_get_search_search_raises_when_outbound_http_disabled(self):
        _env_set("ENABLE_WEB_SEARCH", "1")
        _env_set("ENABLE_OUTBOUND_HTTP", "0")
        clear_settings_cache()
        _clear_search_factory()
        from mellow_link.adapters.search import get_search
        adapter = get_search()
        with self.assertRaises(PermissionError):
            asyncio.run(adapter.search("test query"))
