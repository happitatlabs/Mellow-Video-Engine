"""
Autonomous Agent - 자율 에이전트 모드

사용자 부재 시 백그라운드에서 Tower 계획에 따라 workspace/ 내에서만
도구 제작, 정보 수집을 수행.
- Guardian 윤리 검토 통과 → 자동 실행 (승인 불필요)
- Guardian 윤리 검토 거부 → QUARANTINED (관리자 확인 필요)
"""

from __future__ import annotations

import asyncio
import difflib
import json
import logging
import os
import re
import subprocess
import sys
import traceback
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any, List, Tuple

from mellow_link.core.workspace_sandbox import get_workspace_root, can_write_to_path, resolve_workspace_path
from mellow_link.infra.memory_database import (
    get_memory_db,
    AutonomousWorkResult,
)

logger = logging.getLogger(__name__)


_DESCRIPTION_RE = re.compile(r"^\s*#\s*[Dd]escription\s*:\s*(.+)$", re.MULTILINE)

_SENSING_KEYWORDS = ("구조", "목록", "list", "directory", "workspace", "파일 목록", "디렉터리", "폴더")
_REUSE_LISTING_TOOLS = ("list_files.py", "list_workspace_files.py", "explore_workspace.py")


def _is_pure_sensing_task(info_collected: str, tools_to_create: List[str]) -> bool:
    """순수 정보 수집(디렉터리 구조 파악 등) 여부. 이 경우 write_file 없이 in-process 처리."""
    info_lower = (info_collected or "").lower()
    tools_lower = [t.lower().replace("\\", "/") for t in tools_to_create or []]
    has_sensing_keyword = any(kw in info_lower for kw in _SENSING_KEYWORDS)
    has_listing_tool = any(
        any(reuse in t for reuse in _REUSE_LISTING_TOOLS) for t in tools_lower
    )
    return has_sensing_keyword and (has_listing_tool or not tools_to_create)


def _collect_workspace_listing_in_process(workspace_root: Path) -> str:
    """pathlib로 workspace 목록 수집. write_file 없이 메모리에서 처리."""
    lines: List[str] = []
    try:
        for p in sorted(workspace_root.rglob("*"), key=lambda x: (x.is_file(), x.name)):
            try:
                rel = p.relative_to(workspace_root)
                prefix = "[DIR] " if p.is_dir() else "      "
                lines.append(f"{prefix}{rel.as_posix()}")
            except ValueError:
                continue
        return "\n".join(lines[:100]) if lines else "(비어 있음)"
    except Exception as e:
        logger.debug("[AutonomousAgent] in-process listing failed: %s", e)
        return f"(목록 수집 실패: {e})"


def _get_existing_workspace_tools(workspace_root: Path) -> str:
    """
    workspace/ 하위 모든 .py 파일을 재귀 탐색하고, 각 파일의 # Description을
    정규식으로 추출해 [Existing Tools] 섹션용 문자열로 반환.
    """
    lines: List[str] = []
    try:
        for path in sorted(workspace_root.rglob("*.py")):
            try:
                rel = path.relative_to(workspace_root)
                display_name = rel.as_posix()
            except ValueError:
                display_name = path.name
            desc = ""
            try:
                raw = path.read_text(encoding="utf-8", errors="replace")
                for line in raw.splitlines()[:20]:
                    m = _DESCRIPTION_RE.match(line.strip())
                    if m:
                        desc = m.group(1).strip()[:200]
                        break
            except Exception:
                pass
            if desc:
                lines.append(f"  - {display_name}  # {desc}")
            else:
                lines.append(f"  - {display_name}")
    except Exception as e:
        logger.debug("[AutonomousAgent] list workspace tools failed: %s", e)
    return "\n".join(lines) if lines else "  (없음)"


