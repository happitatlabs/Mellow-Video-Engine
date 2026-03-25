"""
Agent Tools - 리서치/분석 도구: web_search, analyze_text.
"""
import logging
import os
import re
from collections import Counter

from mellow_link.core.tool_registry import tool
from mellow_link.core.agent_tools_base import (
    _is_emergency_lockdown,
    _require_requests,
    requests,
)

logger = logging.getLogger(__name__)


@tool(category="research")
async def web_search(query: str, max_results: int = 5) -> str:
    """
    웹에서 정보를 검색합니다. 최신 정보, 뉴스, 기술 문서 등을 찾을 때 사용하세요.
    DuckDuckGo 검색을 수행하고, 결과를 RAG에 자동으로 저장합니다.

    Args:
        query: 검색할 키워드나 질문 (예: "Python async await 최신 기능", "2024 AI 트렌드")
        max_results: 반환할 최대 결과 수 (기본값: 5, 최대: 10)

    Returns:
        검색 결과를 요약한 문자열. 각 결과는 제목, URL, 요약으로 구성됩니다.

    Example:
        web_search("Python 3.12 새로운 기능")
        -> "1. [제목] Python 3.12 Release Notes - [URL] https://... - [요약] ..."
    """
    if _is_emergency_lockdown():
        return "[차단됨] Emergency Lockdown 모드: 웹 검색이 비활성화되었습니다."

    try:
        from mellow_link.adapters.search import get_search
        from mellow_link.core.tools.web_search_tool import WebSearchTool

        search_results = await get_search().search(
            query, top_k=min(max_results, 10), scrape_content=True
        )

        if not search_results:
            return f"[검색 결과 없음] '{query}'에 대한 결과를 찾을 수 없습니다."

        formatted_results = []
        for idx, result in enumerate(search_results, 1):
            formatted_results.append(f"{idx}. **{result.title}**")
            formatted_results.append(f"   URL: {result.url}")
            if result.snippet:
                snippet_text = result.snippet[:300]
                if len(result.snippet) > 300:
                    snippet_text += "..."
                formatted_results.append(f"   요약: {snippet_text}")
            if result.content and not result.content.startswith("["):
                try:
                    metadata = {
                        "title": result.title,
                        "url": result.url,
                        "snippet": result.snippet,
                        "query": query,
                    }
                    rag_tool = WebSearchTool()
                    await rag_tool.integrate_with_rag(result.content, metadata)
                except Exception as e:
                    logger.warning(f"[web_search] RAG 저장 실패: {e}")
            formatted_results.append("")

        return "\n".join(formatted_results)

    except PermissionError as e:
        return f"[차단됨] {str(e)}"
    except ImportError as e:
        logger.warning(f"[web_search] WebSearchTool import 실패, 폴백 사용: {e}")
        return _fallback_web_search(query, max_results)
    except Exception as e:
        logger.exception("[web_search] 예상치 못한 오류")
        return f"[오류] 웹 검색 중 예상치 못한 오류가 발생했습니다: {str(e)}"


