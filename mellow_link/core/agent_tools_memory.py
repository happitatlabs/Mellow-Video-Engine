"""
Agent Tools - 메모리/RAG 도구: search_memory, get_user_feedback, get_my_work_history.
"""
import json
import logging
from typing import Optional

from mellow_link.core.tool_registry import tool
from mellow_link.core.agent_tools_base import (
    _get_security,
    _pm,
)

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════
# 3. RAG 기억 검색
# ═══════════════════════════════════════════════

@tool(category="memory")
async def search_memory(query: str, top_k: int = 3, timeout: float = 10.0) -> str:
    """
    RAG 데이터베이스에서 관련 문서를 검색합니다.
    과거 대화, 업로드된 문서, 프로젝트 자료에서 정보를 찾을 때 사용하세요.
    
    Performance: 검색 결과는 5분간 캐시되며, 병렬 처리로 빠르게 실행됩니다.
    
    Args:
        query: 검색 쿼리 문자열
        top_k: 반환할 최대 결과 수 (기본값: 3)
        timeout: 검색 타임아웃 (초, 기본값: 10.0)
    """
    from mellow_link.services.rag_service import get_rag_service

    rag = get_rag_service()
    if rag is None:
        return "[Offline] RAG 서비스가 초기화되지 않았습니다."

    try:
        # 타임아웃 적용하여 검색 실행
        import asyncio
        results = await asyncio.wait_for(
            rag.search(query=query, top_k=top_k, timeout=timeout),
            timeout=timeout + 2.0  # 검색 타임아웃 + 여유 시간
        )
        
        if not results:
            return f"[검색 결과 없음] '{query}'에 대한 관련 문서를 찾지 못했습니다."

        lines = [f"[검색 결과] '{query}' - {len(results)}건"]
        for i, r in enumerate(results, 1):
            score = getattr(r, "score", 0)
            filename = getattr(r, "filename", "unknown")
            content = getattr(r, "content", str(r))
            # 검색 결과를 요약해서 전달 (토큰 절약)
            snippet = content[:300] + "..." if len(content) > 300 else content
            lines.append(f"\n--- [{i}] {filename} (유사도: {score:.2f}) ---\n{snippet}")

        return "\n".join(lines)

    except asyncio.TimeoutError:
        logger.warning(f"[search_memory] 검색 타임아웃: {query[:50]}...")
        return f"[Timeout] 검색이 {timeout}초 내에 완료되지 않았습니다. 쿼리를 더 구체적으로 작성하거나 top_k를 줄여보세요."
    except Exception as e:
        logger.exception("[search_memory] failed")
        return f"[Error] RAG 검색 실패: {e}"


# ═══════════════════════════════════════════════
# 3-b. 사용자 피드백 조회
# ═══════════════════════════════════════════════

@tool(category="memory")
def get_user_feedback(session_id: Optional[int] = None, limit: int = 20) -> str:
    """
    사용자가 에이전트 답변에 남긴 피드백(👍/👎)을 조회합니다.
    어떤 답변이 좋았고 어떤 답변이 안 좋았는지 확인하여 응답 품질을 개선할 때 사용하세요.

    Args:
        session_id: 특정 세션의 피드백만 조회 (None이면 최근 전체)
        limit: 최대 조회 건수 (기본 20)
    """
    try:
        from mellow_link.infra.database import (
            SessionLocal, MessageFeedback, ChatMessage, ChatSession
        )
        from sqlalchemy import desc

        db = SessionLocal()
        try:
            query = (
                db.query(MessageFeedback, ChatMessage)
                .join(ChatMessage, MessageFeedback.message_id == ChatMessage.id)
            )

            if session_id is not None:
                query = query.filter(ChatMessage.session_id == session_id)

            results = (
                query
                .order_by(desc(MessageFeedback.created_at))
                .limit(limit)
                .all()
            )

            if not results:
                scope = f"세션 {session_id}" if session_id else "전체"
                return f"[피드백 없음] {scope}에서 사용자 피드백이 아직 없습니다."

            positive_count = sum(1 for fb, _ in results if fb.is_positive)
            negative_count = sum(1 for fb, _ in results if not fb.is_positive)

            lines = [
                f"[사용자 피드백 조회] 총 {len(results)}건 (👍 {positive_count} / 👎 {negative_count})",
                ""
            ]

            for fb, msg in results:
                emoji = "👍" if fb.is_positive else "👎"
                snippet = msg.content[:150] + "..." if len(msg.content) > 150 else msg.content
                ts = fb.created_at.strftime("%Y-%m-%d %H:%M") if fb.created_at else "?"
                comment = f" | 코멘트: {fb.comment}" if fb.comment else ""
                lines.append(
                    f"  {emoji} [msg#{msg.id}, session#{msg.session_id}] {ts}{comment}\n"
                    f"     답변: {snippet}"
                )

            return "\n".join(lines)

        finally:
            db.close()

    except ImportError:
        return "[Error] 데이터베이스 모듈을 불러올 수 없습니다."
    except Exception as e:
        logger.exception("[get_user_feedback] failed")
        return f"[Error] 피드백 조회 실패: {e}"


