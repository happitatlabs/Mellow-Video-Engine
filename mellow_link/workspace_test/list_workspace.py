"""
워크스페이스 파일 목록 조회 스크립트 (통합 버전)

기존의 file_reader.py, list_files.py, list_workspace_files.py, explore_workspace.py를 통합한 버전입니다.
fs_util.py의 list_tree() 메서드를 활용하거나, 직접 구현한 트리 구조 출력을 제공합니다.

사용법:
    python workspace/list_workspace.py              # 트리 구조 출력 (기본)
    python workspace/list_workspace.py --flat      # 플랫 목록 출력
    python workspace/list_workspace.py --relative  # 상대 경로 출력
"""

import sys
from pathlib import Path

# 워크스페이스 루트 경로 (절대 경로 사용)
WORKSPACE_ROOT = Path(__file__).resolve().parent


def print_tree(current_path: Path, prefix: str = '', max_depth: int = 10, current_depth: int = 0) -> None:
    """트리 구조로 디렉토리 출력"""
    if current_depth > max_depth:
        return
    
    try:
        entries = sorted(current_path.iterdir(), key=lambda x: (x.is_file(), x.name.lower()))
    except PermissionError:
        return
    
    for i, entry in enumerate(entries):
        is_last = i == len(entries) - 1
        connector = '└── ' if is_last else '├── '
        print(prefix + connector + entry.name)
        
        if entry.is_dir():
            extension = '    ' if is_last else '│   '
            print_tree(entry, prefix + extension, max_depth, current_depth + 1)


def print_flat_list(base_path: Path, relative: bool = False) -> None:
    """플랫 목록으로 모든 파일 출력"""
    try:
        for path in sorted(base_path.rglob('*')):
            if relative:
                try:
                    print(path.relative_to(base_path))
                except ValueError:
                    print(path.name)
            else:
                print(path)
    except PermissionError as e:
        print(f"[Error] 권한 오류: {e}")


def main() -> int:
    """메인 함수"""
    # 명령줄 인자 파싱
    mode = 'tree'  # 기본값: 트리 구조
    if '--flat' in sys.argv:
        mode = 'flat'
    elif '--relative' in sys.argv:
        mode = 'relative'
    
    # 워크스페이스 존재 확인
    if not WORKSPACE_ROOT.exists():
        print(f"[Error] 워크스페이스 경로가 존재하지 않습니다: {WORKSPACE_ROOT}")
        return 1
    
    if not WORKSPACE_ROOT.is_dir():
        print(f"[Error] 워크스페이스 경로가 디렉토리가 아닙니다: {WORKSPACE_ROOT}")
        return 1
    
    # 모드에 따라 출력
    if mode == 'tree':
        print(f"워크스페이스 트리 구조: {WORKSPACE_ROOT}")
        print()
        print_tree(WORKSPACE_ROOT)
    elif mode == 'flat':
        print(f"워크스페이스 파일 목록 (절대 경로): {WORKSPACE_ROOT}")
        print()
        print_flat_list(WORKSPACE_ROOT, relative=False)
    elif mode == 'relative':
        print(f"워크스페이스 파일 목록 (상대 경로): {WORKSPACE_ROOT}")
        print()
        print_flat_list(WORKSPACE_ROOT, relative=True)
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
