"""
Goal Manager - 목표 트리 관리자

목표 트리의 생성, 조회, 상태 관리를 담당합니다.
복잡한 작업을 계층 구조로 관리하고, 실행 가능한 목표를 추적합니다.
"""

import json
import uuid
import logging
from typing import Optional, List, Any, Dict
from datetime import datetime

from mellow_link.infra.memory_database import (
    MemoryDatabase,
    GoalRecord,
    get_memory_db,
)
from mellow_link.core.goal_planner import GoalPlanner, get_goal_planner

logger = logging.getLogger(__name__)


# =============================================================================
# Goal Manager
# =============================================================================

class GoalManager:
    """
    목표 트리 관리자.
    
    목표 트리의 생성, 조회, 상태 업데이트를 담당합니다.
    """

    def __init__(
        self,
        db: Optional[MemoryDatabase] = None,
        planner: Optional[GoalPlanner] = None
    ):
        """
        Args:
            db: MemoryDatabase 인스턴스 (None이면 싱글톤 사용)
            planner: GoalPlanner 인스턴스 (None이면 싱글톤 사용)
        """
        self.db = db or get_memory_db()
        self.planner = planner or get_goal_planner()
        logger.info("[GoalManager] Initialized")

    async def create_goal_tree(
        self,
        root_intent: str,
        llm_service: Optional[Any] = None
    ) -> str:
        """
        루트 의도로부터 목표 트리 생성.
        
        GoalPlanner를 호출하여 하위 목표들을 생성하고 DB에 저장합니다.
        
        Args:
            root_intent: 루트 목표 의도
            llm_service: LLM 서비스 인스턴스 (GoalPlanner에 전달)
            
        Returns:
            루트 목표 ID
        """
        try:
            # 루트 목표 생성
            root_id = str(uuid.uuid4())
            root_goal = GoalRecord(
                id=root_id,
                parent_id=None,
                title=root_intent[:200],
                description=root_intent,
                priority=10,  # 루트는 최고 우선순위
                status="TO_DO",
                depth=0,
                created_at=datetime.now(),
                updated_at=datetime.now()
            )
            
            if not self.db.save_goal(root_goal):
                raise RuntimeError("Failed to save root goal")
            
            # 하위 목표 계획
            sub_goals_data = await self.planner.plan(root_intent, llm_service=llm_service)
            
            # 하위 목표 저장
            for sub_goal_data in sub_goals_data:
                sub_goal_id = str(uuid.uuid4())
                sub_goal = GoalRecord(
                    id=sub_goal_id,
                    parent_id=root_id,
                    title=sub_goal_data["title"],
                    description=sub_goal_data["description"],
                    priority=sub_goal_data["priority"],
                    status="TO_DO",
                    depth=1,
                    created_at=datetime.now(),
                    updated_at=datetime.now()
                )
                
                if not self.db.save_goal(sub_goal):
                    logger.warning(f"[GoalManager] Failed to save sub-goal: {sub_goal_id}")
            
            logger.info(
                f"[GoalManager] Goal tree created: root={root_id}, "
                f"sub-goals={len(sub_goals_data)}"
            )
            
            return root_id
            
        except Exception as e:
            logger.error(f"[GoalManager] Failed to create goal tree: {e}")
            raise

    def get_next_executable_goal(self) -> Optional[GoalRecord]:
        """
        실행 가능한 다음 목표 조회.
        
        TO_DO 상태인 목표 중 우선순위가 높고 의존성이 해결된 목표를 반환합니다.
        (현재는 단순히 우선순위 기반, 추후 의존성 체크 확장 가능)
        
        Returns:
            실행 가능한 GoalRecord 또는 None
        """
        try:
            executable_goals = self.db.get_executable_goals(limit=1)
            
            if not executable_goals:
                return None
            
            goal = executable_goals[0]
            
            # 의존성 체크: 부모 목표가 완료되었는지 확인
            if goal.parent_id:
                parent = self.db.get_goal(goal.parent_id)
                if parent and parent.status not in ("DONE", "FAILED"):
                    # 부모가 완료되지 않았으면, 부모의 다른 자식들이 모두 완료되었는지 확인
                    siblings = self.db.get_children_goals(goal.parent_id)
                    incomplete_siblings = [
                        s for s in siblings
                        if s.id != goal.id and s.status not in ("DONE", "FAILED")
                    ]
                    
                    # 다른 미완료 형제가 있으면 이 목표는 아직 실행 불가
                    if incomplete_siblings:
                        # 더 높은 우선순위의 형제가 있는지 확인
                        higher_priority_siblings = [
                            s for s in incomplete_siblings
                            if s.priority > goal.priority
                        ]
                        if higher_priority_siblings:
                            return None
            
            logger.debug(f"[GoalManager] Next executable goal: {goal.id} ({goal.title})")
            return goal
            
        except Exception as e:
            logger.error(f"[GoalManager] Failed to get next executable goal: {e}")
            return None

    def get_active_goals(self, limit: int = 20) -> List[GoalRecord]:
        """
        ✅ verified: 미완료 활성 목표 목록 (EvolutionTrigger 주입용).
        TO_DO, IN_PROGRESS인 목표를 우선순위 순으로 반환.
        """
        try:
            return self.db.get_active_goals(limit=limit)
        except Exception as e:
            logger.error(f"[GoalManager] Failed to get active goals: {e}")
            return []

    async def generate_subgoals_from_insights(
        self,
        parent_goal_id: str,
        *,
        insight_limit: int = 10,
        min_confidence: float = 0.5,
        days_threshold: int = 14,
        max_subgoals: int = 5,
        llm_service: Optional[Any] = None,
    ) -> List[GoalRecord]:
        """
        ✅ verified: 상위 목표와 LogAnalyzer 인사이트를 비교하여 시급한 개선 포인트를 하위 목표로 자동 등록.

        behavior_insights의 분석 결과와 사용자가 설정한 광범위한 상위 목표를 대조하고,
        현재 가장 시급한 개선 포인트를 구체적인 하위 목표(Sub-goal)로 DB에 등록한다.

        Args:
            parent_goal_id: 상위 목표(루트 또는 부모)의 ID.
            insight_limit: 참고할 인사이트 최대 개수.
            min_confidence: 인사이트 최소 신뢰도 (0.0~1.0).
            days_threshold: 최근 N일 이내 인사이트만 사용.
            max_subgoals: 생성할 하위 목표 최대 개수.
            llm_service: LLM 서비스 (None이면 규칙 기반으로 인사이트→하위목표 매핑).

        Returns:
            생성된 하위 목표 GoalRecord 리스트.
        """
        created: List[GoalRecord] = []
        try:
            parent = self.db.get_goal(parent_goal_id)
            if not parent:
                logger.warning("[GoalManager] generate_subgoals_from_insights: parent goal not found")
                return created

            insights = self.db.get_recent_insights(
                limit=insight_limit,
                min_confidence=min_confidence,
                days_threshold=days_threshold,
                prefer_verified=True,
            )
            if not insights:
                logger.debug("[GoalManager] No insights to generate subgoals from")
                return created

            parent_depth = getattr(parent, "depth", 0)
            sub_goals_data: List[Dict[str, Any]] = []

            llm = llm_service or getattr(self.planner, "llm_service", None)
            if llm:
                sub_goals_data = await self._subgoals_from_insights_via_llm(
                    parent=parent,
                    insights=insights,
                    llm=llm,
                    max_subgoals=max_subgoals,
                )
            else:
                sub_goals_data = self._subgoals_from_insights_rule_based(
                    insights=insights,
                    max_subgoals=max_subgoals,
                )

            for sg in sub_goals_data[:max_subgoals]:
                sub_id = str(uuid.uuid4())
                sub_goal = GoalRecord(
                    id=sub_id,
                    parent_id=parent_goal_id,
                    title=(sg.get("title") or "하위 목표")[:200],
                    description=(sg.get("description") or "")[:2000],
                    priority=max(1, min(10, int(sg.get("priority", 5)))),
                    status="TO_DO",
                    depth=parent_depth + 1,
                    created_at=datetime.now(),
                    updated_at=datetime.now(),
                )
                if self.db.save_goal(sub_goal):
                    created.append(sub_goal)
                    logger.info("[GoalManager] Sub-goal from insights: %s", sub_goal.title[:80])
                else:
                    logger.warning("[GoalManager] Failed to save sub-goal: %s", sub_id)

            return created
        except Exception as e:
            logger.error("[GoalManager] generate_subgoals_from_insights failed: %s", e)
            return created

    async def _subgoals_from_insights_via_llm(
        self,
        parent: GoalRecord,
        insights: List[Any],
        llm: Any,
        max_subgoals: int,
    ) -> List[Dict[str, Any]]:
        """상위 목표 + 인사이트를 LLM에 넘겨 시급한 개선 포인트를 하위 목표로 분해."""
        insight_lines = []
        for i, ins in enumerate(insights[:10], 1):
            insight_lines.append(
                f"  {i}. [신뢰도 {getattr(ins, 'confidence', 0):.2f}] {getattr(ins, 'finding', '')[:150]}\n"
                f"     권고: {getattr(ins, 'recommendation', '')[:200]}"
            )
        insights_text = "\n".join(insight_lines)

        prompt = f"""너는 목표 분해 전문가다. 사용자가 설정한 '상위 목표'와 LogAnalyzer가 뽑아낸 '인사이트'를 비교하여, 현재 가장 시급한 개선 포인트를 구체적인 하위 목표로 정리하라.

## 상위 목표
- 제목: {parent.title}
- 설명: {parent.description}

## 최근 인사이트 (행동 로그 분석 결과)
{insights_text}

## 요청
1. 상위 목표 달성에 도움이 되고, 인사이트에서 지적한 문제를 해결하는 하위 목표 3~{max_subgoals}개를 제안하라.
2. 시급성·영향도가 큰 순으로 우선순위(priority)를 1~10으로 부여하라.
3. 각 하위 목표는 한 문장으로 구체적이고 실행 가능하게 작성하라.

## 출력 (JSON 배열만)
[
  {{"title": "목표 요약", "description": "구체적 수행 내용", "priority": 8}},
  ...
]
JSON만 출력하라."""

        try:
            if hasattr(llm, "generate"):
                result = await llm.generate(prompt=prompt, mode="thinking", max_tokens=1000, temperature=0.5)
                response_text = result.content if hasattr(result, "content") else str(result)
            elif hasattr(llm, "chat"):
                messages = [
                    {"role": "system", "content": "당신은 목표 분해 전문가입니다. JSON 배열만 출력합니다."},
                    {"role": "user", "content": prompt},
                ]
                response = await llm.chat(messages=messages)
                response_text = response.text if hasattr(response, "text") else str(response)
            else:
                return []

            raw = response_text.strip()
            if "```json" in raw:
                raw = raw.split("```json")[1].split("```")[0].strip()
            elif "```" in raw:
                raw = raw.split("```")[1].split("```")[0].strip()
            data = json.loads(raw)
            if isinstance(data, list):
                return [x for x in data if isinstance(x, dict) and (x.get("title") or x.get("description"))]
            return []
        except Exception as e:
            logger.debug("[GoalManager] LLM subgoals from insights failed: %s", e)
            return []

    def _subgoals_from_insights_rule_based(
        self,
        insights: List[Any],
        max_subgoals: int,
    ) -> List[Dict[str, Any]]:
        """LLM 없이 인사이트를 그대로 하위 목표 후보로 변환 (신뢰도·finding 기반)."""
        out = []
        for ins in sorted(insights, key=lambda x: getattr(x, "confidence", 0), reverse=True)[:max_subgoals]:
            finding = (getattr(ins, "finding", "") or "").strip()
            rec = (getattr(ins, "recommendation", "") or "").strip()
            conf = getattr(ins, "confidence", 0.5)
            if not finding and not rec:
                continue
            title = finding[:200] if finding else (rec[:200] if rec else "인사이트 기반 개선")
            description = rec[:2000] if rec else finding[:2000]
            priority = max(1, min(10, int(conf * 10)))
            out.append({"title": title, "description": description, "priority": priority})
        return out

    async def generate_goals_from_insights(
        self,
        parent_goal_id: Optional[str] = None,
        *,
        insight_limit: int = 15,
        min_confidence: float = 0.5,
        days_threshold: int = 14,
        max_goals: int = 5,
        send_notification: bool = True,
    ) -> List[GoalRecord]:
        """
        ✅ verified: 미처리 behavior_insights → Tower 판별 → SMART 목표 생성 후 GoalManager 등록.

        아직 처리되지 않은 최신 인사이트를 Tower에 전달하여, 안정성·성능·UX를 저해하는
        핵심 요소를 식별하고 구체적·측정 가능한 목표(SMART)를 생성한 뒤 parent_goal_id와
        연결해 TO_DO로 저장. active_goals와의 중복은 간단 체크로 방지.
        """
        created: List[GoalRecord] = []
        try:
            parent_id = await self._resolve_parent_goal_for_insights(parent_goal_id)
            if not parent_id:
                logger.warning("[GoalManager] generate_goals_from_insights: no parent goal")
                return created

            # ✅ W1: atomic claim으로 동시 워커 중복 처리 방지
            unprocessed = self.db.claim_unprocessed_insights(
                limit=insight_limit,
                min_confidence=min_confidence,
                days_threshold=days_threshold,
            )
            if not unprocessed:
                logger.debug("[GoalManager] No unprocessed insights for goal generation")
                return created

            goals_data = await self._tower_smart_goals_from_insights(unprocessed, max_goals=max_goals)
            if not goals_data:
                return created

            parent = self.db.get_goal(parent_id)
            parent_depth = getattr(parent, "depth", 0) if parent else 0
            active = self.db.get_active_goals(limit=100)

            for g in goals_data[:max_goals]:
                title = (g.get("title") or "").strip()
                if not title:
                    continue
                if self._is_duplicate_of_active_goal(title, active):
                    logger.debug("[GoalManager] Skip duplicate goal: %s", title[:60])
                    continue
                goal_id = str(uuid.uuid4())
                record = GoalRecord(
                    id=goal_id,
                    parent_id=parent_id,
                    title=title[:200],
                    description=(g.get("description") or "")[:2000],
                    priority=max(1, min(10, int(g.get("priority", 5)))),
                    status="TO_DO",
                    depth=parent_depth + 1,
                    created_at=datetime.now(),
                    updated_at=datetime.now(),
                )
                if self.db.save_goal(record):
                    created.append(record)
                    logger.info("[GoalManager] Goal from insights registered: %s", record.title[:80])
                    if send_notification:
                        self._notify_goal_registered(record.title)
                else:
                    logger.warning("[GoalManager] Failed to save goal: %s", goal_id)

            return created
        except Exception as e:
            logger.error("[GoalManager] generate_goals_from_insights failed: %s", e)
            return created

    async def _resolve_parent_goal_for_insights(self, parent_goal_id: Optional[str]) -> Optional[str]:
        """parent_goal_id가 있으면 검증 후 반환, 없으면 TO_DO/IN_PROGRESS 루트 1개 반환 또는 '자율 개선' 루트 생성."""
        if parent_goal_id:
            p = self.db.get_goal(parent_goal_id)
            return parent_goal_id if p else None
        roots = self.db.get_all_goals_by_status(status=None)
        roots = [r for r in roots if getattr(r, "parent_id", None) is None]
        for r in roots:
            if r.status in ("TO_DO", "IN_PROGRESS"):
                return r.id
        root_id = str(uuid.uuid4())
        root = GoalRecord(
            id=root_id,
            parent_id=None,
            title="자율 개선",
            description="LogAnalyzer 인사이트 기반 자동 생성 목표의 상위",
            priority=10,
            status="TO_DO",
            depth=0,
            created_at=datetime.now(),
            updated_at=datetime.now(),
        )
        if self.db.save_goal(root):
            return root_id
        return None

    def _is_duplicate_of_active_goal(self, title: str, active_goals: List[GoalRecord]) -> bool:
        """✅ verified: active_goals에 유사 목표가 있으면 True (중복 방지)."""
        t = (title or "").strip().lower()
        if not t:
            return True
        for g in active_goals or []:
            gt = (getattr(g, "title", "") or "").strip().lower()
            if gt == t:
                return True
            if len(t) >= 10 and (t in gt or gt in t):
                return True
        return False

    async def _tower_smart_goals_from_insights(
        self,
        insights: List[Any],
        max_goals: int = 5,
    ) -> List[Dict[str, Any]]:
        """Tower(LLM)에 인사이트 전달 → 안정성·성능·UX 저해 요소 식별 및 SMART 목표 생성."""
        insight_lines = []
        for i, ins in enumerate(insights[:15], 1):
            insight_lines.append(
                f"  {i}. [신뢰도 {getattr(ins, 'confidence', 0):.2f}] {getattr(ins, 'finding', '')[:200]}\n"
                f"     권고: {getattr(ins, 'recommendation', '')[:300]}"
            )
        insights_text = "\n".join(insight_lines)

        prompt = f"""너는 시스템 관제탑(Tower)이다. 현재 인사이트 중 시스템의 안정성, 성능, 사용자 경험을 저해하는 핵심 요소를 식별하고, 이를 해결하기 위한 구체적이고 측정 가능한 목표(SMART 목표)를 생성하라.

## 최근 미처리 인사이트 (behavior_insights)
{insights_text}

## 요청
1. 위 인사이트에서 안정성·성능·UX를 해치는 핵심 이슈를 골라라.
2. 각 이슈에 대해 SMART 목표(구체적·측정가능·달성가능·관련성·기한) 1개씩, 최대 {max_goals}개 제안하라.
3. priority는 1~10 (높을수록 시급).

## 출력 (JSON 배열만)
[
  {{"title": "목표 한 줄 요약", "description": "SMART 상세", "priority": 8}},
  ...
]
JSON만 출력하라."""

        try:
            from mellow_link.core.provider_factory import get_client, generate_async
            tower_cfg = get_client("google", role="tower")
            raw = await generate_async(
                tower_cfg.provider, tower_cfg.model, prompt, tower_cfg.api_key
            )
            text = (raw or "").strip()
            if "```json" in text:
                text = text.split("```json")[1].split("```")[0].strip()
            elif "```" in text:
                text = text.split("```")[1].split("```")[0].strip()
            data = json.loads(text)
            if isinstance(data, list):
                return [x for x in data if isinstance(x, dict) and (x.get("title") or x.get("description"))]
            return []
        except Exception as e:
            logger.debug("[GoalManager] Tower SMART goals from insights failed: %s", e)
            return []

    def _notify_goal_registered(self, goal_title: str) -> None:
        """🔔 Optional: 자율 목표 등록 시 알림 전송."""
        try:
            from mellow_link.services.notification_service import notify_autonomous_goal_registered
            notify_autonomous_goal_registered(goal_title)
        except Exception as e:
            logger.debug("[GoalManager] Goal registered notify skipped: %s", e)

    def update_goal_status(
        self,
        goal_id: str,
        status: str
    ) -> bool:
        """
        목표 상태 업데이트.
        
        목표 완료 시 상태를 업데이트하고, 모든 하위 목표가 완료되면 상위 목표도 DONE으로 갱신합니다.
        
        Args:
            goal_id: 목표 ID
            status: 새 상태 (TO_DO, IN_PROGRESS, DONE, FAILED)
            
        Returns:
            업데이트 성공 여부
        """
        try:
            # 상태 업데이트
            if not self.db.update_goal_status(goal_id, status):
                return False
            
            goal = self.db.get_goal(goal_id)
            if not goal:
                return False
            
            # 목표 상태 변경 시 부모 상태 확인 (DONE 또는 FAILED)
            if status in ("DONE", "FAILED") and goal.parent_id:
                self._check_and_update_parent(goal.parent_id, visited=set())
            
            logger.info(f"[GoalManager] Goal status updated: {goal_id} -> {status}")
            return True
            
        except Exception as e:
            logger.error(f"[GoalManager] Failed to update goal status: {e}")
            return False

    def _check_and_update_parent(
        self,
        parent_id: str,
        visited: Optional[set] = None
    ) -> None:
        """
        부모 목표의 모든 자식 상태를 확인하고, 상태를 전파합니다.
        
        규칙:
        1. 자식 중 하나라도 FAILED가 있으면 부모도 FAILED로 전파
        2. 모든 자식이 DONE이면 부모도 DONE으로 업데이트
        3. 순환 참조 방지를 위해 visited 세트 사용
        
        기술 검토:
        - 재귀 호출 최적화: ✅ verified (단일 경로 탐색이므로 visited 복사 불필요)
        - 순환 참조 방어 유지: ✅ verified (동일 visited 참조로 정상 감지)
        
        Args:
            parent_id: 부모 목표 ID
            visited: 방문한 목표 ID 세트 (순환 참조 방지용)
        """
        if visited is None:
            visited = set()
        
        # 순환 참조 감지
        if parent_id in visited:
            logger.warning(f"[GoalManager] Circular reference detected: {parent_id}")
            return
        
        visited.add(parent_id)
        
        try:
            children = self.db.get_children_goals(parent_id)
            
            if not children:
                return
            
            parent = self.db.get_goal(parent_id)
            if not parent:
                return
            
            # Fail-Fast 정책: 자식 중 하나라도 FAILED가 있으면 즉시 부모를 FAILED로 마킹
            # (다른 자식들의 상태와 관계없이 즉시 전파)
            failed_children = [child for child in children if child.status == "FAILED"]
            
            if failed_children:
                # Fail-Fast: 자식 중 하나라도 FAILED가 있으면 즉시 부모도 FAILED로 전파
                # 다른 자식이 아직 진행 중이어도 실패한 자식이 있으면 부모는 실패로 간주
                if parent.status != "FAILED":
                    self.db.update_goal_status(parent_id, "FAILED")
                    logger.info(
                        f"[GoalManager] Fail-Fast: Parent goal {parent_id} marked as FAILED "
                        f"due to {len(failed_children)} failed child(ren)"
                    )
                    
                    # 재귀적으로 상위 부모도 즉시 FAILED 전파 (Fail-Fast 체인)
                    if parent.parent_id:
                        self._check_and_update_parent(parent.parent_id, visited)
                return  # Fail-Fast: FAILED가 있으면 DONE 체크는 하지 않음
            else:
                # 모든 자식이 완료되었는지 확인 (DONE 또는 FAILED)
                all_completed = all(
                    child.status in ("DONE", "FAILED")
                    for child in children
                )
                
                if all_completed:
                    # 모든 자식이 완료되었으면 부모도 DONE으로 업데이트
                    if parent.status != "DONE":
                        self.db.update_goal_status(parent_id, "DONE")
                        logger.info(f"[GoalManager] Parent goal completed: {parent_id}")
                        
                        # 재귀적으로 상위 부모도 확인
                        if parent.parent_id:
                            self._check_and_update_parent(parent.parent_id, visited)
                        
        except Exception as e:
            logger.error(f"[GoalManager] Failed to check parent goal: {e}")

    def get_goal_tree(self, root_id: str) -> Optional[Dict[str, Any]]:
        """
        목표 트리 전체 구조 조회 (재귀적).
        
        Args:
            root_id: 루트 목표 ID
            
        Returns:
            트리 구조 딕셔너리 또는 None
        """
        try:
            root = self.db.get_goal(root_id)
            if not root:
                return None
            
            return self._build_tree_node(root)
            
        except Exception as e:
            logger.error(f"[GoalManager] Failed to get goal tree: {e}")
            return None

    def _build_tree_node(self, goal: GoalRecord) -> Dict[str, Any]:
        """
        목표 노드와 그 자식들을 재귀적으로 구성.
        
        Args:
            goal: 목표 레코드
            
        Returns:
            트리 노드 딕셔너리
        """
        children = self.db.get_children_goals(goal.id)
        children_data = [self._build_tree_node(child) for child in children]
        
        return {
            "id": goal.id,
            "title": goal.title,
            "description": goal.description,
            "priority": goal.priority,
            "status": goal.status,
            "depth": goal.depth,
            "children": children_data
        }

    def get_all_goals_by_status(self, status: str) -> List[GoalRecord]:
        """
        특정 상태의 모든 목표 조회 (최적화된 단일 쿼리 사용).
        
        Args:
            status: 목표 상태
            
        Returns:
            목표 레코드 리스트
        """
        try:
            # MemoryDatabase의 최적화된 메서드 사용 (N+1 쿼리 방지)
            return self.db.get_all_goals_by_status(status)
            
        except Exception as e:
            logger.error(f"[GoalManager] Failed to get goals by status: {e}")
            return []


# =============================================================================
# 싱글톤 인스턴스
# =============================================================================

_manager_instance: Optional[GoalManager] = None


def get_goal_manager(
    db: Optional[MemoryDatabase] = None,
    planner: Optional[GoalPlanner] = None
) -> GoalManager:
    """
    GoalManager 싱글톤 인스턴스 반환.
    
    Args:
        db: MemoryDatabase 인스턴스 (첫 호출 시에만 적용)
        planner: GoalPlanner 인스턴스 (첫 호출 시에만 적용)
        
    Returns:
        GoalManager 인스턴스
    """
    global _manager_instance
    if _manager_instance is None:
        _manager_instance = GoalManager(db=db, planner=planner)
    return _manager_instance
