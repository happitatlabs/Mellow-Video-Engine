"""
Agent 경로 정규화 / 보안 검증 유틸리티.

워크스페이스(BASE_PATH) 내부로 경로를 제한하고, 상대 경로 → 절대 경로 변환,
상위 디렉토리 탈출(..) 차단, 도구 인자 내 경로 자동 정규화를 수행한다.

의존성:
  - core/workspace_sandbox.py : get_workspace_root (fallback)
"""
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from mellow_link.core.workspace_sandbox import get_workspace_root

logger = logging.getLogger(__name__)

# [ABSOLUTE_PATH_ANCHORING] 프로젝트 루트를 하드코딩된 상수로 고정
# [cite: 2026-02-09] 시스템의 절대 성역 정의
BASE_PATH = Path(r"D:\AI_Project\mellow_link\workspace").resolve()
_WORKSPACE_ROOT_CONSTANT = BASE_PATH  # 하위 호환성 유지

# 경로 인자를 받는 도구별 인자 이름 (도구명 -> [경로 인자 키 목록], 리스트 값은 여러 경로)
_PATH_ARG_BY_TOOL: Dict[str, List[str]] = {
    "read_file": ["file_path"],
    "write_file": ["file_path"],
    "list_directory": ["dir_path", "directory"],
    "cleanup_file": ["file_paths"],
    "animate_image": ["image_path"],
}


def _get_workspace_root() -> Path:
    """
    시스템 기준 경로 = AI_PROJECT/mellow_link/workspace (하드코딩된 상수).
    mellow를 다른 단어로 변형하는 것을 엄격히 금지.
    """
    # 상수 경로가 존재하지 않으면 생성
    BASE_PATH.mkdir(parents=True, exist_ok=True)
    return BASE_PATH


def _normalize_path(path_str: str, workspace_root: Path) -> Tuple[str, Optional[str], Optional[str]]:
    """
    [cite: 2026-02-09] 에브의 멍청한 상대 경로 입력을 완벽한 절대 경로 칩으로 세탁하는 함수.
    
    Args:
        path_str: 입력 경로 문자열
        workspace_root: 워크스페이스 루트 경로 (BASE_PATH와 동일)
        
    Returns:
        (정규화된_경로, 에러_메시지, 수정_메시지)
        - 성공 시: (정규화된_경로, None, 수정_메시지 또는 None)
        - 실패 시: (원본, 에러_메시지, None)
    """
    if not path_str or not isinstance(path_str, str):
        return path_str, None, None
    
    original_path = path_str
    # 1. 공백 제거 및 슬래시 통일
    clean_path = path_str.strip().replace('\\', '/')
    
    # 2. '.' 이나 'workspace' 단독 입력 시 BASE_PATH로 직행 [cite: 2026-02-09]
    # 절대 경로 형태(/workspace)도 처리
    if clean_path in [".", "./", "workspace", "workspace/", "/workspace", "/workspace/"]:
        normalized = str(BASE_PATH)
        correction_msg = f"[경로 자동 교정] '{original_path}' → '{normalized}' (워크스페이스 루트로 해석)"
        return normalized, None, correction_msg
    
    # 3. 불필요한 접두어 제거 (예: workspace/test.txt -> test.txt)
    # 절대 경로 형태(/workspace/...)도 처리
    was_prefixed = False
    if clean_path.startswith("/workspace/"):
        clean_path = clean_path[len("/workspace/"):]
        was_prefixed = True
    elif clean_path.startswith("workspace/"):
        clean_path = clean_path[len("workspace/"):]
        was_prefixed = True
    elif clean_path.startswith("./"):
        clean_path = clean_path[len("./"):]
        was_prefixed = True
    
    # 4. BASE_PATH와 결합 및 실제 경로 계산
    try:
        # join 후 resolve()로 '..' 등 위험 요소 제거
        target = (BASE_PATH / clean_path).resolve()
        
        # 5. [cite: 2026-02-09] 보안 가드레일: 결과가 BASE_PATH 내부인지 검증
        target_str = str(target)
        base_str = str(BASE_PATH)
        
        if not target_str.startswith(base_str):
            # 성역 밖으로 나가려 하면 가차 없이 쳐냄
            return original_path, f"Access Denied: Path {target} is outside of sanctuary.", None
        
        # 경로가 수정되었을 경우 성공 메시지 생성
        correction_msg = None
        if was_prefixed or original_path != target_str:
            correction_msg = f"[경로 자동 교정] '{original_path}' → '{target_str}' (시스템이 자동으로 절대 경로로 변환)"
        
        return target_str, None, correction_msg
        
    except PermissionError as e:
        # 보안 위반
        return original_path, str(e), None
    except Exception as e:
        # 경로 계산 자체가 불가능한 쓰레기 패가 들어왔을 때
        return original_path, f"Invalid path betting: {str(e)}", None