async def run_autonomous_tick(
    orchestrator: Any = None,
    shutdown_event: Optional[asyncio.Event] = None,
) -> Optional[AutonomousWorkResult]:
    """
    자율 에이전트 1회 틱 실행.
    Tower 계획 → workspace 내 작업 → 윤리 검토 → 통과 시 자동 실행.
    """
    from mellow_link.infra.env_loader import load_dotenv_early
    from mellow_link.core.provider_factory import get_client, generate_async

    load_dotenv_early()

    if shutdown_event and shutdown_event.is_set():
        return None

    workspace = get_workspace_root()
    db = get_memory_db()
    record_id = str(uuid.uuid4())

    try:
        waiting = db.get_autonomous_work_results_by_status("WAITING_FOR_APPROVAL", limit=20)
        waiting_lines: List[str] = []
        for r in waiting:
            desc = (r.info_collected or r.tools_created or "").strip()[:300] or "(설명 없음)"
            waiting_lines.append(f"  - [{r.id[:8]}] {desc}")
        waiting_text = "\n".join(waiting_lines) if waiting_lines else "  (없음)"

        tools_inventory = _get_existing_workspace_tools(workspace)
        # 승인 대기 Evolution 제안 (중복 방지·작업 다각화용). Facade 경유
        evo_waiting_lines: List[str] = []
        try:
            from mellow_link.core.evolution_facade import EvolutionFacade
            resp = EvolutionFacade.list_waiting_for_approval()
            if resp.status == "SUCCESS" and resp.items:
                for p in resp.items:
                    req = (p.get("user_request") or "")[:120]
                    tgt = p.get("verdict_target_file") or ""
                    evo_waiting_lines.append(f"  - 대상: {tgt} | 요청: {req}")
        except Exception:
            pass
        evo_waiting_text = "\n".join(evo_waiting_lines) if evo_waiting_lines else "  (없음)"

        tower_cfg = get_client("google", role="tower")
        plan_prompt = f"""너는 시스템 관제탑(Tower)이다.
자율 에이전트가 workspace({workspace}) 내에서 수행할 수 있는 다음 작업을 계획하라.

## 현재 대기 중인 작업
{waiting_text}

## Evolution 승인 대기 제안 (동일·유사 작업 금지, 다른 작업으로 다각화)
{evo_waiting_text}
위 목록과 동일/유사한 파일 수정·요청은 이미 대기 중이므로 선택하지 말라. 대신 다음 우선순위를 고려하라: 다른 도구 제작, 기존 도구 테스트 코드 작성, README.md 업데이트 등.

## 사용 가능한 도구 인벤토리
{tools_inventory}

## 제약
- 작업 구역: mellow_link/workspace/ 만 허용
- core/, config/, .env 수정 금지
- 도구 스크립트 작성, 정보 수집(스크랩) 가능

## 가이드라인
- **workspace 구조 파악, 디렉터리 목록 확인 등 순수 정보 수집 시**: action=reuse, tools_to_create=["list_files.py"] 또는 ["explore_workspace.py"]. autonomous_script.py 생성 절대 금지.
- 새로운 Python 스크립트를 설계하기 전에 [사용 가능한 도구 인벤토리]를 확인하라.
- 비슷한 기능을 하는 도구가 있다면 새로 만들지 말고, 기존 파일을 수정(update_file)하여 활용하라.
- **비슷한 파일명이 사용 가능한 도구 인벤토리에 있다면 반드시 action을 "modify"로 우선 선택하고, 선택 사유를 reason 필드에 명시하라.**
- **새로운 기능이 필요할 때, 인벤토리에 유사한 도구가 있다면 새로 만들지 말고 해당 코드를 읽어(pathlib) 기능을 확장(Update/Refactor)하는 계획을 우선적으로 세워라.** 기존 도구를 수정할 때는 evolution_manager의 로직(기존 코드 읽기 → 수정안 제안 → 검수)을 적극 활용하도록 유도하라.

## 요청
1회 자율 틱에서 수행할 작업 계획을 JSON으로 출력. **action은 필수**다.
{{"plan": "작업 계획 요약", "action": "create" | "modify" | "reuse", "reason": "action 선택 사유 (modify/reuse 시 필수)", "tools_to_create": ["도구명1", "도구명2"], "info_to_collect": "수집할 정보 유형"}}

- action: "create" (기존 도구 없음, 새로 작성) | "modify" (기존 도구 수정) | "reuse" (기존 도구 그대로 사용)
- modify/reuse 선택 시 reason에 어떤 기존 도구를 쓰는지, 왜 그렇게 했는지 반드시 적어라.

실행 불가 시: {{"plan": "skip", "reason": "이유"}}
"""
        plan_text = await generate_async(
            tower_cfg.provider, tower_cfg.model, plan_prompt, tower_cfg.api_key
        )
        plan_data = {}
        try:
            if "```json" in plan_text:
                plan_text = plan_text.split("```json")[1].split("```")[0].strip()
            elif "```" in plan_text:
                plan_text = plan_text.split("```")[1].split("```")[0].strip()
            plan_data = json.loads(plan_text)
        except json.JSONDecodeError:
            plan_data = {"plan": "skip", "reason": "Tower 응답 파싱 실패"}

        if plan_data.get("plan") == "skip":
            logger.info("[AutonomousAgent] Tick skipped: %s", plan_data.get("reason", ""))
            return None

        valid_actions = ("create", "modify", "reuse")
        action = (plan_data.get("action") or "").strip().lower()
        if action not in valid_actions:
            logger.warning("[AutonomousAgent] Invalid or missing action=%r, skipping tick", plan_data.get("action"))
            return None

        tools_created_raw = plan_data.get("tools_to_create", [])
        if action == "create" and not tools_created_raw:
            logger.info("[AutonomousAgent] create 시 tools_to_create 필수, 스킵")
            return None
        tools_created_list = tools_created_raw if isinstance(tools_created_raw, list) else [tools_created_raw] if tools_created_raw else []
        tools_created = json.dumps(tools_created_raw, ensure_ascii=False) if tools_created_raw else "[]"
        info_collected = str(plan_data.get("info_to_collect", plan_data.get("plan", "")))

        # 인지 도구 내재화: 순수 정보 수집(디렉터리 목록 등)은 write_file 없이 in-process 처리
        if action == "reuse" and _is_pure_sensing_task(info_collected, tools_created_list):
            listing_result = _collect_workspace_listing_in_process(workspace)
            record = AutonomousWorkResult(
                id=record_id,
                task_type="autonomous_tick",
                tools_created=tools_created,
                info_collected=info_collected[:5000],
                ethics_review="(인지 내재화: write_file 없이 완료)",
                ethics_approved=1,
                status="COMPLETED",
                created_at=datetime.now(),
                updated_at=datetime.now(),
            )
            db.save_autonomous_work_result(record)
            db.update_autonomous_work_output(record_id, "COMPLETED", output=listing_result)
            logger.info("[AutonomousAgent] 순수 정보 수집 완료 (in-process, 승인 생략): %s", record_id[:8])
            return record

        # 중복 배팅 차단: 제안된 작업이 대기 중인 작업과 80% 이상 유사하면 스킵
        proposed_desc = (info_collected or "").strip()
        for r in waiting:
            existing_desc = (r.info_collected or "").strip()
            if not proposed_desc or not existing_desc:
                continue
            ratio = difflib.SequenceMatcher(None, proposed_desc, existing_desc).ratio()
            if ratio >= 0.8:
                logger.info(
                    "[AutonomousAgent] 이미 유사한 작업이 승인 대기 중입니다 (ID: %s), 새 틱 스킵",
                    r.id[:8],
                )
                return None

        # 윤리 검토: Guardian(Anthropic/OpenAI) 2차 검수 (Tower 자기 검토 폐기)
        from mellow_link.core.guardian_service import get_guardian_service
        guardian = get_guardian_service()
        approved, ethics_review_text, violations = await guardian.audit_autonomous_ethics(
            tools_created=tools_created,
            info_collected=info_collected[:2000] or "(없음)",
        )
        ethics_review_text = (ethics_review_text or "")[:2000]
        if violations and violations != "없음":
            ethics_review_text = f"{ethics_review_text}\n[violations] {violations}"
        if not approved:
            # Guardian 윤리 검토 거부 → 격리 (수동 승인 필요)
            record = AutonomousWorkResult(
                id=record_id,
                task_type="autonomous_tick",
                tools_created=tools_created,
                info_collected=info_collected[:5000],
                ethics_review=ethics_review_text,
                ethics_approved=0,
                status="QUARANTINED",
                created_at=datetime.now(),
                updated_at=datetime.now(),
            )
            db.save_autonomous_work_result(record)
            logger.info("[AutonomousAgent] Tick quarantined: %s", record_id[:8])
            return record

        # Guardian 윤리 검토 통과 → WAITING_FOR_APPROVAL 저장 후 자동 실행
        record = AutonomousWorkResult(
            id=record_id,
            task_type="autonomous_tick",
            tools_created=tools_created,
            info_collected=info_collected[:5000],
            ethics_review=ethics_review_text,
            ethics_approved=1,
            status="WAITING_FOR_APPROVAL",
            created_at=datetime.now(),
            updated_at=datetime.now(),
        )
        db.save_autonomous_work_result(record)
        logger.info("[AutonomousAgent] Tick approved, auto-executing: %s", record_id[:8])

        # 텔레그램 알림 (확인용)
        try:
            from mellow_link.services.notification_service import notify_autonomous_work_waiting_approval
            notify_autonomous_work_waiting_approval(record)
        except Exception as e:
            logger.warning("[AutonomousAgent] Telegram notification failed: %s", e)

        # 자동 실행: Guardian 통과 = 바로 execute
        try:
            success, exec_msg = await execute_approved_work(record_id)
            if success:
                logger.info("[AutonomousAgent] Auto-execution succeeded: %s", record_id[:8])
            else:
                logger.warning("[AutonomousAgent] Auto-execution failed: %s - %s", record_id[:8], exec_msg)
        except Exception as exec_err:
            logger.error("[AutonomousAgent] Auto-execution error: %s - %s", record_id[:8], exec_err)

        # 실행 후 최신 상태 반환
        updated_record = db.get_autonomous_work_result_by_id(record_id)
        return updated_record or record

    except Exception as e:
        logger.exception("[AutonomousAgent] Tick failed: %s", e)
        tb_str = traceback.format_exc()
        record = AutonomousWorkResult(
            id=record_id,
            task_type="autonomous_tick",
            tools_created=None,
            info_collected=None,
            ethics_review=str(e)[:2000] if e else None,  # 길이 제한
            ethics_approved=0,  # 명시적으로 int
            status="ETHICS_FAIL",
            created_at=datetime.now(),  # 명시적으로 datetime 설정
            updated_at=datetime.now(),  # 명시적으로 datetime 설정
        )
        try:
            db.save_autonomous_work_result(record)
        except Exception as save_err:
            # 저장 실패해도 계속 진행 (무한 루프 방지)
            logger.error("[AutonomousAgent] Failed to save error record: %s", save_err, exc_info=True)
        
        # 실패 원인 분석 및 자동 복구 시도
        recovery_success, recovery_msg = await _analyze_and_recover_from_failure(record, e, db)
        if recovery_success:
            logger.info("[AutonomousAgent] 자동 복구 성공: %s", recovery_msg)
            # 복구 성공 시 상태를 PENDING으로 변경하여 재시도 가능하게 함
            record.status = "PENDING"
            record.ethics_review = f"(복구됨) {recovery_msg}\n원본 오류: {str(e)}"[:2000]  # 길이 제한
            record.updated_at = datetime.now()  # 업데이트 시간 갱신
            try:
                db.save_autonomous_work_result(record)
            except Exception as save_err:
                logger.error("[AutonomousAgent] Failed to save recovery record: %s", save_err, exc_info=True)
        
        # 틱 단계 오류 시 텔레그램 알림 추가
        try:
            from mellow_link.services.notification_service import notify_autonomous_work_failed
            failure_reason = str(e)
            if recovery_success:
                failure_reason = f"{failure_reason} (자동 복구 완료: {recovery_msg})"
            notify_autonomous_work_failed(
                record_id,
                "자율 틱 실행 실패" + (" (복구됨)" if recovery_success else ""),
                failure_reason,
                tb_str[:400],
                ""
            )
        except Exception as notify_err:
            logger.warning("[AutonomousAgent] Telegram failure notify failed: %s", notify_err)
        
        return record