def _fallback_web_search(query: str, max_results: int) -> str:
    """폴백: 간단한 DuckDuckGo 검색 (기존 구현)."""
    if not requests:
        return "[오류] requests 라이브러리가 설치되지 않았습니다. pip install requests를 실행하세요."
    
    try:
        import urllib.parse
        encoded_query = urllib.parse.quote_plus(query)
        search_url = f"https://api.duckduckgo.com/?q={encoded_query}&format=json&no_html=1&skip_disambig=1"
        
        response = requests.get(search_url, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        results = []
        if data.get("AbstractText"):
            results.append(f"📖 요약: {data['AbstractText']}")
            if data.get("AbstractURL"):
                results.append(f"   출처: {data['AbstractURL']}")
        
        if data.get("RelatedTopics"):
            for idx, topic in enumerate(data["RelatedTopics"][:max_results], 1):
                if isinstance(topic, dict):
                    text = topic.get("Text", "")
                    url = topic.get("FirstURL", "")
                    if text:
                        results.append(f"{idx}. {text}")
                        if url:
                            results.append(f"   URL: {url}")
        
        return "\n".join(results) if results else f"[검색 결과 없음] '{query}'에 대한 결과를 찾을 수 없습니다."
        
    except Exception as e:
        logger.warning(f"[web_search] 폴백 검색 실패: {e}")
        return f"[오류] 웹 검색 중 오류가 발생했습니다: {str(e)}"


@tool(category="analysis")
def analyze_text(text: str, analysis_type: str = "general") -> str:
    """
    텍스트를 분석합니다. 감정 분석, 키워드 추출, 요약, 언어 감지 등을 수행할 수 있습니다.
    
    Args:
        text: 분석할 텍스트
        analysis_type: 분석 유형 (기본값: "general")
            - "general": 일반적인 분석 (키워드, 요약, 감정)
            - "keywords": 키워드 추출만
            - "summary": 요약만
            - "sentiment": 감정 분석만
            - "language": 언어 감지만
    
    Returns:
        분석 결과를 구조화된 문자열로 반환합니다.
    
    Example:
        analyze_text("오늘 날씨가 정말 좋네요!", "sentiment")
        -> "감정 분석 결과: 긍정적 (confidence: 0.85)"
    """
    if not text or not text.strip():
        return "[오류] 분석할 텍스트가 비어있습니다."
    
    text = text.strip()
    results = []
    
    try:
        # 기본 통계
        char_count = len(text)
        word_count = len(text.split())
        line_count = len(text.splitlines())
        
        results.append(f"📊 텍스트 통계:")
        results.append(f"   - 문자 수: {char_count:,}")
        results.append(f"   - 단어 수: {word_count:,}")
        results.append(f"   - 줄 수: {line_count}")
        results.append("")
        
        # 분석 유형별 처리
        if analysis_type in ("general", "keywords"):
            # 간단한 키워드 추출 (한글/영어 단어 추출)
            # 한글 단어 추출
            korean_words = re.findall(r'[가-힣]+', text)
            # 영어 단어 추출
            english_words = re.findall(r'\b[a-zA-Z]{3,}\b', text)
            
            # 빈도수 계산
            korean_freq = Counter(korean_words)
            english_freq = Counter(english_words)
            
            # 상위 키워드
            top_korean = korean_freq.most_common(5)
            top_english = english_freq.most_common(5)
            
            results.append("🔑 주요 키워드:")
            if top_korean:
                results.append("   한글:")
                for word, count in top_korean:
                    results.append(f"     - {word} ({count}회)")
            if top_english:
                results.append("   영어:")
                for word, count in top_english:
                    results.append(f"     - {word} ({count}회)")
            results.append("")
        
        if analysis_type in ("general", "summary"):
            # 간단한 요약 (첫 문장 + 마지막 문장)
            sentences = re.split(r'[.!?]\s+', text)
            sentences = [s.strip() for s in sentences if s.strip()]
            
            if len(sentences) > 2:
                summary = f"{sentences[0]}... {sentences[-1]}"
            elif sentences:
                summary = sentences[0]
            else:
                summary = text[:200] + "..." if len(text) > 200 else text
            
            results.append(f"📝 요약:")
            results.append(f"   {summary}")
            results.append("")
        
        if analysis_type in ("general", "sentiment"):
            # 간단한 감정 분석 (키워드 기반)
            positive_keywords = ["좋", "행복", "기쁨", "만족", "훌륭", "완벽", "좋아", "love", "happy", "good", "great", "excellent"]
            negative_keywords = ["나쁘", "슬픔", "화", "불만", "실망", "싫", "bad", "sad", "angry", "hate", "terrible"]
            
            text_lower = text.lower()
            positive_count = sum(1 for kw in positive_keywords if kw in text_lower)
            negative_count = sum(1 for kw in negative_keywords if kw in text_lower)
            
            if positive_count > negative_count:
                sentiment = "긍정적"
                confidence = min(0.5 + (positive_count - negative_count) * 0.1, 0.95)
            elif negative_count > positive_count:
                sentiment = "부정적"
                confidence = min(0.5 + (negative_count - positive_count) * 0.1, 0.95)
            else:
                sentiment = "중립적"
                confidence = 0.5
            
            results.append(f"😊 감정 분석:")
            results.append(f"   - 감정: {sentiment}")
            results.append(f"   - 신뢰도: {confidence:.2f}")
            results.append("")
        
        if analysis_type in ("general", "language"):
            # 언어 감지 (간단한 휴리스틱)
            korean_chars = len(re.findall(r'[가-힣]', text))
            english_chars = len(re.findall(r'[a-zA-Z]', text))
            total_chars = len(re.sub(r'[^\w\s가-힣]', '', text))
            
            if total_chars == 0:
                detected_lang = "알 수 없음"
            elif korean_chars > english_chars * 2:
                detected_lang = "한국어"
            elif english_chars > korean_chars * 2:
                detected_lang = "영어"
            else:
                detected_lang = "혼합 (한국어/영어)"
            
            results.append(f"🌐 언어 감지:")
            results.append(f"   - 언어: {detected_lang}")
            results.append(f"   - 한글 비율: {korean_chars/total_chars*100:.1f}%" if total_chars > 0 else "")
            results.append(f"   - 영문 비율: {english_chars/total_chars*100:.1f}%" if total_chars > 0 else "")
        
        return "\n".join(results)
        
    except Exception as e:
        logger.exception("[analyze_text] 분석 중 오류 발생")
        return f"[오류] 텍스트 분석 중 예상치 못한 오류가 발생했습니다: {str(e)}"
