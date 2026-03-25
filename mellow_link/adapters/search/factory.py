"""
검색 어댑터 Factory.

ENABLE_WEB_SEARCH=0 또는 ENABLE_OUTBOUND_HTTP=0 → NullSearchAdapter.
둘 다 1이면 DuckDuckGoSearchAdapter.
"""
import logging
from typing import Optional

from mellow_link.adapters.search.base import SearchAdapter, SearchResult
from mellow_link.adapters.search.search_null import NullSearchAdapter
from mellow_link.adapters.search.search_duckduckgo import DuckDuckGoSearchAdapter

logger = logging.getLogger(__name__)

_search_instance: Optional[SearchAdapter] = None


def get_search() -> SearchAdapter:
    global _search_instance
    if _search_instance is not None:
        return _search_instance
    try:
        from mellow_link.config.settings import get_settings
        s = get_settings()
        if not s.allow_outbound_http():
            _search_instance = NullSearchAdapter()
            logger.info("[SearchFactory] Using NullSearchAdapter (ENABLE_OUTBOUND_HTTP=0)")
        elif not s.allow_web_search():
            _search_instance = NullSearchAdapter()
            logger.info("[SearchFactory] Using NullSearchAdapter (ENABLE_WEB_SEARCH=0)")
        else:
            _search_instance = DuckDuckGoSearchAdapter()
            logger.info("[SearchFactory] Using DuckDuckGoSearchAdapter")
    except Exception as e:
        logger.warning("[SearchFactory] allow check failed, defaulting to Null: %s", e)
        _search_instance = NullSearchAdapter()
    return _search_instance