# ═══════════════════════════════════════════════
# 3-c. 내 작업 기록 조회
# ═══════════════════════════════════════════════

@tool(category="memory")
def get_my_work_history(days: int = 7, include_autonomous: bool = True) -> str:
    """
    최근 N일간 내가 수행한 작업 목록을 조회합니다.
    무엇을 했는지 기억이 안 날 때, 과거 작업을 참고할 때 사용하세요.

    Args:
        days: 조회 기간 (일, 기본값: 7)
        include_autonomous: 자율 작업 결과도 포함할지 (기본값: True)

    Returns:
        최근 작업 기록 요약
    """
    try:
        from datetime import datetime, timedelta
        from mellow_link.infra.memory_database import get_memory_db

        db = get_memory_db()
        since = (datetime.now() - timedelta(days=days)).isoformat()

        lines = [
            f"[내 작업 기록] 최근 {days}일",
            "=" * 50,
            "",
        ]

        # 1. 일반 작업 (experience_ledger)
        experiences = db.get_ledger_entries_since(since, limit=50)

        if experiences:
            lines.append(f"[일반 작업] {len(experiences)}건")
            lines.append("-" * 40)

            for exp in experiences[:20]:  # 최대 20건만 표시
                status = "✅" if exp.is_success else "❌"
                ts = exp.created_at.strftime("%m/%d %H:%M") if exp.created_at else "?"
                intent = (exp.task_intent or "")[:60]
                if len(exp.task_intent or "") > 60:
                    intent += "..."

                # 사용 도구 파싱
                tools_used = ""
                if exp.used_tools:
                    try:
                        tools = json.loads(exp.used_tools)
                        if tools:
                            tools_used = f" | 도구: {', '.join(tools[:3])}"
                            if len(tools) > 3:
                                tools_used += f" 외 {len(tools)-3}개"
                    except Exception:
                        pass

                # 실패 태그
                tag = ""
                if exp.critique_tag:
                    tag = f" [{exp.critique_tag}]"

                lines.append(f"  {status} [{ts}] {intent}{tools_used}{tag}")

            if len(experiences) > 20:
                lines.append(f"  ... 외 {len(experiences) - 20}건 더 있음")
            lines.append("")

        # 2. 자율 작업 (autonomous_work_results)
        if include_autonomous:
            try:
                autonomous_results = db.get_autonomous_work_results_by_status(None, limit=30)

                # 기간 내 필터링
                since_dt = datetime.now() - timedelta(days=days)
                recent_auto = [
                    r for r in autonomous_results
                    if r.created_at and r.created_at >= since_dt
                ]

                if recent_auto:
                    lines.append(f"[자율 작업] {len(recent_auto)}건")
                    lines.append("-" * 40)

                    for r in recent_auto[:15]:  # 최대 15건
                        status_icon = {
                            "COMPLETED": "✅",
                            "FAILED": "❌",
                            "QUARANTINED": "⚠️",
                            "WAITING_FOR_APPROVAL": "⏳",
                            "APPROVED": "👍",
                            "REJECTED": "🚫",
                        }.get(r.status, "❓")

                        ts = r.created_at.strftime("%m/%d %H:%M") if r.created_at else "?"
                        task_type = r.task_type or "unknown"

                        # 작업 내용 요약
                        summary = ""
                        if r.tools_created:
                            summary = f"도구 생성: {r.tools_created[:40]}"
                        elif r.info_collected:
                            summary = f"정보 수집: {r.info_collected[:40]}..."
                        elif r.final_output:
                            summary = f"출력: {r.final_output[:40]}..."

                        lines.append(f"  {status_icon} [{ts}] {task_type}: {summary}")

                    if len(recent_auto) > 15:
                        lines.append(f"  ... 외 {len(recent_auto) - 15}건 더 있음")
                    lines.append("")
            except Exception as e:
                logger.debug(f"[get_my_work_history] autonomous fetch failed: {e}")

        # 3. 통계 요약
        if experiences:
            success_count = sum(1 for e in experiences if e.is_success)
            fail_count = len(experiences) - success_count
            success_rate = (success_count / len(experiences) * 100) if experiences else 0

            lines.append("[통계]")
            lines.append("-" * 40)
            lines.append(f"  총 작업: {len(experiences)}건")
            lines.append(f"  성공: {success_count}건 / 실패: {fail_count}건")
            lines.append(f"  성공률: {success_rate:.1f}%")

            # 자주 사용한 도구 TOP 3
            tool_counter: dict = {}
            for exp in experiences:
                if exp.used_tools:
                    try:
                        tools = json.loads(exp.used_tools)
                        for t in tools:
                            tool_counter[t] = tool_counter.get(t, 0) + 1
                    except Exception:
                        pass

            if tool_counter:
                top_tools = sorted(tool_counter.items(), key=lambda x: -x[1])[:3]
                lines.append(f"  자주 사용한 도구: {', '.join(f'{t}({c})' for t, c in top_tools)}")

        if len(lines) <= 3:
            return f"[내 작업 기록] 최근 {days}일간 작업 기록이 없습니다."

        return "\n".join(lines)

    except Exception as e:
        logger.exception("[get_my_work_history] failed")
        return f"[Error] 작업 기록 조회 실패: {e}"
