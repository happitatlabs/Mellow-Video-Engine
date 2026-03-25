"""
자율 에이전트 스크립트 템플릿

이 파일은 자율 에이전트가 생성하는 스크립트의 템플릿입니다.
새로운 스크립트를 작성할 때 이 템플릿을 참고하세요.

사용법:
    # Description: [스크립트 설명]
    
    from pathlib import Path
    
    # 워크스페이스 루트 경로 (cwd 기준 - .temp/에서 실행되므로 __file__ 사용 금지)
    WORKSPACE_ROOT = Path.cwd()
    
    # 스크립트 로직 작성
    # ...

주의사항:
- 모든 파일 작업은 WORKSPACE_ROOT 내부로 제한됩니다
- 경로 참조 시 반드시 Path.cwd() 사용 (Path(__file__).parent 사용 금지)
- 보안상 core/, config/ 등은 접근 불가합니다
"""

from pathlib import Path

# 워크스페이스 루트 경로 (cwd 기준)
WORKSPACE_ROOT = Path.cwd()

# 스크립트 로직을 여기에 작성하세요
if __name__ == "__main__":
    print(f"워크스페이스 루트: {WORKSPACE_ROOT}")
    # 여기에 실제 로직 추가
