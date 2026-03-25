"""
Goal Planner - 목표 트리 분해 계획자

LLM을 사용하여 사용자 입력을 분석하고, 이를 실행 가능한 하위 목표들로 분해합니다.
복잡한 작업을 계층 구조의 작은 단위로 나누어 관리할 수 있도록 합니다.
"""

import json
import logging
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)


# =============================================================================
# Goal Planner
# =============================================================================

class GoalPlanner:
    """
    목표 분해 계획자.
    
    LLM을 활용하여 사용자 입력을 분석하고, 실행 가능한 하위 목표들로 분해합니다.
    """

    def __init__(self, llm_service: Optional[Any] = None):
        """
        Args:
            llm_service: LLM 서비스 인스턴스 (None이면 plan() 호출 시 주입 필요)
        """
        self.llm_service = llm_service
        logger.info("[GoalPlanner] Initialized")

    async def plan(
        self,
        user_input: str,
        llm_service: Optional[Any] = None
    ) -> List[Dict[str, Any]]:
        """
        사용자 입력을 분석하여 하위 목표들로 분해.
        
        Args:
            user_input: 사용자 입력 텍스트
            llm_service: LLM 서비스 인스턴스 (None이면 self.llm_service 사용)
            
        Returns:
            하위 목표 리스트 (각 목표는 title, description, priority 포함)
        """
        llm = llm_service or self.llm_service
        if not llm:
            raise ValueError("LLM service is required for goal planning")

        try:
            # LLM 프롬프트 구성
            prompt = f"""다음 사용자 요청을 분석하여 실행 가능한 하위 목표들로 분해해주세요.

사용자 요청: "{user_input}"

요구사항:
1. 전체 작업을 3~5개의 구체적이고 실행 가능한(Actionable) 하위 목표로 나누세요.
2. 각 하위 목표는 독립적으로 수행 가능해야 합니다.
3. 우선순위(priority)를 1~10 사이의 숫자로 부여하세요 (높을수록 우선순위 높음).
4. 각 목표는 명확하고 측정 가능해야 합니다.

출력 형식 (JSON 배열):
[
  {{
    "title": "목표의 핵심 요약 (한 줄)",
    "description": "상세 수행 내용 (구체적으로)",
    "priority": 8
  }},
  ...
]

JSON만 출력하고 다른 설명은 포함하지 마세요."""

            # LLM 호출
            if hasattr(llm, 'generate'):
                result = await llm.generate(
                    prompt=prompt,
                    mode="thinking",
                    max_tokens=800,
                    temperature=0.7
                )
                response_text = result.content if hasattr(result, 'content') else str(result)
            elif hasattr(llm, 'chat'):
                messages = [
                    {"role": "system", "content": "당신은 작업 분해 전문가입니다. 사용자 요청을 실행 가능한 하위 목표로 나누는 것이 전문입니다."},
                    {"role": "user", "content": prompt}
                ]
                response = await llm.chat(messages=messages)
                response_text = response.text if hasattr(response, 'text') else str(response)
            else:
                raise ValueError("LLM service must have generate() or chat() method")

            # JSON 파싱
            goals = self._parse_goals(response_text)
            
            # 검증 및 정규화
            goals = self._validate_and_normalize(goals)
            
            logger.info(f"[GoalPlanner] Planned {len(goals)} sub-goals from: {user_input[:50]}...")
            return goals
            
        except Exception as e:
            logger.error(f"[GoalPlanner] Failed to plan goals: {e}")
            # 폴백: 간단한 단일 목표 반환
            return [{
                "title": user_input[:100],
                "description": user_input,
                "priority": 5
            }]

    def _parse_goals(self, response_text: str) -> List[Dict[str, Any]]:
        """
        LLM 응답에서 목표 리스트 파싱.
        
        Args:
            response_text: LLM 응답 텍스트
            
        Returns:
            목표 딕셔너리 리스트
        """
        try:
            # JSON 블록 추출
            text = response_text.strip()
            
            # 코드 블록 제거
            if "```json" in text:
                start = text.find("```json") + 7
                end = text.find("```", start)
                text = text[start:end].strip()
            elif "```" in text:
                start = text.find("```") + 3
                end = text.find("```", start)
                text = text[start:end].strip()
            
            # JSON 파싱
            goals = json.loads(text)
            
            if not isinstance(goals, list):
                goals = [goals]
            
            return goals
            
        except json.JSONDecodeError as e:
            logger.error(f"[GoalPlanner] JSON parse error: {e}")
            # 폴백: 간단한 파싱 시도
            return self._fallback_parse(response_text)

    def _fallback_parse(self, text: str) -> List[Dict[str, Any]]:
        """
        JSON 파싱 실패 시 폴백 파싱.
        
        Args:
            text: 응답 텍스트
            
        Returns:
            목표 딕셔너리 리스트
        """
        goals = []
        lines = text.split('\n')
        current_goal = {}
        
        for line in lines:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            
            if 'title' in line.lower() or '제목' in line:
                if current_goal:
                    goals.append(current_goal)
                current_goal = {"title": line.split(':', 1)[-1].strip(), "priority": 5}
            elif 'description' in line.lower() or '설명' in line:
                current_goal["description"] = line.split(':', 1)[-1].strip()
            elif 'priority' in line.lower() or '우선순위' in line:
                try:
                    current_goal["priority"] = int(line.split(':')[-1].strip())
                except ValueError:
                    current_goal["priority"] = 5
        
        if current_goal:
            goals.append(current_goal)
        
        return goals if goals else [{"title": "작업 수행", "description": text[:200], "priority": 5}]

    def _validate_and_normalize(self, goals: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        목표 리스트 검증 및 정규화.
        
        Args:
            goals: 원본 목표 리스트
            
        Returns:
            검증 및 정규화된 목표 리스트
        """
        normalized = []
        
        for i, goal in enumerate(goals):
            # 필수 필드 확인
            title = goal.get("title", f"목표 {i+1}")
            description = goal.get("description", title)
            priority = goal.get("priority", 5)
            
            # 우선순위 범위 제한 (1~10)
            priority = max(1, min(10, int(priority) if isinstance(priority, (int, float)) else 5))
            
            # 문자열 정리
            title = str(title).strip()[:200]
            description = str(description).strip()[:1000]
            
            normalized.append({
                "title": title,
                "description": description,
                "priority": priority
            })
        
        # 우선순위 순으로 정렬 (높은 순)
        normalized.sort(key=lambda x: x["priority"], reverse=True)
        
        return normalized


# =============================================================================
# 싱글톤 인스턴스
# =============================================================================

_planner_instance: Optional[GoalPlanner] = None


def get_goal_planner(llm_service: Optional[Any] = None) -> GoalPlanner:
    """
    GoalPlanner 싱글톤 인스턴스 반환.
    
    Args:
        llm_service: LLM 서비스 인스턴스 (첫 호출 시에만 적용)
        
    Returns:
        GoalPlanner 인스턴스
    """
    global _planner_instance
    if _planner_instance is None:
        _planner_instance = GoalPlanner(llm_service=llm_service)
    return _planner_instance
