"""
list_workspace.py 테스트

통합된 워크스페이스 목록 조회 스크립트의 테스트입니다.
"""

import subprocess
import sys
from pathlib import Path

# 워크스페이스 루트
WORKSPACE_ROOT = Path(__file__).resolve().parent
SCRIPT_PATH = WORKSPACE_ROOT / "list_workspace.py"


def test_tree_mode():
    """트리 모드 테스트"""
    print("=== 트리 모드 테스트 ===")
    result = subprocess.run(
        [sys.executable, str(SCRIPT_PATH)],
        capture_output=True,
        text=True,
        cwd=str(WORKSPACE_ROOT)
    )
    print(result.stdout)
    return result.returncode == 0


def test_flat_mode():
    """플랫 모드 테스트"""
    print("\n=== 플랫 모드 테스트 ===")
    result = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "--flat"],
        capture_output=True,
        text=True,
        cwd=str(WORKSPACE_ROOT)
    )
    print(result.stdout[:500])  # 처음 500자만 출력
    return result.returncode == 0


def test_relative_mode():
    """상대 경로 모드 테스트"""
    print("\n=== 상대 경로 모드 테스트 ===")
    result = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "--relative"],
        capture_output=True,
        text=True,
        cwd=str(WORKSPACE_ROOT)
    )
    print(result.stdout[:500])  # 처음 500자만 출력
    return result.returncode == 0


def main():
    """모든 테스트 실행"""
    print(f"테스트 대상: {SCRIPT_PATH}")
    print(f"워크스페이스 루트: {WORKSPACE_ROOT}\n")
    
    tests = [
        ("트리 모드", test_tree_mode),
        ("플랫 모드", test_flat_mode),
        ("상대 경로 모드", test_relative_mode),
    ]
    
    results = []
    for name, test_func in tests:
        try:
            success = test_func()
            results.append((name, success))
        except Exception as e:
            print(f"[Error] {name} 테스트 실패: {e}")
            results.append((name, False))
    
    print("\n=== 테스트 결과 ===")
    for name, success in results:
        status = "✅ 통과" if success else "❌ 실패"
        print(f"{name}: {status}")
    
    all_passed = all(success for _, success in results)
    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
