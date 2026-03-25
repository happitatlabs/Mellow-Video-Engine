"""
Orchestrator 페르소나: prompts 폴더에서 페르소나 텍스트 로드 (캐싱 지원).
"""
import os
from pathlib import Path
from typing import Dict, Optional

# 페르소나 파일 캐시 (파일명 -> 내용)
_persona_cache: Dict[str, str] = {}


def load_persona_from_file(filename: str, use_cache: bool = True) -> str:
    """prompts 폴더에서 페르소나 텍스트를 읽어옵니다 (캐싱 지원).
    
    Args:
        filename: 페르소나 파일명
        use_cache: 캐시 사용 여부 (기본값: True)
    
    Returns:
        페르소나 텍스트 내용
    """
    # 캐시 확인
    if use_cache and filename in _persona_cache:
        return _persona_cache[filename]
    
    # 경로 설정 (프로젝트 루트 기준 prompts 폴더)
    base_path = (
        os.environ.get("MELLOW_LINK_PROJECT_ROOT")
        or os.environ.get("PROJECT_ROOT")
        or os.getcwd()
    )

    # 요구사항 예시(<project_root>/prompts/<filename>) + 현재 레포 구조(<project_root>/mellow_link/prompts/<filename>)를 모두 지원
    mellow_link_root = Path(__file__).resolve().parent.parent
    candidates = [
        os.path.join(base_path, "prompts", filename),
        os.path.join(base_path, "mellow_link", "prompts", filename),
        str(mellow_link_root / "prompts" / filename),
    ]

    for file_path in candidates:
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read().strip()
                # 캐시에 저장
                if use_cache:
                    _persona_cache[filename] = content
                return content
        except FileNotFoundError:
            continue
        except Exception as e:
            error_msg = f"Error: Failed to load persona file '{filename}': {e}"
            if use_cache:
                _persona_cache[filename] = error_msg
            return error_msg

    # 파일이 없으면 안전하게 기본 메시지 반환
    error_msg = f"Error: Persona file '{filename}' not found."
    if use_cache:
        _persona_cache[filename] = error_msg
    return error_msg


def clear_persona_cache() -> None:
    """페르소나 캐시를 초기화합니다 (테스트/리로드용)."""
    global _persona_cache
    _persona_cache.clear()