def _validate_path_in_workspace(workspace_root: Path, path_str: str) -> Tuple[bool, Optional[str]]:
    """
    [ABSOLUTE_PATH_ANCHORING] Strict Path Resolution with Integrity Validation.
    경로를 workspace_root 기준으로 결합하고, 문자열 포함 + 실제 하위 경로 검증을 수행.
    Returns (True, None) if allowed, (False, error_message) if blocked.
    
    Note: 이 함수는 정규화된 경로를 검증하는 데 사용됩니다.
    """
    if not path_str or not isinstance(path_str, str):
        return True, None  # 빈 값은 도구 자체 검증에 맡김
    
    path_str = path_str.strip()
    
    # [PATH_SANITIZATION] 상위 디렉토리 접근 시도 차단
    if ".." in path_str:
        return False, f"상위 디렉터리 탈출(..)은 허용되지 않습니다: '{path_str}'"
    
    try:
        p = Path(path_str)
        combined = p.resolve() if p.is_absolute() else (workspace_root / path_str).resolve()
        combined_str = str(combined)
        
        # [PATH_INTEGRITY_VALIDATION] 실제 하위 경로 검증
        if not combined.is_relative_to(workspace_root):
            return False, f"경로가 workspace 밖으로 나갑니다: '{path_str}'"
        
        # [PATH_INTEGRITY_VALIDATION] 경로 변형 방지 (melody_link 등 오타 차단)
        if "melody" in combined_str.lower() or "mellow" not in combined_str.lower():
            return False, f"경로 변형 감지: '{path_str}' (mellow가 melody 등으로 변형됨)"
            
    except Exception as e:
        return False, f"경로 검증 실패: '{path_str}' ({e})"
    return True, None


def _normalize_and_validate_path_args(
    workspace_root: Path, tool_name: str, args: Dict[str, Any]
) -> Tuple[Dict[str, Any], Optional[Tuple[str, str]], Optional[str]]:
    """
    [PATH_NORMALIZATION] 도구 인자에서 경로를 꺼내 정규화하고 검증.
    
    Args:
        workspace_root: 워크스페이스 루트 경로
        tool_name: 도구 이름
        args: 도구 인자 딕셔너리 (수정 가능)
        
    Returns:
        (정규화된_args, 에러_정보, 수정_메시지)
        - 성공 시: (정규화된_args, None, 수정_메시지 또는 None)
        - 실패 시: (원본_args, (경로, 에러_메시지), None)
    """
    path_keys = _PATH_ARG_BY_TOOL.get(tool_name)
    if not path_keys:
        return args, None, None
    
    normalized_args = args.copy()
    correction_messages = []
    
    for key in path_keys:
        val = normalized_args.get(key)
        if val is None:
            continue
        
        if key == "file_paths" and isinstance(val, list):
            # 리스트 형태의 경로들 처리
            normalized_list = []
            for i, item in enumerate(val):
                if isinstance(item, str):
                    normalized_path, err, correction_msg = _normalize_path(item, workspace_root)
                    if err:
                        return args, (item.strip(), f"file_paths[{i}]: {err}"), None
                    normalized_list.append(normalized_path)
                    if correction_msg:
                        correction_messages.append(correction_msg)
                else:
                    normalized_list.append(item)
            normalized_args[key] = normalized_list
        else:
            if isinstance(val, str):
                normalized_path, err, correction_msg = _normalize_path(val, workspace_root)
                if err:
                    return args, (val.strip(), err), None
                normalized_args[key] = normalized_path
                if correction_msg:
                    correction_messages.append(correction_msg)
    
    # 모든 수정 메시지를 하나로 합침
    combined_correction = "\n".join(correction_messages) if correction_messages else None
    
    return normalized_args, None, combined_correction


def _validate_path_args_for_tool(
    workspace_root: Path, tool_name: str, args: Dict[str, Any]
) -> Optional[Tuple[str, str]]:
    """
    [DEPRECATED] 이 함수는 _normalize_and_validate_path_args로 대체되었습니다.
    하위 호환성을 위해 유지되지만, 새로운 코드는 _normalize_and_validate_path_args를 사용해야 합니다.
    
    도구 인자에서 경로를 꺼내 검증. 위반 시 (입력된_경로, 상세메시지) 반환, 통과 시 None.
    """
    _, error_info, _ = _normalize_and_validate_path_args(workspace_root, tool_name, args)
    return error_info
