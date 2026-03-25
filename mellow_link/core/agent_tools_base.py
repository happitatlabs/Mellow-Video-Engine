"""
Agent Tools 공통 기반: 보안 초기화, 경로 헬퍼, dotenv 로딩, 공통 상수.

모든 agent_tools_*.py 도메인 모듈이 이 모듈에서 공통 함수/상수를 import한다.
이 모듈을 import하면 SecurityManager가 1회 초기화되고 freeze된다.
"""
import logging
import os
from pathlib import Path
from typing import Optional, List, Any, Tuple

from mellow_link.core.tool_registry import tool, registry as _tool_registry
from mellow_link.core.path_manager import PathManager
from mellow_link.core.security_manager import SecurityManager, SecurityBlocked
from mellow_link.core.workspace_sandbox import get_workspace_root
from mellow_link.utils.report_masking import (
    mask_report_content,
    is_too_sensitive,
)

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════
# Emergency Lockdown
# ═══════════════════════════════════════════════

def _is_emergency_lockdown() -> bool:
    v = os.getenv("MELLOW_EMERGENCY_LOCKDOWN")
    if not isinstance(v, str):
        return False
    return v.strip().lower() in {"1", "true", "yes", "y", "on"}


# ═══════════════════════════════════════════════
# 외부 API 클라이언트 (선택적 의존성 방어)
# ═══════════════════════════════════════════════

try:
    import requests  # type: ignore
except Exception:  # pragma: no cover
    requests = None


def _require_requests() -> Optional[str]:
    """requests 의존성 체크. 실패 시 사용자용 에러 문자열 반환."""
    if requests is None:
        return "[Error] requests 라이브러리가 설치되어 있지 않습니다. requirements를 확인하고 설치 후 다시 시도하세요."
    return None


# ═══════════════════════════════════════════════
# Critical Security Fix (V-04, V-05): Immutable Security Level
# ═══════════════════════════════════════════════
#
# - SECURITY_LEVEL은 "프로세스 시작(모듈 import) 시점"에만 1회 읽고 고정한다.
# - 런타임 중 os.environ 변경이나 .env 수정으로 보안 등급이 바뀌면 안 된다.
# - 따라서 _get_security()는 os.getenv()를 재호출하지 않고, 고정된 인스턴스만 반환한다.

_FROZEN_SECURITY_LEVEL: str = "NORMAL"
_FROZEN_SECURITY: Optional[SecurityManager] = None


def _get_security() -> SecurityManager:
    """
    보안 매니저 반환 (IMMUTABLE).

    CRITICAL:
      - 이 함수는 런타임에 환경변수를 재평가하지 않는다.
      - 모듈 import 시점에 1회 고정된 _FROZEN_SECURITY만 반환한다.
    """
    global _FROZEN_SECURITY
    if _FROZEN_SECURITY is None:
        # 모듈 import 과정에서 _FROZEN_SECURITY를 초기화하지 못한 경우를 위한 안전장치
        _FROZEN_SECURITY = SecurityManager.from_env()
    return _FROZEN_SECURITY


def _pm() -> PathManager:
    """현재 SecurityManager에 연결된 PathManager를 반환."""
    return _get_security().path_manager


# ═══════════════════════════════════════════════
# Tool output truncation (p95 latency)
# ═══════════════════════════════════════════════

def truncate_list(
    items: List[Any],
    limit: int,
    offset: int = 0,
) -> Tuple[List[Any], int, bool, Optional[int]]:
    """
    Slice a list with limit/offset and return metadata for tool output caps.

    Returns:
        (sliced_items, total_count, truncated, next_offset or None)
    """
    total = len(items)
    end = offset + limit
    sliced = items[offset:end]
    truncated = end < total
    next_offset = end if truncated else None
    return (sliced, total, truncated, next_offset)


def format_truncation_footer(
    total_count: int,
    returned_count: int,
    next_offset: Optional[int],
    message: str = "Results truncated to N items. Narrow your query for more.",
) -> str:
    """Build [TRUNCATED] footer line for tool text output."""
    msg = message.replace("N", str(returned_count))
    part = f"[TRUNCATED] returned {returned_count}/{total_count}."
    if next_offset is not None:
        part += f" next_offset={next_offset}"
    part += f" {msg}"
    return part


_DOTENV_LOADED = False


# ═══════════════════════════════════════════════
# dotenv / sandbox 초기화 헬퍼
# ═══════════════════════════════════════════════