async def run_autonomous_loop(
    orchestrator: Any = None,
    shutdown_event: Optional[asyncio.Event] = None,
    interval_seconds: int = 3600,
) -> None:
    """
    자율 에이전트 백그라운드 루프.
    interval_seconds마다 run_autonomous_tick 호출.
    일시적 오류 시 자동 재시도 (최대 3회, 지수 백오프).
    """
    logger.info("[AutonomousAgent] Loop started (interval=%ds)", interval_seconds)
    consecutive_failures = 0
    max_retries = 3
    base_retry_delay = 60  # 1분
    
    while shutdown_event is None or not shutdown_event.is_set():
        try:
            result = await run_autonomous_tick(orchestrator=orchestrator, shutdown_event=shutdown_event)
            
            # 성공 시 실패 카운터 리셋
            if result and result.status not in ("ETHICS_FAIL", "QUARANTINED"):
                consecutive_failures = 0
            elif result and result.status in ("ETHICS_FAIL", "QUARANTINED"):
                # 실패했지만 재시도 가능한 경우
                consecutive_failures += 1
                if consecutive_failures <= max_retries:
                    retry_delay = base_retry_delay * (2 ** (consecutive_failures - 1))  # 지수 백오프
                    logger.info(
                        "[AutonomousAgent] 재시도 대기: %d/%d회 실패, %d초 후 재시도",
                        consecutive_failures, max_retries, retry_delay
                    )
                    await asyncio.sleep(retry_delay)
                    continue
                else:
                    logger.warning(
                        "[AutonomousAgent] 최대 재시도 횟수 초과 (%d회), 다음 주기까지 대기",
                        consecutive_failures
                    )
                    consecutive_failures = 0  # 다음 주기에서 다시 시도
                    
        except asyncio.CancelledError:
            break
        except Exception as e:
            error_msg = str(e)
            error_type = type(e).__name__
            logger.error(
                "[AutonomousAgent] Loop tick error: %s (type: %s)",
                error_msg, error_type, exc_info=True
            )
            consecutive_failures += 1
            
            # 일시적 오류인지 판단 (네트워크, 타임아웃, 경로 정규화 오류 등)
            is_transient = _is_transient_error(e)
            if is_transient and consecutive_failures <= max_retries:
                retry_delay = base_retry_delay * (2 ** (consecutive_failures - 1))
                logger.info(
                    "[AutonomousAgent] 일시적 오류 감지, %d초 후 재시도 (%d/%d)",
                    retry_delay, consecutive_failures, max_retries
                )
                await asyncio.sleep(retry_delay)
                continue
            else:
                # 영구적 오류이거나 최대 재시도 초과
                if not is_transient:
                    logger.error(
                        "[AutonomousAgent] 영구적 오류로 판단, 재시도 중단 (오류: %s, 타입: %s)",
                        error_msg[:200], error_type
                    )
                else:
                    logger.warning(
                        "[AutonomousAgent] 최대 재시도 횟수 초과 (%d/%d), 다음 주기까지 대기",
                        consecutive_failures, max_retries
                    )
                consecutive_failures = 0  # 다음 주기에서 다시 시도
        
        await asyncio.sleep(interval_seconds)
    logger.info("[AutonomousAgent] Loop stopped")


