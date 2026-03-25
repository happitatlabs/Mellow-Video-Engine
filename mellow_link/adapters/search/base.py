"""
검색 어댑터 인터페이스.

- SearchAdapter: 웹 검색 (search). ENABLE_WEB_SEARCH / ENABLE_OUTBOUND_HTTP로 Null vs 실구현.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional


@dataclass
class SearchResult:
    """웹 검색 결과 데이터 구조."""
    title: str
    url: str
    snippet: str = ""
    content: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    scraped_at: Optional[datetime] = None


class SearchAdapter(ABC):
    """웹 검색 어댑터. OFF 시 NullSearchAdapter(차단), ON 시 DuckDuckGoSearchAdapter."""

    @abstractmethod
    async def search(
        self,
        query: str,
        top_k: int = 5,
        scrape_content: bool = True,
    ) -> List[SearchResult]:
        """
        웹 검색 수행.

        Args:
            query: 검색 쿼리
            top_k: 최대 결과 수
            scrape_content: True면 결과 URL 스크래핑하여 content 채움

        Returns:
            SearchResult 리스트. 차단 시 PermissionError.
        """
        ...
