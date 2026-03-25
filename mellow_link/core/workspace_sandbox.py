"""
Workspace Sandbox - 자율 에이전트 전용 작업 구역 제어

지침: mellow_link/workspace/ 폴더 내로만 파일 쓰기 허용.
core/, config/, .env 등은 절대 수정 불가 (읽기만 허용).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

# 코어 보호: 수정 금지 경로 (자율 에이전트는 쓰기 불가)
_PROTECTED_WRITE_ROOTS = ("core", "infra", "config", "evolution", "main.py")
_PROTECTED_FILES = (".env", "config.py", "settings.py")

# 자율 작업 허용 구역
WORKSPACE_SUBDIR = "workspace"

# [ABSOLUTE_PATH_ANCHORING] 프로젝트 루트를 하드코딩된 상수로 고정
_WORKSPACE_ROOT_CONSTANT = Path(r"D:\AI_Project\mellow_link\workspace")


class WorkspaceSandboxError(PermissionError):
    """workspace 샌드박스 정책 위반."""


def get_workspace_root() -> Path:
    """
    [ABSOLUTE_PATH_ANCHORING] mellow_link/workspace/ 절대 경로 반환.
    하드코딩된 상수를 사용하여 경로 변형(melody_link 등)을 방지.
    """
    # 상수 경로가 존재하지 않으면 생성
    _WORKSPACE_ROOT_CONSTANT.mkdir(parents=True, exist_ok=True)
    return _WORKSPACE_ROOT_CONSTANT


def resolve_workspace_path(rel_path: str, base: Optional[Path] = None) -> Optional[Path]:
    """
    상대 경로를 workspace 내 절대 경로로 변환.
    경로 탈출(..) 시도 시 None 반환.
    """
    root = base or get_workspace_root()
    if ".." in rel_path:
        return None
    path = (root / rel_path.lstrip("/")).resolve()
    try:
        path.relative_to(root)
        return path
    except ValueError:
        return None


def can_write_to_path(target: str | Path, sandbox_root: Optional[Path] = None) -> Tuple[bool, str]:
    """
    자율 에이전트가 해당 경로에 쓰기할 수 있는지 검사.
    
    Returns:
        (허용 여부, 거부 사유)
    """
    base = sandbox_root or Path(__file__).resolve().parents[1]
    path = Path(target)
    if not path.is_absolute():
        path = (base / target).resolve()
    
    try:
        rel = path.relative_to(base)
    except ValueError:
        return False, "sandbox 루트 밖의 경로"
    
    parts = rel.parts
    if not parts:
        return False, "잘못된 경로"
    
    # 코어 보호: 수정 금지
    first = parts[0].lower()
    for protected in _PROTECTED_WRITE_ROOTS:
        if first == protected.lower():
            return False, f"core 보호: {first}/ 수정 금지"
    
    if path.name.lower() in (f.lower() for f in _PROTECTED_FILES):
        return False, f"core 보호: {path.name} 수정 금지"
    
    # workspace/ 내에서만 쓰기 허용
    if first != WORKSPACE_SUBDIR:
        return False, f"작업 구역 제한: {WORKSPACE_SUBDIR}/ 내에서만 쓰기 허용"
    
    return True, ""


def ensure_workspace_safe_path(rel_path: str) -> Optional[Path]:
    """
    workspace 내 안전한 경로 반환. 탈출 시도 시 None.
    """
    return resolve_workspace_path(rel_path)