def _is_transient_error(error: Exception) -> bool:
    """
    일시적 오류인지 판단 (재시도 가능한 오류).
    
    일시적 오류:
    - 네트워크 오류 (ConnectionError, TimeoutError)
    - 서비스 일시적 불가 (503, 429 등)
    - 타임아웃
    - 리소스 일시적 부족
    - 경로 정규화 오류 (PATH_GATE_BLOCKED 등) - 경로 형식 문제는 재시도로 해결 가능
    - 데이터베이스 타입 불일치 (datatype mismatch) - 데이터 변환 로직 개선으로 해결 가능
    
    영구적 오류:
    - 인증 실패 (401)
    - 권한 없음 (403)
    - 잘못된 요청 (400)
    - 구문 오류 등
    """
    error_type = type(error).__name__
    error_str = str(error).lower()
    
    # 데이터베이스 관련 오류 중 일시적 오류로 처리 가능한 것들
    import sqlite3
    if isinstance(error, sqlite3.IntegrityError):
        # datatype mismatch는 데이터 변환 로직 개선으로 해결 가능하므로 일시적 오류로 처리
        if "datatype mismatch" in error_str or "type mismatch" in error_str:
            return True
        # 다른 IntegrityError는 영구적 오류로 처리 (제약 조건 위반 등)
        return False
    
    # 일시적 오류 패턴
    transient_patterns = [
        "connection", "timeout", "network", "temporary", "503", "429",
        "rate limit", "too many requests", "service unavailable",
        "resource temporarily unavailable", "eagain", "would block",
        # 경로 관련 오류는 경로 정규화 로직 개선으로 해결 가능하므로 일시적 오류로 처리
        "path_gate_blocked", "access denied", "경로 검증 실패", "경로 탈출",
        "path outside workspace", "invalid path", "경로 자동 교정",
        # 데이터베이스 타입 불일치
        "datatype mismatch", "type mismatch"
    ]
    
    # 영구적 오류 패턴
    permanent_patterns = [
        "authentication", "authorization", "401", "403", "400",
        "syntax error", "invalid", "not found", "404"
    ]
    
    # 영구적 오류 우선 확인
    if any(pattern in error_str for pattern in permanent_patterns):
        return False
    
    # 일시적 오류 확인
    if any(pattern in error_str for pattern in transient_patterns):
        return True
    
    # 타입 기반 판단
    transient_types = (
        ConnectionError, TimeoutError, asyncio.TimeoutError,
        OSError  # 네트워크 관련 OSError
    )
    if isinstance(error, transient_types):
        return True
    
    return False  # 기본값: 영구적 오류로 간주


