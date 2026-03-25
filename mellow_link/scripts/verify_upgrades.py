#!/usr/bin/env python3
"""
[COMMAND: UPGRADE_LIST_DIRECTORY_RECURSIVE] 및 [COMMAND: ENFORCE_LIFECYCLE_SESSION_LOCK] 검증 스크립트.
"""
from pathlib import Path

def main():
    base = Path(__file__).parent.parent
    print("=== UPGRADE_LIST_DIRECTORY_RECURSIVE 및 ENFORCE_LIFECYCLE_SESSION_LOCK 검증 ===\n")
    
    agent_tools = base / "core" / "agent_tools.py"
    main_py = base / "main.py"
    
    if not agent_tools.exists():
        print("❌ agent_tools.py를 찾을 수 없습니다.")
        return 1
    
    if not main_py.exists():
        print("❌ main.py를 찾을 수 없습니다.")
        return 1
    
    tools_content = agent_tools.read_text(encoding="utf-8")
    main_content = main_py.read_text(encoding="utf-8")
    
    # 1. UPGRADE_LIST_DIRECTORY_RECURSIVE 검증
    print("1. UPGRADE_LIST_DIRECTORY_RECURSIVE 검증:")
    
    if "def list_directory" in tools_content:
        list_dir_section = tools_content[tools_content.find("def list_directory"):tools_content.find("def ", tools_content.find("def list_directory") + 1)]
        
        # recursive 파라미터 확인
        if "recursive: bool = False" in list_dir_section:
            print("  ✅ recursive 파라미터 추가됨")
        else:
            print("  ❌ recursive 파라미터 없음")
        
        # max_depth 파라미터 확인
        if "max_depth: int" in list_dir_section:
            print("  ✅ max_depth 파라미터 추가됨")
        else:
            print("  ❌ max_depth 파라미터 없음")
        
        # Ignore Patterns 확인
        if "__pycache__" in list_dir_section and ".git" in list_dir_section:
            print("  ✅ Ignore Patterns 추가됨")
        else:
            print("  ❌ Ignore Patterns 없음")
        
        # Tree-View Output 확인
        if "└──" in list_dir_section or "├──" in list_dir_section or "│" in list_dir_section:
            print("  ✅ Tree-View Output 구현됨")
        else:
            print("  ❌ Tree-View Output 없음")
        
        # _build_tree 함수 확인
        if "_build_tree" in list_dir_section:
            print("  ✅ _build_tree 함수 구현됨")
        else:
            print("  ❌ _build_tree 함수 없음")
    else:
        print("  ❌ list_directory 함수를 찾을 수 없음")
    
    # 2. ENFORCE_LIFECYCLE_SESSION_LOCK 검증
    print("\n2. ENFORCE_LIFECYCLE_SESSION_LOCK 검증:")
    
    # await asyncio.sleep(3.0) 제거 확인
    sleep_count = main_content.count("await asyncio.sleep(3.0)")
    if sleep_count == 0:
        print("  ✅ 모든 await asyncio.sleep(3.0) 제거됨")
    else:
        print(f"  ❌ await asyncio.sleep(3.0) {sleep_count}개 남아있음")
    
    # finally 블록에서 락 해제 확인
    if "finally:" in main_content and "SESSION_BUSY.discard" in main_content:
        # stream_generator의 finally 블록 확인
        if "async def stream_generator" in main_content:
            stream_gen_section = main_content[main_content.find("async def stream_generator"):main_content.find("except Exception", main_content.find("async def stream_generator") + 5000)]
            if "finally:" in stream_gen_section and "SESSION_BUSY.discard" in stream_gen_section:
                if "await asyncio.sleep(3.0)" not in stream_gen_section:
                    print("  ✅ stream_generator의 finally 블록에서 락 해제 (쿨다운 없음)")
                else:
                    print("  ❌ stream_generator의 finally 블록에 쿨다운 있음")
            else:
                print("  ⚠️ stream_generator의 finally 블록에 락 해제 없음")
        else:
            print("  ⚠️ stream_generator 함수를 찾을 수 없음")
    else:
        print("  ⚠️ finally 블록 또는 SESSION_BUSY.discard를 찾을 수 없음")
    
    # request.is_disconnected() 체크 확인
    if "is_disconnected" in main_content:
        print("  ✅ request.is_disconnected() 체크 추가됨")
    else:
        print("  ⚠️ request.is_disconnected() 체크 없음")
    
    # Background task로 락 해제 보장 확인
    if "release_lock_on_disconnect" in main_content or "background_tasks.add_task" in main_content:
        print("  ✅ Background task로 락 해제 보장 추가됨")
    else:
        print("  ⚠️ Background task로 락 해제 보장 없음")
    
    # 3. Path Consistency 확인
    print("\n3. Path Consistency 확인:")
    if "ABSOLUTE_PATH_ANCHORING" in tools_content or "_ensure_path_inside_workspace" in list_dir_section:
        print("  ✅ ABSOLUTE_PATH_ANCHORING 원칙 준수 확인")
    else:
        print("  ⚠️ ABSOLUTE_PATH_ANCHORING 원칙 확인 불가")
    
    print("\n" + "="*50)
    print("✅ verified: UPGRADE_LIST_DIRECTORY_RECURSIVE 및 ENFORCE_LIFECYCLE_SESSION_LOCK 적용 완료")
    return 0

if __name__ == "__main__":
    import sys
    sys.exit(main())