def _load_dotenv_once() -> None:
    """
    .env 파일을 1회 로드하여 os.getenv()로 읽히도록 합니다.
    override=False로 설정하여 이미 설정된 OS 환경변수는 덮어쓰지 않습니다.
    """
    global _DOTENV_LOADED
    if _DOTENV_LOADED:
        return
    _DOTENV_LOADED = True

    try:
        from dotenv import load_dotenv  # type: ignore
    except Exception:
        return

    # 프로젝트 루트 후보: 런처가 설정한 환경 변수를 우선 사용
    base_path = (
        os.environ.get("MELLOW_LINK_PROJECT_ROOT")
        or os.environ.get("PROJECT_ROOT")
        or str(Path(__file__).resolve().parents[2])
    )

    # 우선순위:
    # 1) <project_root>/.env
    # 2) <project_root>/mellow_link/.env
    load_dotenv(dotenv_path=os.path.join(base_path, ".env"), override=False)
    load_dotenv(dotenv_path=os.path.join(base_path, "mellow_link", ".env"), override=False)


def _compute_sandbox_root_for_security() -> Path:
    """
    SecurityManager의 sandbox_root를 1회 계산하여 고정한다.

    NOTE:
      - PROJECT_ROOT가 레포 상위(D:\\AI_Project)로 잡히는 경우가 많아,
        그 아래에 mellow_link/가 있으면 그쪽을 sandbox로 본다.
      - 이미 mellow_link/ 루트가 들어오면 그대로 사용한다.
    """
    root_hint = os.environ.get("MELLOW_LINK_PROJECT_ROOT") or os.environ.get("PROJECT_ROOT") or ""
    base = Path(root_hint).resolve() if root_hint else Path(__file__).resolve().parents[1]

    if (base / "core").exists() and (base / "config").exists():
        return base
    if (base / "mellow_link" / "core").exists() and (base / "mellow_link" / "config").exists():
        return (base / "mellow_link").resolve()
    return Path(__file__).resolve().parents[1]


# -----------------------------------------------------------------------------
# Freeze Security Level at import time (IMMUTABLE)
# -----------------------------------------------------------------------------
try:
    _load_dotenv_once()
except Exception:
    pass

_lvl = (os.getenv("SECURITY_LEVEL") or os.getenv("MELLOW_SECURITY_LEVEL") or "").strip().upper()
if _lvl not in {"EASY", "NORMAL", "HARD"}:
    _lvl = "NORMAL"

_FROZEN_SECURITY_LEVEL = _lvl
_FROZEN_SECURITY = SecurityManager(level=_FROZEN_SECURITY_LEVEL, sandbox_root=_compute_sandbox_root_for_security())


# ═══════════════════════════════════════════════
# [WORKSPACE_SAFE_ZONE_ENFORCEMENT] 경로가 workspace 내부인지 검증
# ═══════════════════════════════════════════════

def _normalize_workspace_path(input_path: str) -> str:
    """
    [cite: 2026-02-09] 상대 경로를 workspace 기준 절대 경로로 정규화.
    
    Args:
        input_path: 입력 경로 문자열
        
    Returns:
        정규화된 절대 경로 문자열
    """
    workspace_root = get_workspace_root()
    path_obj = Path(input_path)
    
    # 이미 절대 경로이고 workspace 내부인 경우 그대로 사용
    if path_obj.is_absolute():
        try:
            if path_obj.resolve().is_relative_to(workspace_root.resolve()):
                return str(path_obj.resolve())
        except ValueError:
            pass  # 절대 경로지만 workspace 밖인 경우 아래 정규화 로직으로 처리
    
    # 상대 경로 정규화
    clean_path = input_path.strip().replace('\\', '/')
    
    # ".", "./", "workspace", "workspace/" → workspace 루트
    if clean_path in [".", "./", "workspace", "workspace/"]:
        return str(workspace_root)
    # "workspace/" 접두어 제거
    elif clean_path.startswith("workspace/"):
        return str(workspace_root / clean_path[len("workspace/"):])
    elif clean_path.startswith("./"):
        return str(workspace_root / clean_path[len("./"):])
    else:
        # 일반 상대 경로는 workspace 기준으로 결합
        return str(workspace_root / clean_path)


