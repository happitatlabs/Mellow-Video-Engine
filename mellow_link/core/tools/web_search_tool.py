"""
WebSearchTool - MellowLink Agent System용 웹 검색 도구

execute()는 get_search() 어댑터로 위임. RAG 적립(integrate_with_rag)은 이 클래스에서 유지.
"""

import hashlib
import json
import logging
import re
import uuid
from datetime import datetime
from typing import List, Optional, Dict, Any

logger = logging.getLogger(__name__)

# SearchResult는 어댑터 패키지에서 정의. 하위 호환용 re-export.
from mellow_link.adapters.search.base import SearchResult

# =============================================================================
# WebSearchTool Class
# =============================================================================

class WebSearchTool:
    """
    웹 검색 및 콘텐츠 수집 도구.
    
    Research 모드에서 외부 정보를 수집하고 RAG에 적립합니다.
    """
    
    DEFAULT_TIMEOUT = 10.0  # 개별 요청당 타임아웃 (초)
    DEFAULT_MAX_RESULTS = 5
    MAX_CONTENT_LENGTH = 50000  # 최대 스크래핑 콘텐츠 길이 (문자)
    
    def __init__(
        self,
        security_manager=None,
        memory_db=None,
        max_workers: int = 3
    ):
        """
        Args:
            security_manager: (미사용, 하위 호환용)
            memory_db: MemoryDatabase 인스턴스 (None이면 자동 로드). integrate_with_rag용.
            max_workers: (미사용, 하위 호환용)
        """
        if memory_db is None:
            from mellow_link.infra.memory_database import get_memory_db
            self.memory_db = get_memory_db()
        else:
            self.memory_db = memory_db
        logger.info("[WebSearchTool] Initialized")

    async def execute(
        self,
        query: str,
        top_k: int = DEFAULT_MAX_RESULTS,
        scrape_content: bool = True
    ) -> List[SearchResult]:
        """
        웹 검색을 수행합니다. get_search() 어댑터로 위임.
        ENABLE_WEB_SEARCH=0 또는 ENABLE_OUTBOUND_HTTP=0이면 PermissionError.
        """
        from mellow_link.adapters.search import get_search
        return await get_search().search(query, top_k=top_k, scrape_content=scrape_content)

    # ─── 콘텐츠 안전 필터 (RAG 저장 전 검사) ─────────────────────────

    # 프롬프트 인젝션 패턴 (웹 페이지에 숨겨진 공격 명령)
    _INJECTION_PATTERNS: List[re.Pattern] = [
        re.compile(r"ignore\s+(previous|all|above)\s+(instructions?|prompts?|rules?)", re.IGNORECASE),
        re.compile(r"you\s+are\s+now\s+(a|an|the)\s+", re.IGNORECASE),
        re.compile(r"disregard\s+(your|all|the)\s+(rules?|instructions?|guidelines?)", re.IGNORECASE),
        re.compile(r"system\s*prompt\s*:", re.IGNORECASE),
        re.compile(r"<\s*/?system\s*>", re.IGNORECASE),
        re.compile(r"\[INST\]|\[/INST\]|<<SYS>>|<</SYS>>", re.IGNORECASE),
        re.compile(r"act\s+as\s+(if|though)\s+you\s+(are|were)", re.IGNORECASE),
        re.compile(r"new\s+instructions?\s*:", re.IGNORECASE),
        re.compile(r"override\s+(security|safety|rules?|restrictions?)", re.IGNORECASE),
        re.compile(r"jailbreak|DAN\s+mode|developer\s+mode", re.IGNORECASE),
    ]

    # 위험 콘텐츠 키워드 (저장을 차단하지는 않지만, 태깅하여 추후 필터링 가능)
    _HAZARD_KEYWORDS: List[str] = [
        "rm -rf", "format c:", "del /f /s",
        "DROP TABLE", "DELETE FROM", "; --",
        "eval(", "exec(", "__import__(",
        "os.system(", "subprocess.run(",
    ]

    def _sanitize_for_rag(self, content: str, url: str) -> tuple:
        """
        웹 콘텐츠를 RAG 저장 전에 안전성 검사.

        Returns:
            (sanitized_content, safety_tags) — 통과 시 콘텐츠, 차단 시 None
            safety_tags: List[str] — 감지된 위험 태그 목록
        """
        safety_tags: List[str] = []

        # 1. 프롬프트 인젝션 패턴 검사
        for pattern in self._INJECTION_PATTERNS:
            if pattern.search(content):
                match_text = pattern.pattern[:40]
                safety_tags.append(f"PROMPT_INJECTION:{match_text}")
                logger.warning(
                    "[WebSearchTool] ⚠️ 프롬프트 인젝션 패턴 감지: url=%s pattern=%s",
                    url, match_text,
                )

        # 프롬프트 인젝션이 감지되면 RAG 저장 차단
        if any(t.startswith("PROMPT_INJECTION") for t in safety_tags):
            logger.critical(
                "[WebSearchTool] RAG 저장 차단: 프롬프트 인젝션 감지. url=%s", url
            )
            return None, safety_tags

        # 2. 위험 코드 패턴 태깅 (저장은 하되, 태그 부착)
        for kw in self._HAZARD_KEYWORDS:
            if kw in content:
                safety_tags.append(f"HAZARD_CODE:{kw}")

        # 3. 콘텐츠 크기 제한 (RAG에 지나치게 큰 문서 저장 방지)
        if len(content) > self.MAX_CONTENT_LENGTH:
            content = content[:self.MAX_CONTENT_LENGTH]
            safety_tags.append("TRUNCATED")

        # 4. HTML/스크립트 잔류물 제거
        content = re.sub(r"<script[^>]*>.*?</script>", "", content, flags=re.DOTALL | re.IGNORECASE)
        content = re.sub(r"<style[^>]*>.*?</style>", "", content, flags=re.DOTALL | re.IGNORECASE)
        content = re.sub(r"<[^>]+>", "", content)  # 잔여 HTML 태그 제거

        return content.strip(), safety_tags

    # ─── RAG 통합 ──────────────────────────────────────────────────

    async def integrate_with_rag(
        self,
        content: str,
        metadata: Dict[str, Any],
        collection_name: str = "web_search"
    ) -> Optional[str]:
        """
        추출된 데이터를 안전성 검사 후 RAG 시스템에 저장합니다.

        안전 필터:
        - 프롬프트 인젝션 패턴 감지 시 저장 차단
        - 위험 코드 패턴 태깅
        - HTML/스크립트 잔류물 자동 제거
        - 콘텐츠 크기 제한

        Args:
            content: 저장할 콘텐츠
            metadata: 메타데이터 (url, title, snippet 등)
            collection_name: 컬렉션 이름 (기본값: "web_search")

        Returns:
            저장된 레코드 ID (실패 또는 차단 시 None)
        """
        try:
            url = metadata.get("url", "unknown")

            # ── 안전성 검사 ──
            sanitized, safety_tags = self._sanitize_for_rag(content, url)
            if sanitized is None:
                # 프롬프트 인젝션 감지 — 저장하지 않음
                return None

            from mellow_link.infra.memory_database import ExperienceRecord

            record_id = str(uuid.uuid4())
            task_intent = metadata.get("title", "웹 검색 결과")
            task_hash = self._compute_content_hash(sanitized)

            # safety_tags를 메타데이터에 포함 (추후 조회 시 필터링 가능)
            enriched_metadata = {**metadata, "safety_tags": safety_tags}

            record = ExperienceRecord(
                id=record_id,
                task_intent=task_intent[:2000],
                task_hash=task_hash,
                context_summary=f"URL: {url}",
                action_steps=json.dumps(
                    {"source": "web_search", "metadata": enriched_metadata},
                    ensure_ascii=False,
                ),
                final_outcome=sanitized[:5000],
                is_success=1,
                created_at=datetime.now(),
                used_tools=json.dumps(["web_search"]),
            )

            success = self.memory_db.save_experience(record)

            if success:
                tag_info = f" (tags: {safety_tags})" if safety_tags else ""
                logger.info(f"[WebSearchTool] Saved to RAG: {record_id}{tag_info}")
                return record_id
            else:
                logger.error("[WebSearchTool] Failed to save to RAG")
                return None

        except Exception as e:
            logger.error(f"[WebSearchTool] RAG integration failed: {e}", exc_info=True)
            return None
    
    def _compute_content_hash(self, content: str) -> str:
        """콘텐츠의 해시를 계산합니다."""
        return hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]


# =============================================================================
# Factory Function
# =============================================================================

def create_web_search_tool(**kwargs) -> WebSearchTool:
    """WebSearchTool 인스턴스를 생성합니다."""
    return WebSearchTool(**kwargs)
