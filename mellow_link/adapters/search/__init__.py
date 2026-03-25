"""
검색 어댑터: 웹 검색을 정책에 따라 Null vs DuckDuckGo로 분리.
"""
from mellow_link.adapters.search.base import SearchAdapter, SearchResult
from mellow_link.adapters.search.factory import get_search

__all__ = [
    "SearchAdapter",
    "SearchResult",
    "get_search",
]
