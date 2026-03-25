"""
Complexity Evaluator - 적응형 ReAct 턴 제한 계산

입력의 복잡도를 분석하여 적절한 턴 수를 동적으로 할당합니다.
고정된 10턴 제한 대신, 작업의 난이도에 따라 유연하게 조정합니다.
"""

import logging
from typing import Optional, List

logger = logging.getLogger(__name__)


# =============================================================================
# Complexity Evaluator
# =============================================================================

class ComplexityEvaluator:
    """
    작업 복잡도 평가 및 턴 제한 계산기.
    
    입력 길이, 키워드, 도구 개수 등을 분석하여 적절한 턴 수를 결정합니다.
    """

    # 기본 설정
    BASE_TURNS: int = 5  # 기본 턴 수
    MAX_TURNS: int = 30  # 최대 턴 수
    TURNS_PER_100_CHARS: int = 2  # 100자마다 추가 턴
    TURNS_PER_COMPLEX_KEYWORD: int = 3  # 복합 작업 키워드당 추가 턴
    
    # 복합 작업 키워드 (한글/영문)
    COMPLEX_KEYWORDS: List[str] = [
        # 한글 키워드
        "분석", "설계", "구현", "검색", "개발", "생성", "작성", "수정",
        "변환", "처리", "계산", "평가", "비교", "정렬", "필터링",
        "통합", "연결", "추출", "파싱", "검증", "테스트", "디버깅",
        # 영문 키워드
        "analyze", "design", "implement", "search", "develop", "create",
        "generate", "write", "modify", "convert", "process", "calculate",
        "evaluate", "compare", "sort", "filter", "integrate", "connect",
        "extract", "parse", "validate", "test", "debug",
    ]

    def __init__(
        self,
        base_turns: int = BASE_TURNS,
        max_turns: int = MAX_TURNS,
        turns_per_100_chars: int = TURNS_PER_100_CHARS,
        turns_per_keyword: int = TURNS_PER_COMPLEX_KEYWORD,
    ):
        """
        Args:
            base_turns: 기본 턴 수
            max_turns: 최대 턴 수
            turns_per_100_chars: 100자마다 추가 턴
            turns_per_keyword: 복합 키워드당 추가 턴
        """
        self.base_turns = base_turns
        self.max_turns = max_turns
        self.turns_per_100_chars = turns_per_100_chars
        self.turns_per_keyword = turns_per_keyword

    def calculate_limit(
        self,
        user_input: str,
        available_tools_count: int = 0,
        past_failure_bonus: int = 0
    ) -> int:
        """
        작업 복잡도를 기반으로 턴 제한을 계산합니다.
        
        평가 기준:
        1. 기본 턴: base_turns
        2. 입력 길이: 100자마다 +turns_per_100_chars
        3. 복합 키워드: 개당 +turns_per_keyword
        4. 도구 개수: 도구가 많을수록 탐색 범위 증가 (가중치 적용)
        5. 과거 실패 보너스: past_failure_bonus (선택사항)
        
        Args:
            user_input: 사용자 입력 텍스트
            available_tools_count: 사용 가능한 도구 개수
            past_failure_bonus: 과거 실패 기록 기반 추가 턴 (기본 0)
            
        Returns:
            계산된 턴 제한 (최대 max_turns)
        """
        # 1. 기본 턴
        turns = self.base_turns
        
        # 2. 입력 길이 기반 추가 턴
        input_length = len(user_input)
        length_bonus = (input_length // 100) * self.turns_per_100_chars
        turns += length_bonus
        
        # 3. 복합 키워드 검색 및 가중치 적용
        keyword_count = self._count_complex_keywords(user_input)
        keyword_bonus = keyword_count * self.turns_per_keyword
        turns += keyword_bonus
        
        # 4. 도구 개수 가중치
        # 도구가 많을수록 탐색 범위가 넓어지므로 추가 턴 필요
        # 도구가 10개 이상이면 +2턴, 20개 이상이면 +4턴
        if available_tools_count >= 20:
            turns += 4
        elif available_tools_count >= 10:
            turns += 2
        elif available_tools_count >= 5:
            turns += 1
        
        # 5. 과거 실패 기록 보너스
        turns += past_failure_bonus
        
        # 최대 제한 적용
        final_turns = min(turns, self.max_turns)
        
        logger.debug(
            f"[ComplexityEvaluator] Calculated turns: {final_turns} "
            f"(base={self.base_turns}, length_bonus={length_bonus}, "
            f"keyword_bonus={keyword_bonus}, tools={available_tools_count}, "
            f"failure_bonus={past_failure_bonus})"
        )
        
        return final_turns

    def _count_complex_keywords(self, text: str) -> int:
        """
        텍스트에서 복합 작업 키워드 개수를 세어 반환합니다.
        
        Args:
            text: 검색할 텍스트
            
        Returns:
            발견된 키워드 개수 (중복 제거)
        """
        text_lower = text.lower()
        found_keywords = set()
        
        for keyword in self.COMPLEX_KEYWORDS:
            if keyword.lower() in text_lower:
                found_keywords.add(keyword.lower())
        
        return len(found_keywords)

    def evaluate_complexity_level(self, user_input: str) -> str:
        """
        복잡도 레벨을 평가합니다 (디버깅/로깅용).
        
        Args:
            user_input: 사용자 입력
            
        Returns:
            복잡도 레벨 ("low", "medium", "high", "very_high")
        """
        turns = self.calculate_limit(user_input)
        
        if turns <= 8:
            return "low"
        elif turns <= 15:
            return "medium"
        elif turns <= 25:
            return "high"
        else:
            return "very_high"


# =============================================================================
# 싱글톤 인스턴스
# =============================================================================

_evaluator_instance: Optional[ComplexityEvaluator] = None


def get_complexity_evaluator() -> ComplexityEvaluator:
    """
    ComplexityEvaluator 싱글톤 인스턴스 반환.
    
    Returns:
        ComplexityEvaluator 인스턴스
    """
    global _evaluator_instance
    if _evaluator_instance is None:
        _evaluator_instance = ComplexityEvaluator()
    return _evaluator_instance