async def _analyze_and_recover_from_failure(
    record: AutonomousWorkResult,
    error: Exception,
    db: Any
) -> Tuple[bool, str]:
    """
    실패 원인 분석 및 자동 복구 시도.
    
    Returns:
        (복구 성공 여부, 복구 메시지)
    """
    error_str = str(error).lower()
    error_type = type(error).__name__
    
    # 1. 네트워크/연결 오류 → 재시도만 (이미 상위에서 처리됨)
    if _is_transient_error(error):
        return False, "일시적 오류 - 재시도 대기 중"
    
    # 2. 파일 시스템 오류 → workspace 정리 시도
    if any(keyword in error_str for keyword in ["permission", "access denied", "file", "directory"]):
        logger.info("[AutonomousAgent] 파일 시스템 오류 감지, workspace 정리 시도")
        try:
            workspace = get_workspace_root()
            # .temp 폴더 정리
            temp_dir = workspace / ".temp"
            if temp_dir.exists():
                for item in temp_dir.iterdir():
                    try:
                        if item.is_file():
                            item.unlink()
                        elif item.is_dir():
                            import shutil
                            shutil.rmtree(item)
                    except Exception:
                        pass
                logger.info("[AutonomousAgent] workspace .temp 폴더 정리 완료")
            return True, "workspace 정리 완료"
        except Exception as cleanup_error:
            logger.warning("[AutonomousAgent] workspace 정리 실패: %s", cleanup_error)
            return False, f"workspace 정리 실패: {cleanup_error}"
    
    # 3. 코드 생성 오류 → 간단한 작업으로 대체
    if any(keyword in error_str for keyword in ["code", "verdict", "generation", "parse"]):
        logger.info("[AutonomousAgent] 코드 생성 오류 감지, 정보 수집 모드로 전환")
        # 정보 수집만 수행하도록 상태 변경
        try:
            record.info_collected = f"(복구: 코드 생성 실패로 정보 수집 모드로 전환)\n{record.info_collected or ''}"
            db.save_autonomous_work_result(record)
            return True, "정보 수집 모드로 전환 완료"
        except Exception as save_error:
            logger.warning("[AutonomousAgent] 상태 변경 실패: %s", save_error)
            return False, f"상태 변경 실패: {save_error}"
    
    # 4. 기타 오류 → 로그만 남기고 복구 불가
    logger.warning("[AutonomousAgent] 복구 불가능한 오류: %s (타입: %s)", error_str, error_type)
    return False, f"복구 불가능한 오류: {error_type}"


