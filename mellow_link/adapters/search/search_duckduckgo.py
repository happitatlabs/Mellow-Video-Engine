"""
DuckDuckGo 기반 검색 어댑터 (ENABLE_WEB_SEARCH=1, ENABLE_OUTBOUND_HTTP=1일 때).
"""
import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Dict, List, Optional
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

try:
    import requests
except ImportError:
    requests = None

try:
    from bs4 import BeautifulSoup
    BS4_AVAILABLE = True
except ImportError:
    BS4_AVAILABLE = False

try:
    from ddgs import DDGS
    DDGS_AVAILABLE = True
except ImportError:
    try:
        from duckduckgo_search import DDGS
        DDGS_AVAILABLE = True
    except ImportError:
        DDGS_AVAILABLE = False

from mellow_link.adapters.search.base import SearchAdapter, SearchResult

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 10.0
MAX_CONTENT_LENGTH = 50000


class DuckDuckGoSearchAdapter(SearchAdapter):
    """duckduckgo-search 및 스크래핑 기반 웹 검색."""

    def __init__(self, max_workers: int = 3):
        if not requests:
            raise ImportError("requests 라이브러리가 필요합니다. pip install requests")
        self.executor = ThreadPoolExecutor(max_workers=max_workers)
        self._robots_cache: Dict[str, Optional[RobotFileParser]] = {}

    def _check_security(self) -> bool:
        """SecurityManager로 아웃바운드 HTTP 허용 여부 확인."""
        from mellow_link.core.security_manager import SecurityManager
        sm = SecurityManager.from_env()
        if sm.level == "EASY":
            return True
        if sm.level == "HARD":
            import os
            if os.getenv("MELLOW_HARD_OUTBOUND_HTTP_ALLOW", "").strip().lower() in {"1", "true", "yes"}:
                return True
            logger.warning("[DuckDuckGoSearchAdapter] HARD 모드: 아웃바운드 HTTP 차단됨")
            return False
        return True

    async def search(
        self,
        query: str,
        top_k: int = 5,
        scrape_content: bool = True,
    ) -> List[SearchResult]:
        if not self._check_security():
            raise PermissionError(
                "SecurityManager가 아웃바운드 HTTP를 차단했습니다. "
                "SECURITY_LEVEL을 확인하거나 MELLOW_HARD_OUTBOUND_HTTP_ALLOW를 설정하세요."
            )
        if DDGS_AVAILABLE:
            results = await self._search_ddgs(query, top_k)
        else:
            results = await self._search_instant_answer(query, top_k)
        if scrape_content and results:
            await self._scrape_results_parallel(results)
        return results[:top_k]

    async def _search_ddgs(self, query: str, max_results: int) -> List[SearchResult]:
        try:
            def _do_search():
                with DDGS() as ddgs:
                    return list(ddgs.text(query, max_results=max_results))

            raw_results = await asyncio.to_thread(_do_search)
            results: List[SearchResult] = []
            for item in raw_results:
                title = item.get("title", "")
                url = item.get("href", item.get("link", ""))
                snippet = item.get("body", item.get("snippet", ""))
                if title and url:
                    results.append(SearchResult(
                        title=title, url=url, snippet=snippet,
                        metadata={"source": "ddgs_text"},
                    ))
            logger.info("[DuckDuckGoSearchAdapter] DDGS returned %s results for: %s", len(results), query[:60])
            return results
        except Exception as e:
            logger.warning("[DuckDuckGoSearchAdapter] DDGS search failed, falling back: %s", e)
            return await self._search_instant_answer(query, max_results)

    async def _search_instant_answer(self, query: str, max_results: int) -> List[SearchResult]:
        import urllib.parse
        encoded = urllib.parse.quote_plus(query)
        url = f"https://api.duckduckgo.com/?q={encoded}&format=json&no_html=1&skip_disambig=1"
        try:
            response = await asyncio.to_thread(requests.get, url, timeout=DEFAULT_TIMEOUT)
            response.raise_for_status()
            data = response.json()
            results: List[SearchResult] = []
            if data.get("AbstractText"):
                results.append(SearchResult(
                    title=data.get("Heading", "요약"),
                    url=data.get("AbstractURL", ""),
                    snippet=data["AbstractText"],
                    metadata={"source": "duckduckgo_abstract"},
                ))
            if data.get("RelatedTopics"):
                for topic in data["RelatedTopics"][:max_results]:
                    if isinstance(topic, dict):
                        text = topic.get("Text", "")
                        u = topic.get("FirstURL", "")
                        if text and u:
                            results.append(SearchResult(
                                title=text[:100], url=u, snippet=text,
                                metadata={"source": "duckduckgo_related"},
                            ))
            return results[:max_results]
        except Exception as e:
            logger.error("[DuckDuckGoSearchAdapter] Instant Answer API error: %s", e)
            return []

    async def _scrape_results_parallel(self, results: List[SearchResult]) -> None:
        tasks = [self._scrape_content(r.url) for r in results]
        contents = await asyncio.gather(*tasks, return_exceptions=True)
        for result, content in zip(results, contents):
            if isinstance(content, Exception):
                logger.warning("[DuckDuckGoSearchAdapter] Failed to scrape %s: %s", result.url, content)
            elif isinstance(content, str):
                result.content = content
                result.scraped_at = datetime.now()

    async def _check_robots(self, url: str) -> bool:
        try:
            parsed = urlparse(url)
            base_url = f"{parsed.scheme}://{parsed.netloc}"
            if base_url in self._robots_cache:
                rp = self._robots_cache[base_url]
                if rp is None:
                    return True
                return rp.can_fetch("MellowLink-Bot", url)
            robots_url = urljoin(base_url, "/robots.txt")
            try:
                response = await asyncio.to_thread(requests.get, robots_url, timeout=5.0)
                if response.status_code == 200:
                    rp = RobotFileParser()
                    rp.set_url(robots_url)
                    rp.read()
                    self._robots_cache[base_url] = rp
                    return rp.can_fetch("MellowLink-Bot", url)
                self._robots_cache[base_url] = None
                return True
            except Exception:
                self._robots_cache[base_url] = None
                return True
        except Exception as e:
            logger.warning("[DuckDuckGoSearchAdapter] robots.txt check failed for %s: %s", url, e)
            return True

    async def _scrape_content(self, url: str) -> str:
        if not await self._check_robots(url):
            logger.warning("[DuckDuckGoSearchAdapter] robots.txt blocked: %s", url)
            return f"[차단됨] robots.txt에 의해 스크래핑이 차단되었습니다: {url}"
        try:
            response = await asyncio.to_thread(
                requests.get,
                url,
                timeout=DEFAULT_TIMEOUT,
                headers={"User-Agent": "MellowLink-Bot/1.0 (Research Tool)"},
            )
            response.raise_for_status()
            if not BS4_AVAILABLE:
                return "[오류] BeautifulSoup4 미설치로 스크래핑 불가"
            soup = BeautifulSoup(response.content, "html.parser")
            for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
                tag.decompose()
            content_parts = []
            title_tag = soup.find("title")
            if title_tag:
                content_parts.append(f"# {title_tag.get_text().strip()}\n")
            main = soup.find("main") or soup.find("article") or soup.find("body")
            if main:
                for para in main.find_all(["p", "h1", "h2", "h3", "h4", "h5", "h6", "li"]):
                    text = para.get_text().strip()
                    if text:
                        if para.name.startswith("h"):
                            level = int(para.name[1])
                            content_parts.append(f"{'#' * level} {text}\n")
                        elif para.name == "li":
                            content_parts.append(f"- {text}\n")
                        else:
                            content_parts.append(f"{text}\n")
            content = "\n".join(content_parts)
            if len(content) > MAX_CONTENT_LENGTH:
                content = content[:MAX_CONTENT_LENGTH] + "\n\n...(내용이 길어 일부만 표시됨)..."
            return content.strip()
        except requests.exceptions.RequestException as e:
            logger.warning("[DuckDuckGoSearchAdapter] Failed to scrape %s: %s", url, e)
            return f"[오류] 콘텐츠를 가져올 수 없습니다: {str(e)}"
        except Exception as e:
            logger.error("[DuckDuckGoSearchAdapter] Scraping error for %s: %s", url, e, exc_info=True)
            return f"[오류] 스크래핑 중 예상치 못한 오류: {str(e)}"

    def __del__(self):
        if hasattr(self, "executor"):
            self.executor.shutdown(wait=False)