def _normalize_read_path(input_path: str) -> str:
    """
    읽기 전용 경로 정규화.
    - 상대 경로는 mellow_link(sandbox) 루트 기준으로 해석
    - ".", "./", "workspace", "workspace/"는 기존 호환을 위해 workspace 루트로 해석
    """
    sandbox_root = _pm().root
    workspace_root = get_workspace_root()
    path_obj = Path(input_path)

    # 절대 경로는 그대로 두고, 이후 보안 검증 단계에서 sandbox 포함 여부를 검사
    if path_obj.is_absolute():
        return str(path_obj.resolve())

    clean_path = input_path.strip().replace("\\", "/")
    if clean_path in [".", "./", "workspace", "workspace/"]:
        return str(workspace_root)
    if clean_path.startswith("workspace/"):
        return str(workspace_root / clean_path[len("workspace/"):])
    if clean_path.startswith("./"):
        clean_path = clean_path[len("./"):]
    return str(sandbox_root / clean_path)


def _ensure_path_inside_workspace(resolved_path: Path, input_path_display: str) -> Optional[str]:
    """
    [ABSOLUTE_PATH_ANCHORING] resolved_path가 mellow_link/workspace 내부인지 검증.
    문자열 포함 + 실제 하위 경로 검증을 수행. 위반 시 에러 메시지 반환.
    """
    try:
        ws = get_workspace_root()
        resolved = resolved_path.resolve()
        resolved_str = str(resolved)
        workspace_str = str(ws.resolve())
        
        # [PATH_INTEGRITY_VALIDATION] 문자열 포함 검증
        if "mellow_link" not in resolved_str.lower() or "workspace" not in resolved_str.lower():
            logger.critical("\033[91m[PATH_GATE_BLOCKED] Path integrity violation: '%s' (mellow_link/workspace not in path)\033[0m", input_path_display)
            return (
                f"[ERROR] 너는 허가되지 않은 경로 '{input_path_display}'에 접근하려 했다. "
                "너의 모든 작업은 오직 'mellow_link/workspace' 내부에서만 허용된다. 경로를 수정하여 다시 시도하라."
            )
        
        # [PATH_INTEGRITY_VALIDATION] 실제 하위 경로 검증
        if not resolved.is_relative_to(ws):
            logger.critical("\033[91m[PATH_GATE_BLOCKED] Path outside workspace: '%s'\033[0m", input_path_display)
            return (
                f"[ERROR] 너는 허가되지 않은 경로 '{input_path_display}'에 접근하려 했다. "
                "너의 모든 작업은 오직 'mellow_link/workspace' 내부에서만 허용된다. 경로를 수정하여 다시 시도하라."
            )
        
        # [PATH_INTEGRITY_VALIDATION] 경로 변형 방지 (melody_link 등 오타 차단)
        if "melody" in resolved_str.lower() or ("mellow" not in resolved_str.lower() and "workspace" in resolved_str.lower()):
            logger.critical("\033[91m[PATH_GATE_BLOCKED] Path mutation detected: '%s' (mellow mutated to melody or missing)\033[0m", input_path_display)
            return (
                f"[ERROR] 경로 변형 감지: '{input_path_display}' (mellow가 melody 등으로 변형되었거나 누락됨). "
                "정확한 경로 'mellow_link/workspace'를 사용하라."
            )
    except Exception as e:
        logger.critical("\033[91m[PATH_GATE_BLOCKED] Path validation exception: '%s' (%s)\033[0m", input_path_display, e)
        return None
    return None


def _ensure_path_inside_sandbox_for_read(resolved_path: Path, input_path_display: str) -> Optional[str]:
    """
    읽기 전용 경로가 mellow_link(sandbox) 내부인지 검증.
    """
    try:
        sandbox_root = _pm().root.resolve()
        resolved = resolved_path.resolve()
        if not resolved.is_relative_to(sandbox_root):
            logger.critical(
                "\033[91m[PATH_GATE_BLOCKED] Path outside sandbox(read): '%s'\033[0m",
                input_path_display,
            )
            return (
                f"[ERROR] 허가되지 않은 경로 '{input_path_display}'에 접근하려 했다. "
                "읽기 작업은 오직 'mellow_link' 내부에서만 허용된다."
            )
    except Exception as e:
        logger.critical(
            "\033[91m[PATH_GATE_BLOCKED] Read sandbox validation exception: '%s' (%s)\033[0m",
            input_path_display,
            e,
        )
        return None
    return None
