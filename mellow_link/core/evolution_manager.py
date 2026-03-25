"""
Evolution Manager - 코드 진화 파이프라인 (Phase 5: 자기 수정)

에이전트가 제안한 소스 코드 변경안을 관리합니다.
보안과 롤백을 최우선으로, 적용 전 dry-run/diff를 수행하고 실패 시 즉시 이전 버전으로 복구합니다.

삼권분립: Tower(Gemini) → Verdict(OpenAI) → Audit(Anthropic)
기술 검토: 삼권분립 체인 로직
"""

import difflib
import json
import logging
import os
import tempfile
import threading
import time
import uuid
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Tuple, Set, Any, Dict

from mellow_link.infra.memory_database import (
    get_memory_db,
    EvolutionLogRecord,
)
from mellow_link.core.evolution_schemas import EvolutionProposal, SecurityError
from mellow_link.core.evolution_logging import _log_evolution, _log_security_alert
from mellow_link.core.evolution_protocol import (
    _check_daily_limits,
    _get_protocol_auto_evolved_tag,
    _get_protocol_cost_cap_usd,
    _get_protocol_past_failure_limit,
    _get_protocol_post_apply_verify_enabled,
    _get_protocol_retry_limit,
    _record_cycle_end,
)
from mellow_link.core.evolution_preflight import (
    _is_in_auto_apply_scope,
    _is_large_scale,
    _parse_tower_report_for_plan,
    _run_post_apply_verification,
    pre_flight_check,
)

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════
# Evolution Manager
# ═══════════════════════════════