def _record_and_notify_failure(
    record_id: str,
    db: Any,
    stage: str,
    reason: str,
    tb_str: str = "",
    stdout_snippet: str = "",
) -> None:
    """실패 시 DB 기록 및 텔레그램 알림."""
    logger.error("[AutonomousAgent] 실패 기록: record_id=%s stage=%s reason=%s", record_id[:8], stage, reason)
    output = f"[실패] {stage}: {reason}"
    if tb_str:
        output += f"\n\n[Traceback]\n{tb_str[:2000]}"
    if stdout_snippet:
        output += f"\n\n[stdout 일부]\n{stdout_snippet[:1000]}"
    db.update_autonomous_work_output(record_id, "COMPLETED", output=output)
    try:
        from mellow_link.services.notification_service import notify_autonomous_work_failed
        notify_autonomous_work_failed(record_id, stage, reason, tb_str[:400], stdout_snippet[:400])
    except Exception as e:
        logger.warning("[AutonomousAgent] Telegram failure notify failed: %s", e)


async def execute_approved_work(record_id: str, auto_retry: bool = True) -> Tuple[bool, str]:
    """
    승인된 자율 작업 실행.
    - Verdict로 도구 코드 생성 → workspace/에 파일 생성 → 실행 → 결과 저장.
    - 모든 단계에서 오류 발생 시 상세 로그 및 텔레그램 알림.
    - 일시적 오류 시 자동 재시도 (auto_retry=True).

    Args:
        record_id: 실행할 자율 작업 레코드 ID
        auto_retry: 일시적 오류 시 자동 재시도 여부 (기본값: True)

    Returns:
        (success, message)
    """
    from mellow_link.infra.env_loader import load_dotenv_early
    from mellow_link.core.provider_factory import get_client, generate_async

    load_dotenv_early()
    db = get_memory_db()
    record = db.get_autonomous_work_result_by_id(record_id)
    if not record:
        return False, "레코드를 찾을 수 없습니다."
    if record.status != "WAITING_FOR_APPROVAL":
        return False, f"승인 대기 상태가 아님 (현재: {record.status})"

    workspace = get_workspace_root()
    tool_name = "autonomous_script.py"
    info_desc = (record.info_collected or "").strip() or "작업 수행"

    # tools_created 파싱 (JSON: ["list_files.py"] 등)
    tool_names: List[str] = []
    try:
        raw = (record.tools_created or "").strip()
        if raw.startswith("["):
            tool_names = json.loads(raw)
        else:
            tool_names = [raw] if raw else []
    except json.JSONDecodeError:
        tool_names = [(record.tools_created or "").strip()] if (record.tools_created or "").strip() else []
    if tool_names:
        tool_name = tool_names[0] if isinstance(tool_names[0], str) else str(tool_names[0])
    if not tool_name.endswith(".py"):
        tool_name = f"{tool_name}.py"

    # 인지 내재화: 순수 정보 수집이면 write_file 없이 in-process 처리
    if (not tool_names or tool_name == "autonomous_script.py") and _is_pure_sensing_task(info_desc, tool_names or ["autonomous_script.py"]):
        listing_result = _collect_workspace_listing_in_process(workspace)
        db.update_autonomous_work_output(record_id, "COMPLETED", output=listing_result)
        try:
            from mellow_link.services.notification_service import notify_autonomous_work_completed
            notify_autonomous_work_completed(
                record_id, "list_directory", True, listing_result[:500],
                task_summary=info_desc[:500] or "",
            )
        except Exception as e:
            logger.warning("[AutonomousAgent] Telegram completion notify failed: %s", e)
        return True, listing_result[:500]

    if not tool_name.strip() or tool_name.strip() == ".py":
        _record_and_notify_failure(
            record_id, db,
            "파일명 검증 실패",
            "파일명이 올바르지 않음 (빈 문자열 또는 공백)",
        )
        return False, "파일명이 올바르지 않음"

    try:
        # Verdict로 Python 스크립트 코드 생성
        from mellow_link.core.verdict_prompts import get_verdict_io_standards
        io_standards = get_verdict_io_standards()
        existing_tools_for_verdict = _get_existing_workspace_tools(workspace)
        code_prompt = f"""다음 작업을 수행하는 Python 스크립트를 작성하라.

## 작업 내용
{info_desc}

## [Existing Tools] (workspace 내 기존 스크립트)
{existing_tools_for_verdict}

## 제약
- mellow_link/workspace/ 내에서만 동작
- 파일 입출력: **open() 금지** (AST 보안 검사에서 차단됨). 반드시 `Path(경로).read_text(encoding="utf-8")`, `Path(경로).write_text(내용, encoding="utf-8")` 만 사용.
- pathlib만 사용 (open, os.path, os.walk, os.listdir 금지)
- subprocess, eval, exec 금지
- print()로 결과 출력
- 단일 스크립트, 함수 정의 없이 바로 실행되는 코드

## 경로 규칙 (매우 중요)
- 워크스페이스 루트 경로: `WORKSPACE_ROOT = Path.cwd()` 를 사용하라.
- **절대 금지**: `Path(__file__).parent` 또는 `Path(__file__).resolve().parent` 사용 금지. 스크립트는 .temp/ 하위에서 실행되므로 __file__ 기준 경로가 workspace 루트와 다르다.
- 환경변수 `WORKSPACE_ROOT`도 사용 가능: `Path(os.environ.get("WORKSPACE_ROOT", Path.cwd()))`
- 기존 도구(fs_util.py 등)를 읽으려면: `Path.cwd() / "fs_util.py"` 로 접근하라.

## 재사용 및 진화
- **새로운 기능이 필요할 때, 인벤토리에 유사한 도구가 있다면 새로 만들지 말고 pathlib로 해당 코드를 읽어 기능을 확장(Update/Refactor)하는 계획을 우선적으로 세워라.**
- 기존 파일의 경로를 인자로 받거나, 기존 스크립트를 import/호출하여 중복 코드를 최소화하라.
- [Existing Tools]에 비슷한 기능이 있으면 새 로직 대신 해당 파일을 확장·수정하는 방향을 우선하라.
- 기존 도구를 수정할 때는 evolution_manager의 파이프라인(기존 파일 읽기 → 수정안 생성 → diff 검토) 로직을 적극 활용하라.

## 메타데이터
- 스크립트 **맨 첫 줄**에 반드시 **실질적인** # Description: 한 줄을 작성하라. 파일의 핵심 기능을 요약한 구체적인 문장이어야 한다.
- "# Description: (자동)" 또는 의미 없는 플레이스홀더는 **금지**다. 지키지 않으면 실행 단계에서 실패 처리된다.

{io_standards}

## 출력
Python 코드만 출력 (```python 제외, 설명 금지)"""

        try:
            verdict_cfg = get_client("openai", role="verdict")
            code_text = await generate_async(
                verdict_cfg.provider, verdict_cfg.model, code_prompt, verdict_cfg.api_key, max_tokens=2048
            )
            code_text = (code_text or "").strip()
            code_text = re.sub(r"^```(?:python|py)?\s*\n?", "", code_text)
            code_text = re.sub(r"\n?```\s*$", "", code_text)
            code_text = code_text.strip()
        except Exception as e:
            tb = traceback.format_exc()
            _record_and_notify_failure(
                record_id, db,
                "Verdict 모델의 코드 생성 실패",
                str(e),
                tb,
            )
            return False, f"Verdict 코드 생성 실패: {e}"

        if not code_text:
            _record_and_notify_failure(
                record_id, db,
                "Verdict 모델의 코드 생성 실패",
                "생성된 코드가 비어 있음",
            )
            return False, "생성된 코드 없음"

        # # Description: 품질 검증 (의미 없는 플레이스홀더 불가)
        first_lines = "\n".join(code_text.splitlines()[:5])
        desc_match = _DESCRIPTION_RE.search(first_lines)
        if not desc_match:
            _record_and_notify_failure(
                record_id, db,
                "Verdict 메타데이터 검증 실패",
                "스크립트 상단에 # Description: (핵심 기능 한 줄 요약)이 없음",
            )
            return False, "Description 주석 누락"
        desc_value = desc_match.group(1).strip()
        if not desc_value or desc_value == "(자동)" or len(desc_value) < 5:
            _record_and_notify_failure(
                record_id, db,
                "Verdict 메타데이터 검증 실패",
                f"# Description:이 비어 있거나 플레이스홀더임 (현재: {desc_value[:50]!r})",
            )
            return False, "Description 주석이 실질적이지 않음"

        # 뒷문 봉쇄: 검증 계층
        from mellow_link.core.risk_classifier import classify_code_risk_level
        from mellow_link.core.tool_forge import run_ast_security_check

        level, level_reason = classify_code_risk_level(code_text)
        logger.info("[AutonomousAgent] Code risk level=%s reason=%s", level, level_reason)

        ok, ast_err = run_ast_security_check(code_text)
        if not ok:
            _record_and_notify_failure(
                record_id, db,
                "보안 검색대(AST)에서 차단됨",
                ast_err,
            )
            return False, f"보안 검색대(AST)에서 차단됨: {ast_err}"

        if level == 3:
            from mellow_link.core.guardian_service import get_guardian_service
            guardian = get_guardian_service()
            audit = await guardian.audit_tool_code(tool_name, info_desc, code_text)
            if not audit.is_approved:
                _record_and_notify_failure(
                    record_id, db,
                    "Guardian 정밀 검수에서 거부됨",
                    audit.critique or "승인되지 않음",
                )
                return False, f"Guardian 정밀 검수 거부: {audit.critique}"

        # workspace 내에 파일 생성. 임시 스크립트는 .temp/ 에 저장 (메인 공간 오염 방지)
        if tool_name == "autonomous_script.py":
            safe_path = resolve_workspace_path(".temp/autonomous_script.py")
            if safe_path:
                safe_path.parent.mkdir(parents=True, exist_ok=True)
        else:
            safe_path = resolve_workspace_path(tool_name)
        if not safe_path:
            _record_and_notify_failure(
                record_id, db,
                "경로 검증 실패",
                "경로 탈출 시도 차단",
            )
            return False, "잘못된 경로"
        allowed, _ = can_write_to_path(str(safe_path))
        if not allowed:
            _record_and_notify_failure(
                record_id, db,
                "파일 저장 실패",
                "workspace 쓰기 권한 없음",
            )
            return False, "쓰기 권한 없음"

        try:
            safe_path.write_text(code_text, encoding="utf-8")
        except Exception as e:
            tb = traceback.format_exc()
            _record_and_notify_failure(
                record_id, db,
                "파일 저장 실패",
                str(e),
                tb,
            )
            return False, f"파일 쓰기 실패: {e}"

        # 실행 (subprocess, timeout 60초)
        db.update_autonomous_work_output(record_id, "EXECUTING", output=record.output or "")
        output = ""
        run_success = False
        try:
            run_env = os.environ.copy()
            run_env["PYTHONPATH"] = os.pathsep.join([str(workspace), run_env.get("PYTHONPATH", "")])
            run_env["WORKSPACE_ROOT"] = str(workspace)
            proc = subprocess.run(
                [sys.executable, str(safe_path)],
                cwd=str(workspace),
                env=run_env,
                capture_output=True,
                text=True,
                timeout=60,
                encoding="utf-8",
                errors="replace",
            )
            stdout = (proc.stdout or "").strip()
            stderr = (proc.stderr or "").strip()
            output_lines = []
            if stdout:
                output_lines.append(f"[stdout]\n{stdout[:3000]}")
            if stderr:
                output_lines.append(f"[stderr]\n{stderr[:1000]}")
            output_lines.append(f"\n[exit_code={proc.returncode}]")
            output = "\n\n".join(output_lines)
            run_success = proc.returncode == 0
        except subprocess.TimeoutExpired as te:
            stdout_snippet = ""
            raw = getattr(te, "stdout", None)
            if raw is not None:
                stdout_snippet = raw[:500] if isinstance(raw, str) else raw.decode("utf-8", errors="replace")[:500]
            _record_and_notify_failure(
                record_id, db,
                "스크립트 실행 실패",
                "실행 타임아웃 (60초)",
                stdout_snippet=stdout_snippet,
            )
            return False, "스크립트 실행 타임아웃 (60초)"
        except Exception as e:
            tb = traceback.format_exc()
            _record_and_notify_failure(
                record_id, db,
                "스크립트 실행 실패",
                str(e),
                tb,
            )
            return False, f"스크립트 실행 오류: {e}"
        else:
            db.update_autonomous_work_output(record_id, "COMPLETED", output=output)

        # 임시 스크립트 자동 정리: autonomous_script.py는 .temp/에서 삭제
        if tool_name == "autonomous_script.py" and safe_path and safe_path.exists():
            try:
                safe_path.unlink()
                temp_dir = safe_path.parent
                if temp_dir.name == ".temp" and temp_dir.exists() and not any(temp_dir.iterdir()):
                    temp_dir.rmdir()
                logger.info("[AutonomousAgent] 임시 스크립트 정리: %s", safe_path.name)
            except Exception as e:
                logger.debug("[AutonomousAgent] 임시 스크립트 삭제 실패 (무시): %s", e)

        # 텔레그램 완료 알림 (성공/실패, 업그레이드/작업 내용 포함)
        try:
            from mellow_link.services.notification_service import notify_autonomous_work_completed
            display_name = tool_name.replace(".py", "")
            notify_autonomous_work_completed(
                record_id, display_name, run_success, output[:500],
                task_summary=info_desc[:500] if info_desc else "",
            )
        except Exception as e:
            logger.warning("[AutonomousAgent] Telegram completion notify failed: %s", e)

        return run_success, output[:500] if output else "완료"

    except Exception as e:
        tb = traceback.format_exc()
        logger.exception("[AutonomousAgent] execute_approved_work unexpected error: %s", e)
        
        # 일시적 오류이고 자동 재시도 활성화된 경우 재시도
        if auto_retry and _is_transient_error(e):
            logger.info("[AutonomousAgent] 일시적 오류 감지, 자동 재시도 시도")
            try:
                await asyncio.sleep(5)  # 짧은 대기 후 재시도
                return await execute_approved_work(record_id, auto_retry=False)  # 재시도는 1회만
            except Exception as retry_error:
                logger.error("[AutonomousAgent] 재시도도 실패: %s", retry_error)
                _record_and_notify_failure(
                    record_id, db,
                    "자율 주행 예외 발생 (재시도 실패)",
                    f"원본: {e}\n재시도: {retry_error}",
                    tb,
                )
                return False, f"예외 발생 (재시도 실패): {e}"
        
        _record_and_notify_failure(
            record_id, db,
            "자율 주행 예외 발생",
            str(e),
            tb,
        )
        return False, f"예외 발생: {e}"
