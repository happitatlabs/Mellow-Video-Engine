"""
Evolution Pre-flight: 코드 검증, 적용 후 검증, 자동 적용 범위, Tower 파싱.

pre_flight_check: AST/JSON 문법 검사
_run_post_apply_verification: py_compile + smoke 테스트
_is_in_auto_apply_scope: 프로토콜 기반 자동 적용 경로 판정
_parse_tower_report_for_plan, _is_large_scale: Tower 보고서 파싱
"""
import ast
import json
import logging
import subprocess
import sys
from pathlib import Path
from typing import Tuple

from mellow_link.core.evolution_logging import _log_security_alert
from mellow_link.core.evolution_protocol import (
    _get_protocol_auto_apply_scope,
    _get_protocol_path_scope_core,
    _load_evolution_protocol,
)

logger = logging.getLogger(__name__)


def pre_flight_check(target_file: str, proposed_code: str) -> Tuple[bool, str]:
    """
    코드 생성 후 실행 가능 여부 자가 검증.
    통과 시에만 결재를 올리도록 함.
    Returns:
        (passed, error_message) - passed가 False면 error_message에 사유.
    """
    if not proposed_code or not proposed_code.strip():
        return False, "제안된 코드가 비어 있습니다."
    ext = Path(target_file or "").suffix.lower()
    try:
        if ext == ".py":
            ast.parse(proposed_code)
            return True, ""
        if ext == ".json":
            json.loads(proposed_code)
            return True, ""
        # .md, .txt 등: 문법 검증 없이 통과
        return True, ""
    except json.JSONDecodeError as e:
        return False, f"JSON 구문 오류: {str(e)[:200]}"
    except SyntaxError as e:
        return False, f"Python 구문 오류 (line {e.lineno or '?'}): {str(e.msg or e)[:200]}"
    except Exception as e:
        return False, f"검증 실패: {str(e)[:200]}"


def _run_post_apply_verification(target_path: Path, target_file: str) -> Tuple[bool, str]:
    """
    적용 직후 검증: py_compile(.py) + smoke 테스트(앱 로드).
    Returns:
        (passed, message)
    """
    ext = Path(target_file or "").suffix.lower()
    if ext == ".py":
        try:
            result = subprocess.run(
                [sys.executable, "-m", "py_compile", str(target_path)],
                capture_output=True,
                text=True,
                timeout=10,
                cwd=str(Path(__file__).resolve().parent.parent),
            )
            if result.returncode != 0:
                err = (result.stderr or result.stdout or "").strip()[:500]
                return False, f"py_compile 실패: {err}"
        except subprocess.TimeoutExpired:
            return False, "py_compile 타임아웃"
        except Exception as e:
            return False, f"검증 오류: {e!r}"
    # smoke: 앱 로드 가능 여부 확인 (시스템 크래시 방지)
    try:
        protocol = _load_evolution_protocol()
        cfg = protocol.get("post_apply_verify") or {}
        timeout = 15
        if isinstance(cfg.get("smoke_timeout_sec"), (int, float)):
            timeout = max(5, min(int(cfg["smoke_timeout_sec"]), 60))
        result = subprocess.run(
            [sys.executable, "-c", "from mellow_link.main import app"],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(Path(__file__).resolve().parent.parent),
        )
        if result.returncode != 0:
            err = (result.stderr or result.stdout or "").strip()[:400]
            return False, f"smoke 테스트 실패 (앱 로드 불가): {err}"
    except subprocess.TimeoutExpired:
        return False, "smoke 테스트 타임아웃"
    except Exception as e:
        logger.debug("[EvolutionManager] post_apply smoke skipped: %s", e)
    return True, "검증 통과"


def _is_in_auto_apply_scope(target_file: str) -> bool:
    """대상 경로가 auto_apply_scope에 포함되는지. path_scope.core=denied면 core/ 차단."""
    if not target_file or not target_file.strip():
        return False
    norm = target_file.replace("\\", "/").strip().strip("/").lower()
    parts = norm.split("/")
    if not parts:
        return False

    # [P0] path_scope.core가 "denied"면 core/ 하위 원천 차단
    first = parts[0].lower()
    if first == "core":
        path_scope_core = _get_protocol_path_scope_core()
        if path_scope_core == "denied":
            logger.warning("[EvolutionManager] Security: Access to core path denied by protocol.")
            _log_security_alert("PATH_SCOPE_DENIED", f"target={target_file} path_scope.core=denied")
            return False

    scope = _get_protocol_auto_apply_scope()
    if not scope:
        return False
    scope_lower = [s.lower() for s in scope]
    return first in scope_lower or any(norm.startswith(s.lower() + "/") for s in scope)


def _parse_tower_report_for_plan(tr: str) -> Tuple[str, str, str]:
    """tower_report에서 analysis, recommended_target, priority 추출."""
    analysis, rec_target, priority = "", "", ""
    try:
        raw = tr or ""
        if "```json" in raw:
            raw = raw.split("```json")[1].split("```")[0].strip()
        elif "```" in raw:
            raw = raw.split("```")[1].split("```")[0].strip()
        obj = json.loads(raw)
        analysis = (obj.get("analysis") or "")[:2000]
        rec_target = (obj.get("recommended_target") or "").strip()
        priority = (obj.get("priority") or "").strip().lower()
    except Exception:
        pass
    return analysis, rec_target, priority


def _is_large_scale(tower_report: str) -> bool:
    """대규모 수정 여부. True면 계획 우선 보고 단계로 전환."""
    analysis, rec_target, priority = _parse_tower_report_for_plan(tower_report)
    if priority == "high":
        return True
    if len(analysis) > 400:
        return True
    return False