class EvolutionManager:
    """
    코드 변경 제안서 관리: 생성, dry-run/diff, Guardian 검수, 테스트, 적용, 롤백.
    파일 시스템 격리: services/, custom_tools/ 내에서만 활동 가능. ✅ verified
    is_maintenance=True 시 core/, infra/ 한시적 허용 (외부 주입만 가능).
    """

    # 기본 허용 하위 디렉터리 (core 등 핵심 엔진 제외, workspace 자율 작업 구역 포함)
    _ALLOWED_SUBDIRS = ("services", "custom_tools", "workspace")
    # 관리자 모드 시 추가 허용
    _MAINTENANCE_SUBDIRS = ("core", "infra")

    # os.replace 리트라이 (Windows 원자성 대비)
    _REPLACE_RETRIES = 3
    _REPLACE_RETRY_DELAY = 0.05

    def __init__(
        self,
        sandbox_root: Path,
        db=None,
        *,
        is_maintenance: bool = False,
    ):
        """
        Args:
            sandbox_root: 필수. 적용 허용 경로 루트 (Fail-Safe).
            db: MemoryDatabase (None이면 싱글톤 사용)
            is_maintenance: True 시 core/, infra/ 수정 허용. 에이전트는 스스로 켤 수 없고
                           외부에서 명시적 주입만 가능. Bootstrap Paradox 해결용.
        """
        if sandbox_root is None:
            _log_security_alert("INIT_BLOCKED", "sandbox_root is required (Fail-Safe)")
            raise SecurityError("sandbox_root는 필수입니다. 보안을 위해 반드시 설정해야 합니다.")
        self._sandbox_root = Path(sandbox_root).resolve()
        self.db = db or get_memory_db()
        self._is_maintenance = bool(is_maintenance)
        self._active_targets: Set[str] = set()
        self._targets_lock = threading.Lock()

        logger.info(
            "[EvolutionManager] Initialized sandbox=%s maintenance=%s",
            self._sandbox_root,
            self._is_maintenance,
        )
        _log_evolution("INIT", f"sandbox={self._sandbox_root} is_maintenance={self._is_maintenance}")

    def set_sandbox_root(self, path: Path) -> None:
        """적용 허용 경로(샌드박스) 루트. 초기화 후 변경 시에만 사용."""
        self._sandbox_root = Path(path).resolve()
        _log_evolution("SANDBOX_SET", str(self._sandbox_root))
        logger.info("[EvolutionManager] Sandbox root set: %s", self._sandbox_root)

    def _get_allowed_subdirs(self) -> Tuple[str, ...]:
        """현재 모드에 따른 허용 하위 디렉터리."""
        if self._is_maintenance:
            return self._ALLOWED_SUBDIRS + self._MAINTENANCE_SUBDIRS
        return self._ALLOWED_SUBDIRS

    def _acquire_target_lock(self, target_path: Path) -> bool:
        """대상 파일에 대한 락 획득. 이미 락 중이면 False."""
        key = str(target_path.resolve())
        with self._targets_lock:
            if key in self._active_targets:
                _log_security_alert(
                    "CONCURRENT_BLOCKED",
                    f"target={key} already locked by another proposal",
                )
                return False
            self._active_targets.add(key)
            return True

    def _release_target_lock(self, target_path: Path) -> None:
        """대상 파일 락 해제."""
        key = str(target_path.resolve())
        with self._targets_lock:
            self._active_targets.discard(key)

    def create_proposal(
        self,
        target_file: str,
        proposed_code: str,
        reason: str,
        author_agent_id: Optional[str] = None,
        root_goal_id: Optional[str] = None,
    ) -> Tuple[Optional[str], str]:
        """
        제안서 생성 및 DB 기록 (DRAFT). ✅ verified: root_goal_id 메타데이터 포함.
        Returns:
            (log_id, message)
        """
        try:
            log_id = str(uuid.uuid4())
            record = EvolutionLogRecord(
                id=log_id,
                target_file=target_file,
                proposed_code=proposed_code,
                reason=reason,
                diff_preview=None,
                status="DRAFT",
                previous_content=None,
                author_agent_id=author_agent_id,
                root_goal_id=root_goal_id,
            )
            if not self.db.save_evolution_log(record):
                _log_evolution("CREATE_FAIL", f"log_id={log_id} db_save_failed")
                return None, "[Error] 제안서 저장 실패"
            _log_evolution("CREATE", f"log_id={log_id} target={target_file} status=DRAFT")
            logger.info("[EvolutionManager] Proposal created: %s -> %s", log_id, target_file)
            return log_id, f"제안서 생성됨 (id={log_id})"
        except Exception as e:
            _log_evolution("CREATE_ERROR", str(e))
            logger.exception("[EvolutionManager] create_proposal failed")
            return None, f"[Error] {e!r}"

    def dry_run_and_diff(self, log_id: str) -> Tuple[bool, str]:
        """
        적용 전 dry-run: Diff 생성 및 문법 검사.
        상태를 DRY_RUN으로 갱신. ✅ verified
        """
        try:
            record = self.db.get_evolution_log(log_id)
            if not record:
                _log_evolution("DRY_RUN_FAIL", f"log_id={log_id} not_found")
                return False, "[Error] 제안서를 찾을 수 없습니다."
            if record.status not in ("DRAFT", "DRY_RUN"):
                return False, f"[Error] 해당 상태에서는 dry-run 불가: {record.status}"

            target_path = self._resolve_target(record.target_file)
            if not target_path:
                _log_security_alert("DRY_RUN_PATH_BLOCKED", f"log_id={log_id} target={record.target_file}")
                _log_evolution("DRY_RUN_FAIL", f"log_id={log_id} path_blocked={record.target_file}")
                return False, f"[Error] 허용된 경로가 아닙니다 (services/ 또는 custom_tools/만 가능): {record.target_file}"

            current_content = ""
            file_existed = target_path.exists()
            if file_existed:
                try:
                    current_content = target_path.read_text(encoding="utf-8")
                except Exception as e:
                    return False, f"[Error] 현재 파일 읽기 실패: {e!r}"

            diff_lines = list(difflib.unified_diff(
                current_content.splitlines(keepends=True),
                record.proposed_code.splitlines(keepends=True),
                fromfile=record.target_file,
                tofile=record.target_file + " (proposed)",
                lineterm="",
            ))
            diff_preview = "".join(diff_lines) if diff_lines else "(no diff)"

            # Python 파일만 AST 검사 (.md, .txt 등은 스킵)
            ext = Path(record.target_file).suffix.lower()
            if ext == ".py":
                try:
                    import ast
                    ast.parse(record.proposed_code)
                except SyntaxError as e:
                    self.db.update_evolution_log_status(log_id, "DRAFT", diff_preview=diff_preview)
                    _log_evolution("DRY_RUN_FAIL", f"log_id={log_id} syntax_error={e.msg}")
                    return False, f"[Error] 제안 코드 문법 오류: {e.msg} (line {e.lineno})"

            record.diff_preview = diff_preview
            record.status = "DRY_RUN"
            # 신규 파일(기존 미존재)인 경우 None으로 롤백 시 삭제 가능하게 함
            record.previous_content = current_content if file_existed else None
            self.db.save_evolution_log(record)
            _log_evolution("DRY_RUN", f"log_id={log_id} status=DRY_RUN file_existed={file_existed}")
            logger.info("[EvolutionManager] Dry-run done for %s", log_id)
            return True, diff_preview
        except SecurityError as e:
            _log_security_alert("DRY_RUN_SECURITY", str(e))
            raise
        except Exception as e:
            _log_evolution("DRY_RUN_ERROR", f"log_id={log_id} {e!r}")
            logger.exception("[EvolutionManager] dry_run_and_diff failed")
            return False, f"[Error] {e!r}"

    def run_guardian_audit(self, log_id: str) -> Tuple[bool, str]:
        """
        Guardian 2차 검수 실행. 승인 시 True, 거부 시 False.
        S1 다중 레이어 검증 순서 (엄격): (1) 정적 패턴 Risk Classifier
        (2) AST Pre-flight (3) LLM Guardian (4) Post-apply feedback.
        """
        try:
            record = self.db.get_evolution_log(log_id)
            if not record:
                return False, "[Error] 제안서를 찾을 수 없습니다."
            if record.status not in ("DRY_RUN", "TESTS_PENDING"):
                return False, f"[Error] Guardian 검수 불가 상태: {record.status}"

            # S1 Layer 1: 정적 패턴 — Level 3 시 LLM 호출 없이 즉시 거부
            from mellow_link.core.risk_classifier import classify_code_risk_level
            level, level_reason = classify_code_risk_level(record.proposed_code or "")
            if level == 3:
                _log_evolution("HARD_BLOCK", f"log_id={log_id} Level 3: {level_reason}")
                return False, f"[HARD_BLOCK] Level 3 위험 패턴 감지: {level_reason}"

            # S1 Layer 2: AST/문법 검사 — LLM 호출 전 선행
            ok_preflight, preflight_msg = pre_flight_check(
                record.target_file or "", record.proposed_code or ""
            )
            if not ok_preflight:
                _log_evolution("PREFLIGHT_FAIL", f"log_id={log_id} {preflight_msg}")
                return False, f"Pre-flight 실패: {preflight_msg}"

            from mellow_link.core.guardian_service import get_guardian_service
            guardian = get_guardian_service()
            result = guardian.audit_evolution_proposal_sync(
                record.target_file,
                record.proposed_code,
                record.reason,
            )
            if result.is_approved:
                _log_evolution("GUARDIAN_APPROVED", f"log_id={log_id}")
                return True, f"Guardian 승인: {result.critique[:200]}"
            # ✅ verified: REJECT 시 즉시 중단·사유 로그 (위험 점수 포함)
            risk = getattr(result, "risk_score", 0)
            _log_evolution(
                "GUARDIAN_REJECTED",
                f"log_id={log_id} risk_score={risk} critique={(result.critique or '')[:150]}"
            )
            logger.warning(
                "[EvolutionManager] Guardian 거부 → 진화 프로세스 중단. log_id=%s risk_score=%s",
                log_id, risk,
            )
            return False, f"Guardian 거부: {result.critique}\n수정 제안: {result.refined_recommendation}"
        except Exception as e:
            _log_evolution("GUARDIAN_ERROR", f"log_id={log_id} {e!r}")
            logger.exception("[EvolutionManager] run_guardian_audit failed")
            return False, f"[Error] {e!r}"

    def run_tests_and_approve(self, log_id: str) -> Tuple[bool, str]:
        """TestForge로 테스트 실행. 통과 시 APPROVAL_PENDING으로 전환. ⚠️ possible"""
        try:
            from mellow_link.core.test_forge import get_test_forge
            forge = get_test_forge(self.db)
            return forge.run_tests_for_proposal(log_id)
        except Exception as e:
            _log_evolution("TESTS_ERROR", f"log_id={log_id} {e!r}")
            logger.exception("[EvolutionManager] run_tests_and_approve failed")
            return False, f"[Error] {e!r}"

    def apply_proposal(self, log_id: str) -> Tuple[bool, str]:
        """
        제안 적용: Atomic Write로 파일에 proposed_code 기록.
        실패 시 즉시 previous_content로 복구(신규 파일이면 삭제)하고 ROLLED_BACK.
        """
        target_path: Optional[Path] = None
        try:
            record = self.db.get_evolution_log(log_id)
            if not record:
                _log_evolution("APPLY_FAIL", f"log_id={log_id} not_found")
                return False, "[Error] 제안서를 찾을 수 없습니다."
            if record.status not in ("DRY_RUN", "TESTS_PENDING", "APPROVAL_PENDING"):
                return False, f"[Error] 적용 불가 상태: {record.status}"

            target_path = self._resolve_target(record.target_file)
            if not target_path:
                _log_security_alert("APPLY_PATH_BLOCKED", f"log_id={log_id} target={record.target_file}")
                return False, f"[Error] 허용된 경로가 아닙니다: {record.target_file}"

            # 동시성 제어: 동일 파일에 대한 중복 적용 차단
            if not self._acquire_target_lock(target_path):
                return False, f"[Error] 대상 파일이 다른 제안에 의해 사용 중입니다: {record.target_file}"

            is_new_file = record.previous_content is None
            if is_new_file and target_path.exists():
                self._release_target_lock(target_path)
                return False, "[Error] 이전 내용 백업이 없어 롤백 불가. dry_run_and_diff를 먼저 실행하세요."

            backup = record.previous_content
            success = self._atomic_write(target_path, record.proposed_code)
            if not success:
                _log_evolution("APPLY_FAIL", f"log_id={log_id} atomic_write_failed")
                self._release_target_lock(target_path)
                return False, "[Error] 적용 쓰기 실패"

            try:
                self.db.update_evolution_log_status(log_id, "APPLIED")
                _log_evolution("APPLIED", f"log_id={log_id} target={record.target_file}")
                logger.info("[EvolutionManager] Applied proposal: %s -> %s", log_id, record.target_file)
                return True, f"[완료] 적용됨: {record.target_file}"
            except Exception as e:
                self._revert_file(target_path, backup, is_new_file)
                self.db.update_evolution_log_status(log_id, "ROLLED_BACK", previous_content=backup)
                _log_evolution("ROLLED_BACK", f"log_id={log_id} db_update_failed")
                return False, f"[Error] DB 갱신 실패로 롤백함: {e!r}"
            finally:
                if target_path is not None:
                    self._release_target_lock(target_path)
        except SecurityError as e:
            _log_security_alert("APPLY_SECURITY", str(e))
            if target_path is not None:
                self._release_target_lock(target_path)
            raise
        except Exception as e:
            _log_evolution("APPLY_ERROR", f"log_id={log_id} {e!r}")
            logger.exception("[EvolutionManager] apply_proposal failed")
            record = self.db.get_evolution_log(log_id) if log_id else None
            if record and record.target_file:
                target_path = self._resolve_target(record.target_file)
                if target_path:
                    try:
                        self._acquire_target_lock(target_path)
                        self._revert_file(
                            target_path,
                            record.previous_content,
                            record.previous_content is None,
                        )
                        self.db.update_evolution_log_status(
                            log_id, "ROLLED_BACK", previous_content=record.previous_content
                        )
                        _log_evolution("ROLLED_BACK", f"log_id={log_id} exception_recovery")
                    except SecurityError:
                        pass
                    finally:
                        if target_path:
                            self._release_target_lock(target_path)
            return False, f"[Error] {e!r}"

    def run_post_apply_feedback(self, log_id: str) -> Tuple[bool, str]:
        """
        S1 Layer 4: 적용 전/후 experience_ledger 성공률 비교 (Post-apply feedback).
        적용 후 에러율이 급증하면 feedback_failed=1(FAILED 취급) 설정 및 롤백 권고 반환.
        비동기/스케줄러에서 호출 가능, 메인 채팅 비간섭.
        """
        try:
            record = self.db.get_evolution_log(log_id)
            if not record or record.status != "APPLIED":
                return True, "스킵(APPLIED 아님)"
            applied_at = getattr(record, "applied_at", None)
            if not applied_at:
                return True, "스킵(applied_at 없음)"
            if getattr(record, "feedback_failed", 0) == 1:
                return False, "이미 FAILED 마킹됨. 롤백 권고."

            before_iso = applied_at.isoformat()
            before_entries = self.db.get_ledger_entries_before(before_iso, limit=50)
            after_entries = self.db.get_ledger_entries_since(before_iso, limit=200)

            if len(after_entries) < 5:
                return True, "샘플 부족(적용 후 5건 미만)"

            rate_before = (
                sum(1 for e in before_entries if e.is_success == 1) / len(before_entries)
                if before_entries else 0.5
            )
            rate_after = sum(1 for e in after_entries if e.is_success == 1) / len(after_entries)

            # 에러율 급증: 적용 후 성공률이 20%p 이상 하락 또는 고성공률 대비 급락
            if rate_after < rate_before - 0.2 or (rate_before >= 0.6 and rate_after < 0.4):
                self.db.set_evolution_feedback_failed(log_id)
                _log_evolution(
                    "FEEDBACK_FAILED",
                    f"log_id={log_id} rate_before={rate_before:.2f} rate_after={rate_after:.2f} → 롤백 권고"
                )
                return False, (
                    f"에러율 급증 감지 (적용 전 성공률 {rate_before:.1%} → 적용 후 {rate_after:.1%}). "
                    "EvolutionStatus.FAILED로 마킹됨. 롤백 프로세스 실행을 권고합니다."
                )
            return True, f"정상 (적용 후 성공률 {rate_after:.1%})"
        except Exception as e:
            logger.warning("[EvolutionManager] run_post_apply_feedback failed: %s", e)
            return True, f"검증 스킵: {e!r}"

    def run_post_apply_feedback_for_recent_applied(self, window_hours: float = 2.0) -> List[Tuple[str, bool, str]]:
        """
        ✅ verified: 최근 APPLIED 건에 대해 피드백 검사 실행 (스케줄러용).
        applied_at이 window_hours 이내이고 feedback_failed=0인 로그만 대상.
        Returns:
            [(log_id, ok, message), ...]
        """
        from datetime import timedelta
        results: List[Tuple[str, bool, str]] = []
        try:
            cutoff = datetime.now() - timedelta(hours=window_hours)
            applied_logs = self.db.get_evolution_logs_by_status(status="APPLIED", limit=50)
            for rec in applied_logs:
                if getattr(rec, "feedback_failed", 0) == 1:
                    continue
                applied_at = getattr(rec, "applied_at", None)
                if not applied_at or applied_at < cutoff:
                    continue
                ok, msg = self.run_post_apply_feedback(rec.id)
                results.append((rec.id, ok, msg))
        except Exception as e:
            logger.warning("[EvolutionManager] run_post_apply_feedback_for_recent_applied failed: %s", e)
        return results

    def rollback(self, log_id: str) -> Tuple[bool, str]:
        """적용된 제안을 이전 내용으로 복구하고 ROLLED_BACK으로 변경. 신규 파일이면 물리적 삭제."""
        target_path: Optional[Path] = None
        try:
            record = self.db.get_evolution_log(log_id)
            if not record:
                return False, "[Error] 제안서를 찾을 수 없습니다."
            if record.status != "APPLIED":
                return False, f"[Error] 롤백은 APPLIED 상태에서만 가능: {record.status}"
            # previous_content가 None이면 신규 생성 파일 → 롤백 시 물리적 삭제

            target_path = self._resolve_target(record.target_file)
            if not target_path:
                _log_security_alert("ROLLBACK_PATH_BLOCKED", f"log_id={log_id} target={record.target_file}")
                return False, f"[Error] 허용된 경로가 아닙니다: {record.target_file}"

            if not self._acquire_target_lock(target_path):
                return False, f"[Error] 대상 파일이 다른 작업에 의해 사용 중입니다: {record.target_file}"

            try:
                is_new_file = record.previous_content is None
                ok = self._revert_file(target_path, record.previous_content, is_new_file)
                if not ok:
                    _log_evolution("ROLLBACK_FAIL", f"log_id={log_id} revert_failed")
                    return False, "[Error] 롤백 실패"
                self.db.update_evolution_log_status(log_id, "ROLLED_BACK", previous_content=record.previous_content)
                _log_evolution("ROLLED_BACK", f"log_id={log_id} manual_rollback is_new_file={is_new_file}")
                logger.info("[EvolutionManager] Rolled back: %s", log_id)
                return True, f"[완료] 롤백됨: {record.target_file}"
            finally:
                self._release_target_lock(target_path)
        except SecurityError as e:
            _log_security_alert("ROLLBACK_SECURITY", str(e))
            if target_path is not None:
                self._release_target_lock(target_path)
            raise
        except Exception as e:
            _log_evolution("ROLLBACK_ERROR", f"log_id={log_id} {e!r}")
            logger.exception("[EvolutionManager] rollback failed")
            if target_path is not None:
                self._release_target_lock(target_path)
            return False, f"[Error] {e!r}"

    def _revert_file(self, target_path: Path, previous_content: Optional[str], is_new_file: bool) -> bool:
        """
        롤백 수행. is_new_file이면 파일 삭제, 아니면 previous_content로 덮어쓰기. ✅ verified
        """
        if is_new_file:
            try:
                if target_path.exists():
                    target_path.unlink()
                    _log_evolution("REVERT_DELETE", f"path={target_path} (신규 파일 롤백)")
                return True
            except Exception as e:
                logger.critical("[EvolutionManager] Rollback delete failed: %s", e)
                _log_security_alert("ROLLBACK_DELETE_FAILED", f"path={target_path} err={e}")
                return False
        if previous_content is not None:
            return self._atomic_write(target_path, previous_content)
        return True

    def _resolve_target(self, target_file: str) -> Optional[Path]:
        """
        절대 경로로 해석. sandbox + 허용 디렉터리 내부만 허용. ✅ verified
        sandbox_root 미설정 시 SecurityError 발생 (Fail-Safe).
        """
        if not self._sandbox_root:
            _log_security_alert(
                "SANDBOX_NOT_SET",
                "_resolve_target called without sandbox_root. Path resolution blocked.",
            )
            raise SecurityError(
                "sandbox_root가 설정되지 않았습니다. "
                "경로 해석을 차단합니다. EvolutionManager(sandbox_root=...)로 초기화하세요."
            )
        path = Path(target_file)
        if not path.is_absolute():
            base = Path(__file__).resolve().parent.parent
            path = (base / target_file).resolve()
        try:
            rel = path.relative_to(self._sandbox_root)
        except ValueError:
            _log_security_alert("PATH_OUTSIDE_SANDBOX", f"target={target_file}")
            return None
        parts = rel.parts
        allowed = self._get_allowed_subdirs()
        if not parts or parts[0] not in allowed:
            _log_security_alert("PATH_NOT_ALLOWED", f"target={target_file} allowed={allowed}")
            return None
        return path

    def _atomic_write(self, target_path: Path, content: str) -> bool:
        """임시 파일 작성 후 os.replace로 원자적 쓰기. Windows 대비 리트라이 포함."""
        try:
            target_path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(
                dir=target_path.parent,
                prefix=".evolution_",
                suffix=".tmp",
                text=True,
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(content)
                for attempt in range(self._REPLACE_RETRIES):
                    try:
                        os.replace(tmp, target_path)
                        return True
                    except OSError as e:
                        if attempt < self._REPLACE_RETRIES - 1:
                            time.sleep(self._REPLACE_RETRY_DELAY)
                        else:
                            raise
                return False
            except Exception:
                try:
                    if os.path.exists(tmp):
                        os.unlink(tmp)
                except Exception:
                    pass
                raise
        except Exception as e:
            logger.warning("[EvolutionManager] Atomic write failed: %s", e)
            return False

    def _rollback_file(self, target_path: Path, previous_content: Optional[str], is_new_file: bool = False) -> None:
        """파일 롤백 헬퍼 (기존 호환용)."""
        self._revert_file(target_path, previous_content, is_new_file)

    def _get_proposals_ledger_dir(self) -> Path:
        """logs/evolution_proposals/ 경로."""
        base = Path(__file__).resolve().parent.parent
        d = base / "logs" / "evolution_proposals"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _save_proposal_to_ledger(self, proposal: EvolutionProposal) -> Path:
        """제안서를 JSON으로 logs/evolution_proposals/에 저장."""
        ledger_dir = self._get_proposals_ledger_dir()
        path = ledger_dir / f"{proposal.id}.json"
        data = asdict(proposal)
        try:
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            _log_evolution("LEDGER_SAVE", f"id={proposal.id} path={path}")
        except Exception as e:
            logger.warning("[EvolutionManager] Ledger save failed: %s", e)
        return path

    async def run_evolution_cycle(
        self,
        user_request: str,
        audit_feedback: Optional[str] = None,
        max_retries: Optional[int] = None,
        _retry_count: int = 0,
        root_goal_id: Optional[str] = None,
    ) -> EvolutionProposal:
        """
        삼권분립 파이프라인: Tower → Verdict → Audit.
        검수 거부 시 피드백을 반영하여 max_retries회까지 재시도.
        EVOLUTION_PROTOCOL.json의 retry.max_retries 사용 (기본 1, 루프 방지).
        누적 비용이 cost_cap 초과 시 재시도 중단.
        
        Args:
            user_request: 사용자 요청
            audit_feedback: 검수 거부 피드백 (자동 재시도 시 Tower/Verdict에 주입)
            max_retries: 거부 시 재시도 최대 횟수. None이면 프로토콜 값 사용 (기본 1)
            _retry_count: 내부용 재시도 횟수
        """
        if max_retries is None:
            max_retries = _get_protocol_retry_limit()
        from mellow_link.infra.env_loader import load_dotenv_early
        load_dotenv_early()

        # [AIRGAP] ENABLE_GUARDIAN_APIS=0 이면 쿨다운/데일리리밋 등 어떤 로직도 타지 않고 즉시 반환
        try:
            from mellow_link.config.settings import get_settings
            if not get_settings().allow_guardian_api():
                logger.info("[Evolution] AIRGAP_BLOCK: ENABLE_GUARDIAN_APIS=0 (guardian apis disabled)")
                _log_evolution("AIRGAP_BLOCK", "ENABLE_GUARDIAN_APIS=0")
                return EvolutionProposal(
                    id=str(uuid.uuid4()),
                    user_request=user_request,
                    created_at=datetime.now().isoformat(),
                    error="AIRGAP_BLOCK: ENABLE_GUARDIAN_APIS=0 (guardian apis disabled)",
                    root_goal_id=root_goal_id,
                )
        except Exception:
            pass

        # [P1] 진입 시 일일 한도 및 쿨다운 체크 (_retry_count==0일 때만). 토글 ON일 때만 유지.
        if _retry_count == 0:
            can_proceed, limit_reason = _check_daily_limits()
            if not can_proceed:
                proposal = EvolutionProposal(
                    id=str(uuid.uuid4()),
                    user_request=user_request,
                    created_at=datetime.now().isoformat(),
                    error=f"DAILY_LIMIT: {limit_reason}",
                )
                _log_evolution("DAILY_LIMIT_BLOCK", limit_reason)
                _record_cycle_end()  # 차단 시에도 기록 (다음 진입 시 쿨다운)
                return proposal

        from mellow_link.core.provider_factory import get_client, generate_async, estimate_token_cost

        proposal_id = str(uuid.uuid4())
        total_token_usage = 0
        total_cost = 0.0
        total_latency = 0.0
        created_at = datetime.now().isoformat()
        proposal = EvolutionProposal(
            id=proposal_id,
            user_request=user_request,
            created_at=created_at,
            root_goal_id=root_goal_id,
        )

        # 중복 체크 1: user_request 유사도
        is_dup, dup_reason = self._check_duplicate_pending(user_request)
        if is_dup:
            proposal.error = f"SKIP_DUPLICATE: {dup_reason}"
            _log_evolution("SKIP_DUPLICATE", dup_reason)
            logger.info("[EvolutionManager] 중복 제안 스킵: %s", dup_reason)
            _record_cycle_end()
            return proposal

        base = Path(__file__).resolve().parent.parent
        log_dir = base / "logs"
        system_logs = ""
        for log_name in ("evolution.log", "system.log"):
            p = log_dir / log_name
            if p.exists():
                try:
                    content = p.read_text(encoding="utf-8", errors="replace")
                    system_logs += f"\n--- {log_name} ---\n{content[-50000:]}\n"
                except Exception:
                    pass

        if not system_logs.strip():
            system_logs = "(로그 없음)"

        # 과거 실패 사례 조회 (자가 학습): Tower 분석 전 주입. limit은 EVOLUTION_PROTOCOL.quality에서 로드
        past_failure_block = ""
        try:
            from mellow_link.core.agent_tools import get_past_failure_context
            past_failure_block = get_past_failure_context(
                target_file=None, limit=_get_protocol_past_failure_limit()
            )
            if past_failure_block:
                past_failure_block = f"\n{past_failure_block}\n"
        except Exception as e:
            logger.debug("[EvolutionManager] get_past_failure_context skipped: %s", e)

        feedback_block = ""
        if audit_feedback and audit_feedback.strip():
            feedback_block = f"""

## [중요] 이전 검수 거부 피드백 - 반드시 반영하여 수정하라
{audit_feedback.strip()}
"""

        # Step 1: Tower (Gemini)
        try:
            tower_cfg = get_client("google", role="tower")
            tower_prompt = f"""너는 시스템 관제탑(Tower)이다.
사용자 요청: {user_request}
{feedback_block}
{past_failure_block}

## 최근 시스템 로그 (일부)
{system_logs[:30000]}

## 요청
위 로그를 분석하고, 사용자 요청에 맞춰 "무엇을 고칠지"에 대한 분석 보고서를 작성하라.
[윤리·저작권] 제안 대상이 시스템 보안·윤리 가이드를 준수하는지, 타인 저작권 침해가 없는지 검토하라.
JSON 형식으로 출력:
{{"analysis": "분석 내용", "recommended_target": "수정 권장 파일 경로 (services/ 또는 custom_tools/ 또는 workspace/)", "priority": "high|medium|low"}}"""
            t0 = time.time()
            proposal.tower_report = await generate_async(
                tower_cfg.provider, tower_cfg.model, tower_prompt, tower_cfg.api_key
            )
            total_latency += time.time() - t0
            tok, c = estimate_token_cost(tower_prompt, len(proposal.tower_report or ""), tower_cfg.provider)
            total_token_usage += tok
            total_cost += c
            _log_evolution("TOWER_DONE", f"id={proposal_id}")

            # 중복 체크 2: recommended_target 동일 여부
            rec_target = ""
            try:
                tr = proposal.tower_report or ""
                raw = tr
                if "```json" in raw:
                    raw = raw.split("```json")[1].split("```")[0].strip()
                elif "```" in raw:
                    raw = raw.split("```")[1].split("```")[0].strip()
                try:
                    obj = json.loads(raw)
                    rec_target = (obj.get("recommended_target") or "").strip()
                except json.JSONDecodeError:
                    if "recommended_target" in tr:
                        start = tr.find('"recommended_target"')
                        if start >= 0:
                            rest = tr[start + len('"recommended_target"'):].lstrip(": \"'")
                            candidates = [rest.find(c) for c in ('"', "'", ",", "}") if rest.find(c) >= 0]
                            end = min(candidates) if candidates else len(rest)
                            rec_target = rest[:end].strip().strip('"\'')
            except Exception:
                pass
            if rec_target:
                is_dup, dup_reason = self._check_duplicate_pending(user_request, recommended_target=rec_target)
                if is_dup:
                    proposal.error = f"SKIP_DUPLICATE: {dup_reason}"
                    _log_evolution("SKIP_DUPLICATE", dup_reason)
                    logger.info("[EvolutionManager] 중복 제안 스킵 (대상 파일): %s", dup_reason)
                    self._save_proposal_to_ledger(proposal)
                    _record_cycle_end()
                    return proposal

            # 스마트 배팅: 가성비 예측 - 성공 확률 낮고 비용만 높을 것으로 예상되면 제안
            try:
                from mellow_link.core.agent_tools import predict_low_roi
                is_low_roi, low_roi_msg = predict_low_roi(target_file=rec_target or None)
                if is_low_roi and low_roi_msg:
                    proposal.error = f"LOW_ROI_SUGGESTION: {low_roi_msg}"
                    _log_evolution("LOW_ROI_SUGGESTION", low_roi_msg)
                    try:
                        from mellow_link.core.agent_tools import save_evolution_result
                        save_evolution_result(
                            proposal_id=proposal_id,
                            target_file=rec_target or "",
                            user_request=user_request,
                            verdict_code="",
                            audit_critique=low_roi_msg,
                            status="REJECTED",
                            token_usage=total_token_usage,
                            cost=total_cost,
                            latency=total_latency,
                        )
                    except Exception:
                        pass
                    self._save_proposal_to_ledger(proposal)
                    _record_cycle_end()
                    return proposal
            except Exception as ex:
                logger.debug("[EvolutionManager] predict_low_roi skipped: %s", ex)

            # 계획 우선 보고: 대규모 수정 시 Proposed Plan을 먼저 보고하고 진행 승인 대기
            if _is_large_scale(proposal.tower_report):
                proposal.plan_pending = True
                self._save_proposal_to_ledger(proposal)
                analysis, rec_target_parsed, _ = _parse_tower_report_for_plan(proposal.tower_report)
                plan_summary = (analysis or proposal.tower_report[:800])[:800]
                try:
                    from mellow_link.services.notification_service import notify_evolution_plan_ready
                    notify_evolution_plan_ready(
                        proposal.id, user_request, plan_summary,
                        target_hint=rec_target_parsed or rec_target,
                    )
                except Exception as e:
                    logger.warning("[EvolutionManager] Plan 보고 알림 실패: %s", e)
                _log_evolution("PLAN_PENDING", f"id={proposal_id} large_scale, 진행 승인 대기")
                _record_cycle_end()
                return proposal
        except Exception as e:
            proposal.error = f"Tower step failed: {e}"
            logger.exception("[EvolutionManager] Tower step failed")
            self._save_proposal_to_ledger(proposal)
            _record_cycle_end()
            return proposal

        # Step 2: Verdict (OpenAI)
        try:
            from mellow_link.core.verdict_prompts import get_verdict_io_standards
            verdict_cfg = get_client("openai", role="verdict")
            io_standards = get_verdict_io_standards()
            verdict_prompt = f"""너는 코드 판결관(Verdict)이다.
사용자 요청: {user_request}
{feedback_block}
{past_failure_block}

## Tower 분석 보고서
{proposal.tower_report[:8000]}

{io_standards}

## 요청
위 분석을 바탕으로 구체적인 수정안을 작성하라.

**중요: target_file 확장자에 맞는 형식으로 proposed_code를 작성하라.**
- .py 파일: 전체 Python 코드 (pathlib 사용 필수)
- .md 파일: 전체 Markdown 문서 (```python 사용하지 말 것)
- .txt, .json 등: 해당 형식의 전체 내용

**필수 규칙:**
1. proposed_code는 전체 파일 내용을 완전하게 제공 (절대 중간에 끊지 말 것)
2. reason에는 반드시 구체적인 수정 사유를 명시
3. target_file은 services/, custom_tools/, workspace/ 중 하나의 하위

JSON 형식으로만 출력:
{{"target_file": "경로", "proposed_code": "전체 파일 내용 (형식 일치)", "reason": "명확한 수정 사유"}}"""
            t0_v = time.time()
            verdict_text = await generate_async(
                verdict_cfg.provider, verdict_cfg.model, verdict_prompt, verdict_cfg.api_key
            )
            total_latency += time.time() - t0_v
            tok, c = estimate_token_cost(verdict_prompt, len(verdict_text or ""), verdict_cfg.provider)
            total_token_usage += tok
            total_cost += c
            try:
                if "```json" in verdict_text:
                    verdict_text = verdict_text.split("```json")[1].split("```")[0].strip()
                elif "```" in verdict_text:
                    verdict_text = verdict_text.split("```")[1].split("```")[0].strip()
                v = json.loads(verdict_text)
                proposal.verdict_target_file = (v.get("target_file") or "").strip()
                proposal.verdict_proposed_code = v.get("proposed_code", "") or ""
                proposal.verdict_reason = (v.get("reason") or "").strip() or proposal.user_request[:500]
            except json.JSONDecodeError:
                proposal.verdict_reason = verdict_text[:2000]
            _log_evolution("VERDICT_DONE", f"id={proposal_id} target={proposal.verdict_target_file}")

            # 자가 검증: pre_flight_check 통과 시에만 Audit 진행
            ok_preflight, preflight_msg = pre_flight_check(
                proposal.verdict_target_file, proposal.verdict_proposed_code
            )
            if not ok_preflight:
                proposal.error = f"pre_flight_check 실패: {preflight_msg}"
                _log_evolution("PREFLIGHT_FAIL", preflight_msg)
                logger.warning("[EvolutionManager] pre_flight_check 실패: %s", preflight_msg)
                try:
                    from mellow_link.core.agent_tools import save_evolution_result
                    save_evolution_result(
                        proposal_id=proposal_id,
                        target_file=proposal.verdict_target_file or "",
                        user_request=user_request,
                        verdict_code=proposal.verdict_proposed_code or "",
                        audit_critique=preflight_msg,
                        status="FAIL",
                        token_usage=total_token_usage,
                        cost=total_cost,
                        latency=total_latency,
                    )
                except Exception as ex:
                    logger.debug("[EvolutionManager] save_evolution_result (preflight) skipped: %s", ex)
                self._save_proposal_to_ledger(proposal)
                _record_cycle_end()
                return proposal
        except Exception as e:
            proposal.error = f"Verdict step failed: {e}"
            logger.exception("[EvolutionManager] Verdict step failed")
            self._save_proposal_to_ledger(proposal)
            _record_cycle_end()
            return proposal

        # Step 3: Audit (Anthropic)
        try:
            audit_cfg = get_client("anthropic", role="audit")
            # 대상 파일 확장자에 맞는 코드블록 언어 (README.md → markdown)
            ext = Path(proposal.verdict_target_file or "").suffix.lower()
            code_lang = "markdown" if ext in (".md", ".mdx") else ("json" if ext == ".json" else "python" if ext == ".py" else "text")
            code_preview = proposal.verdict_proposed_code[:12000]  # truncation 완화
            audit_prompt = f"""너는 시니어 소프트웨어 아키텍트이자 감사관(Audit)이다.
에이전트가 제안한 자기 수정 내용이 안전하고 타당한지 검토하라.

## 대상 파일
{proposal.verdict_target_file}

## 수정 사유
{proposal.verdict_reason[:2000]}

## 제안된 내용 (형식: {code_lang})
```{code_lang}
{code_preview}
```

## 요청
1. Python인 경우: os, subprocess, eval, exec 등 위험 API 사용 여부 확인.
2. Markdown/문서인 경우: 내용 완전성, 구조적 일관성 검토.
3. 논리적 오류나 보안 취약점 검토.
4. [윤리·저작권] 제작 내용이 시스템 보안·윤리 가이드를 준수하는가? 타인 저작권 침해 여부?
5. JSON만 출력:
{{"is_approved": true 또는 false, "critique": "검토 의견", "refined_recommendation": "거부 시 수정 제안"}}"""
            t0_a = time.time()
            audit_text = await generate_async(
                audit_cfg.provider, audit_cfg.model, audit_prompt, audit_cfg.api_key
            )
            total_latency += time.time() - t0_a
            tok, c = estimate_token_cost(audit_prompt, len(audit_text or ""), audit_cfg.provider)
            total_token_usage += tok
            total_cost += c
            try:
                if "```json" in audit_text:
                    audit_text = audit_text.split("```json")[1].split("```")[0].strip()
                elif "```" in audit_text:
                    audit_text = audit_text.split("```")[1].split("```")[0].strip()
                a = json.loads(audit_text)
                proposal.audit_approved = bool(a.get("is_approved", False))
                proposal.audit_critique = a.get("critique", "")
                proposal.audit_refined = a.get("refined_recommendation", "")
                proposal.audit_risk_score = max(0, min(100, int(a.get("risk_score", 0))))
                if proposal.audit_risk_score >= 70:
                    proposal.audit_approved = False
            except json.JSONDecodeError:
                proposal.audit_critique = audit_text[:1500]
            _log_evolution("AUDIT_DONE", f"id={proposal_id} approved={proposal.audit_approved} risk={getattr(proposal,'audit_risk_score',0)}")
        except Exception as e:
            proposal.error = f"Audit step failed: {e}"
            logger.exception("[EvolutionManager] Audit step failed")

        self._save_proposal_to_ledger(proposal)

        # 가성비 브리핑 및 진화 원장 기록
        try:
            from mellow_link.core.agent_tools import save_evolution_result, get_cost_efficiency_briefing
            proposal.cost_efficiency_briefing = get_cost_efficiency_briefing(total_cost, proposal.verdict_target_file)
            status = "SUCCESS" if proposal.audit_approved else "REJECTED"
            save_evolution_result(
                proposal_id=proposal_id,
                target_file=proposal.verdict_target_file or "",
                user_request=user_request,
                verdict_code=proposal.verdict_proposed_code or "",
                audit_critique=proposal.audit_critique or proposal.error or "",
                status=status,
                token_usage=total_token_usage,
                cost=total_cost,
                latency=total_latency,
            )
        except Exception as ex:
            logger.debug("[EvolutionManager] save_evolution_result (audit) skipped: %s", ex)

        # 검수 거부 시 피드백 반영하여 자동 재시도 (자가 발전)
        # 단, 누적 비용이 프로토콜 상한 초과 시 재시도 중단 (자원 낭비 방지)
        cost_cap = _get_protocol_cost_cap_usd()
        cost_exceeded = cost_cap > 0 and total_cost > cost_cap
        if cost_exceeded:
            _log_evolution(
                "COST_CAP_STOP",
                f"id={proposal_id} total_cost={total_cost:.4f} > cap={cost_cap} (retry aborted)",
            )
            logger.info(
                "[EvolutionManager] 재시도 중단: 누적 비용 %.4f USD > 상한 %.2f USD",
                total_cost, cost_cap,
            )
        can_retry = (
            not proposal.audit_approved
            and _retry_count < max_retries
            and not proposal.error
            and not cost_exceeded
        )
        if can_retry:
            parts = []
            if proposal.audit_critique and proposal.audit_critique.strip():
                parts.append(f"검토 의견: {proposal.audit_critique.strip()}")
            if proposal.audit_refined and proposal.audit_refined.strip():
                parts.append(f"수정 제안: {proposal.audit_refined.strip()}")
            if not parts:
                parts.append("(구체적 피드백 없음 - 코드 품질을 개선하여 재제안하라)")
            next_feedback = "\n".join(parts)
            _log_evolution("AUTO_RETRY", f"id={proposal_id} attempt={_retry_count + 1}/{max_retries}")
            return await self.run_evolution_cycle(
                user_request, audit_feedback=next_feedback,
                max_retries=max_retries, _retry_count=_retry_count + 1,
            )

        # 통과 시: 위험 점수 70 미만이고 auto_apply_scope 내이면 자동 적용
        target = (proposal.verdict_target_file or "").replace("\\", "/")
        is_temp_script = "autonomous_script.py" in target
        risk = getattr(proposal, "audit_risk_score", 0)
        if risk >= 70:
            _log_evolution("AUTO_APPLY_BLOCKED", f"id={proposal_id} risk_score={risk} (>=70)")
            proposal.audit_approved = False
        if proposal.audit_approved and not is_temp_script:
            if _is_in_auto_apply_scope(target):
                # Auto-Apply: 승인 우회, 즉시 적용
                ok, msg = self.apply_from_proposal(proposal.id, is_auto_apply=True)
                _log_evolution("AUTO_APPLY", f"id={proposal_id} target={target} ok={ok} msg={msg[:100]}")
                if ok:
                    logger.info("[EvolutionManager] Auto-apply 완료: %s", msg[:200])
                    try:
                        from mellow_link.services.notification_service import notify_evolution_applied
                        tag = _get_protocol_auto_evolved_tag()
                        notify_evolution_applied(
                            proposal.id, target,
                            f"{tag} [자동 적용] {proposal.user_request[:100]}",
                        )
                    except Exception as e:
                        logger.debug("[EvolutionManager] Auto-apply 알림 생략: %s", e)
                else:
                    logger.warning("[EvolutionManager] Auto-apply 실패: %s", msg[:200])
            else:
                # 승인 대기 알림
                try:
                    from mellow_link.services.notification_service import notify_evolution_proposal_ready
                    notify_evolution_proposal_ready(
                        proposal.id, proposal.user_request, audit_approved=True,
                        target_file=proposal.verdict_target_file or "",
                        cost_efficiency_briefing=getattr(proposal, "cost_efficiency_briefing", "") or "",
                    )
                except Exception as e:
                    logger.debug("[EvolutionManager] Telegram notify skipped: %s", e)
        else:
            _log_evolution("FINAL_REJECT", f"id={proposal_id} attempts={_retry_count + 1} (no notify)")

        _record_cycle_end()
        return proposal

    def _check_duplicate_pending(
        self,
        user_request: str,
        recommended_target: Optional[str] = None,
    ) -> Tuple[bool, str]:
        """
        승인 대기 중인 제안과 중복 여부 검사.
        Returns:
            (is_duplicate, reason)
        """
        pending = self.list_waiting_for_approval()
        if not pending:
            return False, ""

        req_norm = (user_request or "").strip()
        for p in pending:
            other_req = (p.get("user_request") or "").strip()
            if req_norm and other_req:
                ratio = difflib.SequenceMatcher(None, req_norm.lower(), other_req.lower()).ratio()
                if ratio >= 0.75:
                    return True, f"user_request 유사 (ratio={ratio:.2f}, 기존 ID:{p.get('id','')[:8]})"
            other_target = (p.get("verdict_target_file") or "").strip().replace("\\", "/")
            if recommended_target and other_target:
                t1 = recommended_target.strip().replace("\\", "/").lower()
                t2 = other_target.lower()
                if t1 == t2 or t1.endswith(t2) or t2.endswith(t1):
                    return True, f"동일 대상 파일 ({other_target}, 기존 ID:{p.get('id','')[:8]})"
        return False, ""

    def list_waiting_for_approval(self) -> List[Dict[str, Any]]:
        """승인 대기 중인 제안서 목록 (audit_approved, 미적용, 미거부)."""
        ledger_dir = self._get_proposals_ledger_dir()
        out = []
        for p in sorted(ledger_dir.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                if not data.get("audit_approved"):
                    continue
                if data.get("applied") or data.get("rejected"):
                    continue
                out.append({
                    "id": data.get("id", p.stem),
                    "user_request": (data.get("user_request") or "")[:500],
                    "verdict_target_file": data.get("verdict_target_file") or "",
                    "verdict_reason": (data.get("verdict_reason") or "")[:300],
                    "created_at": data.get("created_at", ""),
                })
            except Exception:
                continue
        return out[:20]

    def get_all_proposals(self) -> List[Dict[str, Any]]:
        """제안서 목록 (ledger 전체, 로그/목록용)."""
        ledger_dir = self._get_proposals_ledger_dir()
        if not ledger_dir.exists():
            return []
        out = []
        for p in sorted(ledger_dir.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                out.append({
                    "id": data.get("id", p.stem),
                    "user_request": (data.get("user_request") or "")[:500],
                    "verdict_target_file": data.get("verdict_target_file") or "",
                    "created_at": data.get("created_at", ""),
                    "audit_approved": data.get("audit_approved", False),
                    "applied": data.get("applied", False),
                    "rejected": data.get("rejected", False),
                })
            except Exception:
                continue
        return out[:100]

    def reject_proposal(self, proposal_id: str) -> Tuple[bool, str]:
        """제안서 거부 처리 (ledger에 rejected 플래그 기록)."""
        ledger_dir = self._get_proposals_ledger_dir()
        path = ledger_dir / f"{proposal_id}.json"
        if not path.exists():
            return False, "제안서를 찾을 수 없습니다."
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            data["rejected"] = True
            data["rejected_at"] = datetime.now().isoformat()
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            return True, "거부 처리되었습니다."
        except Exception as e:
            return False, str(e)

    def _load_proposal(self, proposal_id: str) -> Optional[EvolutionProposal]:
        """ledger에서 제안서 로드."""
        ledger_dir = self._get_proposals_ledger_dir()
        path = ledger_dir / f"{proposal_id}.json"
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return EvolutionProposal(
                id=data.get("id", proposal_id),
                user_request=data.get("user_request", ""),
                tower_report=data.get("tower_report", ""),
                verdict_target_file=data.get("verdict_target_file", ""),
                verdict_proposed_code=data.get("verdict_proposed_code", ""),
                verdict_reason=data.get("verdict_reason", ""),
                audit_approved=bool(data.get("audit_approved", False)),
                audit_critique=data.get("audit_critique", ""),
                audit_refined=data.get("audit_refined", ""),
                created_at=data.get("created_at", ""),
                error=data.get("error"),
                plan_pending=bool(data.get("plan_pending", False)),
            )
        except Exception as e:
            logger.warning("[EvolutionManager] Load proposal failed: %s", e)
            return None

    async def run_evolution_proceed_from_plan(self, proposal_id: str) -> Optional[EvolutionProposal]:
        """
        계획 진행 승인 후 Verdict → pre_flight_check → Audit 실행.
        plan_pending 상태의 제안서에 대해 호출.
        """
        proposal = self._load_proposal(proposal_id)
        if not proposal:
            return None
        if not proposal.plan_pending:
            logger.warning("[EvolutionManager] proceed_from_plan: plan_pending 아님: %s", proposal_id)
            return None
        if not proposal.tower_report:
            proposal.error = "tower_report 없음"
            self._save_proposal_to_ledger(proposal)
            return proposal

        proposal.plan_pending = False
        user_request = proposal.user_request
        total_token_usage = 0
        total_cost = 0.0
        total_latency = 0.0
        feedback_block = ""
        rec_target = ""
        try:
            _, rec_target, _ = _parse_tower_report_for_plan(proposal.tower_report)
        except Exception:
            pass
        past_failure_block = ""
        try:
            from mellow_link.core.agent_tools import get_past_failure_context
            past_failure_block = get_past_failure_context(
                target_file=rec_target or None, limit=_get_protocol_past_failure_limit()
            )
            if past_failure_block:
                past_failure_block = f"\n{past_failure_block}\n"
        except Exception:
            pass

        # Step 2: Verdict
        try:
            from mellow_link.core.provider_factory import get_client, generate_async, estimate_token_cost
            from mellow_link.core.verdict_prompts import get_verdict_io_standards
            verdict_cfg = get_client("openai", role="verdict")
            io_standards = get_verdict_io_standards()
            verdict_prompt = f"""너는 코드 판결관(Verdict)이다.
사용자 요청: {user_request}
{feedback_block}
{past_failure_block}

## Tower 분석 보고서
{proposal.tower_report[:8000]}

{io_standards}

## 요청
위 분석을 바탕으로 구체적인 수정안을 작성하라.

**중요: target_file 확장자에 맞는 형식으로 proposed_code를 작성하라.**
- .py 파일: 전체 Python 코드 (pathlib 사용 필수)
- .md 파일: 전체 Markdown 문서 (```python 사용하지 말 것)
- .txt, .json 등: 해당 형식의 전체 내용

**필수 규칙:**
1. proposed_code는 전체 파일 내용을 완전하게 제공 (절대 중간에 끊지 말 것)
2. reason에는 반드시 구체적인 수정 사유를 명시
3. target_file은 services/, custom_tools/, workspace/ 중 하나의 하위

JSON 형식으로만 출력:
{{"target_file": "경로", "proposed_code": "전체 파일 내용 (형식 일치)", "reason": "명확한 수정 사유"}}"""
            t0 = time.time()
            verdict_text = await generate_async(
                verdict_cfg.provider, verdict_cfg.model, verdict_prompt, verdict_cfg.api_key
            )
            total_latency += time.time() - t0
            tok, c = estimate_token_cost(verdict_prompt, len(verdict_text or ""), verdict_cfg.provider)
            total_token_usage += tok
            total_cost += c
            try:
                if "```json" in verdict_text:
                    verdict_text = verdict_text.split("```json")[1].split("```")[0].strip()
                elif "```" in verdict_text:
                    verdict_text = verdict_text.split("```")[1].split("```")[0].strip()
                v = json.loads(verdict_text)
                proposal.verdict_target_file = (v.get("target_file") or "").strip()
                proposal.verdict_proposed_code = v.get("proposed_code", "") or ""
                proposal.verdict_reason = (v.get("reason") or "").strip() or proposal.user_request[:500]
            except json.JSONDecodeError:
                proposal.verdict_reason = verdict_text[:2000]
            _log_evolution("VERDICT_DONE", f"id={proposal_id} target={proposal.verdict_target_file}")

            ok_preflight, preflight_msg = pre_flight_check(
                proposal.verdict_target_file, proposal.verdict_proposed_code
            )
            if not ok_preflight:
                proposal.error = f"pre_flight_check 실패: {preflight_msg}"
                _log_evolution("PREFLIGHT_FAIL", preflight_msg)
                try:
                    from mellow_link.core.agent_tools import save_evolution_result
                    save_evolution_result(
                        proposal_id=proposal_id,
                        target_file=proposal.verdict_target_file or "",
                        user_request=user_request,
                        verdict_code=proposal.verdict_proposed_code or "",
                        audit_critique=preflight_msg,
                        status="FAIL",
                        token_usage=total_token_usage,
                        cost=total_cost,
                        latency=total_latency,
                    )
                except Exception:
                    pass
                self._save_proposal_to_ledger(proposal)
                return proposal
        except Exception as e:
            proposal.error = f"Verdict step failed: {e}"
            logger.exception("[EvolutionManager] proceed_from_plan Verdict failed")
            self._save_proposal_to_ledger(proposal)
            return proposal

        # Step 3: Audit
        try:
            audit_cfg = get_client("anthropic", role="audit")
            ext = Path(proposal.verdict_target_file or "").suffix.lower()
            code_lang = "markdown" if ext in (".md", ".mdx") else ("json" if ext == ".json" else "python" if ext == ".py" else "text")
            code_preview = proposal.verdict_proposed_code[:12000]
            audit_prompt = f"""너는 시니어 소프트웨어 아키텍트이자 감사관(Audit)이다.

에이전트가 제안한 자기 수정 내용이 안전하고 타당한지 검토하라.

## 대상 파일
{proposal.verdict_target_file}

## 수정 사유
{proposal.verdict_reason[:2000]}

## 제안된 내용 (형식: {code_lang})
```{code_lang}
{code_preview}
```

## 요청
1. Python인 경우: os, subprocess, eval, exec 등 위험 API 사용 여부 확인.
2. Markdown/문서인 경우: 내용 완전성, 구조적 일관성 검토.
3. 논리적 오류나 보안 취약점 검토.
4. [윤리·저작권] 제작 내용이 시스템 보안·윤리 가이드를 준수하는가? 타인 저작권 침해 여부?
5. JSON만 출력:
{{"is_approved": true 또는 false, "critique": "검토 의견", "refined_recommendation": "거부 시 수정 제안"}}"""
            t0_a = time.time()
            audit_text = await generate_async(
                audit_cfg.provider, audit_cfg.model, audit_prompt, audit_cfg.api_key
            )
            total_latency += time.time() - t0_a
            tok, c = estimate_token_cost(audit_prompt, len(audit_text or ""), audit_cfg.provider)
            total_token_usage += tok
            total_cost += c
            try:
                if "```json" in audit_text:
                    audit_text = audit_text.split("```json")[1].split("```")[0].strip()
                elif "```" in audit_text:
                    audit_text = audit_text.split("```")[1].split("```")[0].strip()
                a = json.loads(audit_text)
                proposal.audit_approved = bool(a.get("is_approved", False))
                proposal.audit_critique = a.get("critique", "")
                proposal.audit_refined = a.get("refined_recommendation", "")
                proposal.audit_risk_score = max(0, min(100, int(a.get("risk_score", 0))))
                if proposal.audit_risk_score >= 70:
                    proposal.audit_approved = False
            except json.JSONDecodeError:
                proposal.audit_critique = audit_text[:1500]
            _log_evolution("AUDIT_DONE", f"id={proposal_id} approved={proposal.audit_approved} risk={getattr(proposal,'audit_risk_score',0)}")
        except Exception as e:
            proposal.error = f"Audit step failed: {e}"
            logger.exception("[EvolutionManager] proceed_from_plan Audit failed")

        self._save_proposal_to_ledger(proposal)

        try:
            from mellow_link.core.agent_tools import save_evolution_result, get_cost_efficiency_briefing
            proposal.cost_efficiency_briefing = get_cost_efficiency_briefing(total_cost, proposal.verdict_target_file)
            status = "SUCCESS" if proposal.audit_approved else "REJECTED"
            save_evolution_result(
                proposal_id=proposal_id,
                target_file=proposal.verdict_target_file or "",
                user_request=user_request,
                verdict_code=proposal.verdict_proposed_code or "",
                audit_critique=proposal.audit_critique or proposal.error or "",
                status=status,
                token_usage=total_token_usage,
                cost=total_cost,
                latency=total_latency,
            )
        except Exception:
            pass

        target = (proposal.verdict_target_file or "").replace("\\", "/")
        is_temp_script = "autonomous_script.py" in target
        if proposal.audit_approved and not is_temp_script:
            if _is_in_auto_apply_scope(target):
                ok, msg = self.apply_from_proposal(proposal.id, is_auto_apply=True)
                _log_evolution("AUTO_APPLY", f"proceed id={proposal.id[:8]} target={target} ok={ok}")
                if ok:
                    logger.info("[EvolutionManager] proceed_from_plan Auto-apply 완료: %s", msg[:200])
                    try:
                        from mellow_link.services.notification_service import notify_evolution_applied
                        tag = _get_protocol_auto_evolved_tag()
                        notify_evolution_applied(
                            proposal.id, target,
                            f"{tag} [자동 적용] {proposal.user_request[:100]}",
                        )
                    except Exception as e:
                        logger.debug("[EvolutionManager] Auto-apply 알림 생략: %s", e)
            else:
                try:
                    from mellow_link.services.notification_service import notify_evolution_proposal_ready
                    notify_evolution_proposal_ready(
                        proposal.id, proposal.user_request, audit_approved=True,
                        target_file=proposal.verdict_target_file or "",
                        cost_efficiency_briefing=getattr(proposal, "cost_efficiency_briefing", "") or "",
                    )
                except Exception as e:
                    logger.debug("[EvolutionManager] Telegram notify skipped: %s", e)
        return proposal

    async def run_evolution_refine_cycle(self, proposal_id: str) -> Optional[EvolutionProposal]:
        """
        검수 거부된 제안서를 기반으로 피드백 반영 후 재시도.
        audit_critique, audit_refined를 Tower/Verdict에 주입하여 개선된 제안을 생성한다.
        
        Returns:
            새 EvolutionProposal 또는 실패 시 None
        """
        proposal = self._load_proposal(proposal_id)
        if not proposal:
            logger.warning("[EvolutionManager] Refine: proposal not found: %s", proposal_id)
            return None
        if proposal.audit_approved:
            logger.warning("[EvolutionManager] Refine: proposal was approved, no need to refine: %s", proposal_id)
            return None
        parts = []
        if proposal.audit_critique and proposal.audit_critique.strip():
            parts.append(f"검토 의견: {proposal.audit_critique.strip()}")
        if proposal.audit_refined and proposal.audit_refined.strip():
            parts.append(f"수정 제안: {proposal.audit_refined.strip()}")
        if not parts:
            parts.append("(구체적 피드백 없음 - 코드 품질을 개선하여 재제안하라)")
        audit_feedback = "\n".join(parts)
        _log_evolution("REFINE_START", f"from={proposal_id} feedback_len={len(audit_feedback)}")
        return await self.run_evolution_cycle(proposal.user_request, audit_feedback=audit_feedback)

    def apply_from_proposal(self, proposal_id: str, is_auto_apply: bool = False) -> Tuple[bool, str]:
        """
        EvolutionProposal JSON 기반 적용.
        create_proposal -> dry_run -> (audit 이미 완료) APPROVAL_PENDING -> apply_proposal.
        Sandbox: services/, custom_tools/ 만 허용. SecurityError로 차단.
        is_auto_apply=True이면 ledger에 auto_evolved_tag 기록 (검토용).
        """
        ledger_dir = self._get_proposals_ledger_dir()
        path = ledger_dir / f"{proposal_id}.json"
        if not path.exists():
            return False, f"[Error] 제안서를 찾을 수 없습니다: {proposal_id}"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            return False, f"[Error] JSON 파싱 실패: {e}"
        target_file = data.get("verdict_target_file", "").strip()
        proposed_code = data.get("verdict_proposed_code", "")
        reason = data.get("verdict_reason", "") or data.get("user_request", "")
        if not target_file or not proposed_code:
            return False, "[Error] target_file 또는 proposed_code가 비어 있습니다."
        if not data.get("audit_approved", False):
            return False, "[Error] 검수 미승인 상태에서는 적용할 수 없습니다."
        root_goal_id = (data.get("root_goal_id") or "").strip() or None
        log_id, msg = self.create_proposal(
            target_file, proposed_code, reason,
            author_agent_id="triple_chain",
            root_goal_id=root_goal_id,
        )
        if not log_id:
            return False, msg
        ok, out = self.dry_run_and_diff(log_id)
        if not ok:
            return False, out
        self.db.update_evolution_log_status(log_id, "APPROVAL_PENDING")
        ok, out = self.apply_proposal(log_id)
        if ok and _get_protocol_post_apply_verify_enabled():
            target_path = self._resolve_target(target_file)
            if target_path and target_path.exists():
                verify_ok, verify_msg = _run_post_apply_verification(target_path, target_file)
                if not verify_ok:
                    _log_evolution("VERIFY_FAIL_ROLLBACK", f"log_id={log_id} {verify_msg[:100]}")
                    logger.warning("[EvolutionManager] 검증 실패, 롤백: %s", verify_msg[:200])
                    roll_ok, roll_msg = self.rollback(log_id)
                    if roll_ok:
                        return False, f"[검증 실패로 롤백됨] {verify_msg}"
                    return False, f"[검증 실패] 롤백 시도 실패: {roll_msg}"
        if ok:
            try:
                data["applied"] = True
                data["applied_at"] = datetime.now().isoformat()
                if is_auto_apply:
                    data["auto_evolved_tag"] = _get_protocol_auto_evolved_tag()
                path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception as e:
                logger.debug("[EvolutionManager] applied 플래그 저장 실패: %s", e)
        return ok, out


# ═══════════════════════════════════════════════
# Singleton
# ═══════════════════════════════

_evolution_manager_instance: Optional[EvolutionManager] = None


def get_evolution_manager(
    db=None,
    sandbox_root: Optional[Path] = None,
    *,
    is_maintenance: bool = False,
) -> EvolutionManager:
    """EvolutionManager 싱글톤. sandbox_root 필수 (없으면 mellow_link 루트 사용)."""
    global _evolution_manager_instance
    if _evolution_manager_instance is None:
        base = Path(__file__).resolve().parent.parent
        root = sandbox_root if sandbox_root is not None else base
        _evolution_manager_instance = EvolutionManager(
            sandbox_root=root,
            db=db,
            is_maintenance=is_maintenance,
        )
    return _evolution_manager_instance
