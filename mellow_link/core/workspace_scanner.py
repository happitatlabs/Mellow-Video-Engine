"""
Workspace Scanner - 로컬 AI가 자동으로 workspace 데이터를 수집하는 모듈

workspace 폴더 내부 데이터 수집은 로컬 AI가 자동으로, 주기적으로 수행합니다.
사용자 승인 없이 자동 실행됩니다.
"""

import asyncio
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional, Any

from mellow_link.core.workspace_sandbox import get_workspace_root

logger = logging.getLogger(__name__)


async def scan_workspace_with_local_ai(agent_brain: Any) -> None:
    """
    로컬 AI가 자동으로 workspace를 탐색하고 데이터를 수집합니다.
    AgentBrain의 도구(list_directory, read_file 등)를 사용하여 자동으로 실행됩니다.
    
    Args:
        agent_brain: AgentBrain 인스턴스 (도구 사용 가능)
    """
    workspace = get_workspace_root()
    
    logger.info("[WorkspaceScanner] 로컬 AI 자동 workspace 탐색 시작")
    
    try:
        # 로컬 AI에게 workspace 탐색 요청
        # 도구를 사용하여 자동으로 파일 목록, 구조 등을 수집
        result = await agent_brain.run(
            user_input=(
                "workspace 폴더의 전체 구조를 탐색하고 주요 파일들의 정보를 수집해줘. "
                "list_directory 도구를 사용해서 폴더 구조를 파악하고, "
                "중요해 보이는 파일들은 read_file로 읽어서 내용을 확인해줘. "
                "수집한 정보를 정리해서 요약해줘."
            ),
            require_at_least_one_tool=True
        )
        
        logger.info(
            "[WorkspaceScanner] 탐색 완료: %d턴 실행, 종료 이유: %s",
            result.total_turns,
            result.finish_reason
        )
        
        # 실행된 도구 확인
        executed_tools = [step.action.tool for step in result.steps if step.action]
        logger.info(
            "[WorkspaceScanner] 사용된 도구: %s",
            ", ".join(executed_tools) if executed_tools else "(없음)"
        )
        
        return result
        
    except Exception as e:
        logger.error(f"[WorkspaceScanner] 탐색 실패: {e}", exc_info=True)
        return None


async def run_periodic_workspace_scan(
    llm_service: Any,
    agent_brain: Any,
    interval_seconds: int = 3600,  # 1시간마다
    shutdown_event: Optional[asyncio.Event] = None
) -> None:
    """
    주기적으로 workspace를 스캔하는 백그라운드 루프.
    
    Args:
        llm_service: LLMService 인스턴스
        agent_brain: AgentBrain 인스턴스
        interval_seconds: 스캔 주기 (초)
        shutdown_event: 종료 이벤트
    """
    logger.info("[WorkspaceScanner] 주기적 스캔 루프 시작 (interval=%ds)", interval_seconds)
    
    while shutdown_event is None or not shutdown_event.is_set():
        try:
            await scan_workspace_with_local_ai(agent_brain)
        except Exception as e:
            logger.error(f"[WorkspaceScanner] 주기적 스캔 실패: {e}")
        
        # 다음 스캔까지 대기
        try:
            await asyncio.wait_for(
                shutdown_event.wait() if shutdown_event else asyncio.sleep(interval_seconds),
                timeout=interval_seconds
            )
            if shutdown_event and shutdown_event.is_set():
                break
        except asyncio.TimeoutError:
            pass
    
    logger.info("[WorkspaceScanner] 주기적 스캔 루프 종료")
