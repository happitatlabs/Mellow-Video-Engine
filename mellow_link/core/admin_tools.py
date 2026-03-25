"""
Admin Tools - 관리자 전용 도구

관리자만 사용할 수 있는 특수 도구들입니다.
"""

import logging
from pathlib import Path
from typing import List, Optional

from mellow_link.core.workspace_sandbox import get_workspace_root

logger = logging.getLogger(__name__)


def list_admin_trash() -> str:
    """
    관리자 전용: .admin_trash 폴더의 파일 목록을 반환합니다.
    
    이 함수는 관리자만 호출할 수 있으며, 삭제된 파일들을 확인할 수 있습니다.
    
    Returns:
        .admin_trash 폴더의 파일 목록
    """
    workspace_root = get_workspace_root()
    admin_trash_root = workspace_root / ".admin_trash"
    
    if not admin_trash_root.exists():
        return "[정보] .admin_trash 폴더가 없습니다 (삭제된 파일 없음)"
    
    files = []
    try:
        for item in admin_trash_root.rglob("*"):
            if item.is_file():
                rel_path = item.relative_to(admin_trash_root)
                files.append(str(rel_path.as_posix()))
    except Exception as e:
        return f"[오류] 목록 조회 실패: {e}"
    
    if not files:
        return "[정보] 삭제된 파일이 없습니다"
    
    result = ["[관리자 전용] 삭제된 파일 목록 (.admin_trash):\n"]
    for i, file_path in enumerate(sorted(files), 1):
        result.append(f"{i}. {file_path}")
    
    result.append(f"\n총 {len(files)}개 파일")
    return "\n".join(result)


def restore_from_admin_trash(file_path: str, restore_to: Optional[str] = None) -> str:
    """
    관리자 전용: .admin_trash에서 파일을 복구합니다.
    
    Args:
        file_path: .admin_trash 내부의 파일 경로
        restore_to: 복구할 위치 (기본값: 원본 위치)
    
    Returns:
        복구 결과 메시지
    """
    workspace_root = get_workspace_root()
    admin_trash_root = workspace_root / ".admin_trash"
    
    # .admin_trash 내부 파일 경로
    trash_file = admin_trash_root / file_path
    
    if not trash_file.exists():
        return f"[오류] 파일을 찾을 수 없습니다: {file_path}"
    
    if not trash_file.is_file():
        return f"[오류] 파일만 복구할 수 있습니다: {file_path}"
    
    # 복구 위치 결정
    if restore_to:
        restore_path = workspace_root / restore_to
    else:
        # 원본 위치 추정 (타임스탬프 제거)
        # 파일명에서 _YYYYMMDD_HHMMSS 패턴 제거
        import re
        original_name = re.sub(r'_\d{8}_\d{6}(?=\.[^.]+$|$)', '', trash_file.name)
        restore_path = workspace_root / original_name
    
    # 복구 위치가 workspace 내부인지 확인
    try:
        restore_path.relative_to(workspace_root)
    except ValueError:
        return f"[오류] 복구 위치가 workspace 외부입니다: {restore_to}"
    
    try:
        import shutil
        restore_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(trash_file), str(restore_path))
        logger.info("[admin_tools] 파일 복구 완료: %s -> %s", file_path, restore_path)
        
        return (
            f"[완료] 파일이 복구되었습니다.\n"
            f"원본: {file_path}\n"
            f"복구 위치: {restore_path.relative_to(workspace_root)}"
        )
    except Exception as e:
        logger.error("[admin_tools] 파일 복구 실패: %s", e)
        return f"[오류] 파일 복구 실패: {e}"


def permanently_delete_from_admin_trash(file_path: str) -> str:
    """
    관리자 전용: .admin_trash에서 파일을 영구 삭제합니다.
    
    ⚠️ 경고: 이 작업은 되돌릴 수 없습니다!
    
    Args:
        file_path: .admin_trash 내부의 파일 경로
    
    Returns:
        삭제 결과 메시지
    """
    workspace_root = get_workspace_root()
    admin_trash_root = workspace_root / ".admin_trash"
    
    trash_file = admin_trash_root / file_path
    
    if not trash_file.exists():
        return f"[오류] 파일을 찾을 수 없습니다: {file_path}"
    
    # .admin_trash 내부인지 확인
    try:
        trash_file.relative_to(admin_trash_root)
    except ValueError:
        return f"[오류] .admin_trash 내부 파일만 영구 삭제할 수 있습니다: {file_path}"
    
    try:
        trash_file.unlink()
        logger.warning("[admin_tools] 파일 영구 삭제: %s", file_path)
        
        return f"[완료] 파일이 영구 삭제되었습니다: {file_path}\n⚠️ 이 작업은 되돌릴 수 없습니다."
    except Exception as e:
        logger.error("[admin_tools] 파일 영구 삭제 실패: %s", e)
        return f"[오류] 파일 삭제 실패: {e}"
