#!/usr/bin/env python3
"""
[COMMAND: RESOLVE_LOCK_VULNERABILITIES] 검증 스크립트.
"""
import re
from pathlib import Path

def main():
    base = Path(__file__).parent.parent
    print("=== RESOLVE_LOCK_VULNERABILITIES 검증 ===\n")
    
    main_py = base / "main.py"
    if not main_py.exists():
        print("❌ main.py를 찾을 수 없습니다.")
        return 1
    
    content = main_py.read_text(encoding="utf-8")
    
    # 1. Problem 1: 데드락 방지 확인
    print("1. 데드락 방지 확인 (Problem 1):")
    
    # SESSION_BUSY.add()와 try 블록 사이에 logger.debug가 없는지 확인
    # 패턴: SESSION_BUSY.add(...) 다음에 try:가 바로 오는지 확인
    add_pattern = r'SESSION_BUSY\.add\([^)]+\)'
    try_pattern = r'\s+try\s*:'
    
    # /chat/ask 엔드포인트 확인
    chat_ask_section = content[content.find("@app.post(\"/chat/ask\""):content.find("@app.post(\"/chat\"", content.find("@app.post(\"/chat/ask\"") + 1)]
    
    if "SESSION_BUSY.add" in chat_ask_section:
        # SESSION_BUSY.add() 다음에 try:가 바로 오는지 확인
        add_positions = [m.end() for m in re.finditer(add_pattern, chat_ask_section)]
        for pos in add_positions:
            next_try = chat_ask_section[pos:pos+100].find("try:")
            if next_try > 0:
                between = chat_ask_section[pos:pos+next_try]
                # logger.debug가 try 전에 있으면 문제
                if "logger.debug" in between or "logger.info" in between:
                    print("  ❌ SESSION_BUSY.add()와 try 블록 사이에 로깅이 있음 (데드락 위험)")
                    return 1
        print("  ✅ SESSION_BUSY.add() 직후 try 블록 진입 확인")
    else:
        print("  ⚠️ SESSION_BUSY.add()를 찾을 수 없음")
    
    # 2. Problem 2: /chat 엔드포인트 세션 락 적용 확인
    print("\n2. /chat 엔드포인트 세션 락 적용 확인 (Problem 2):")
    
    chat_section = content[content.find("@app.post(\"/chat\", tags=[\"LLM\"])"):content.find("@app.post(\"/generate-image\"", content.find("@app.post(\"/chat\", tags=[\"LLM\"])") + 1)]
    
    if "_generate_stable_session_key" in chat_section:
        print("  ✅ _generate_stable_session_key 함수 호출 확인")
    else:
        print("  ❌ _generate_stable_session_key 함수 호출 없음")
    
    if "SESSION_BUSY.add" in chat_section:
        print("  ✅ SESSION_BUSY.add() 호출 확인")
    else:
        print("  ❌ SESSION_BUSY.add() 호출 없음")
    
    if "SESSION_BUSY.discard" in chat_section:
        print("  ✅ SESSION_BUSY.discard() 호출 확인")
    else:
        print("  ❌ SESSION_BUSY.discard() 호출 없음")
    
    # 3. 최종 무결성 확인: 모든 예외 경로에서 3초 쿨다운 후 락 해제
    print("\n3. 최종 무결성 확인 (모든 예외 경로):")
    
    # SESSION_BUSY.discard() 호출 전에 await asyncio.sleep(3.0)가 있는지 확인
    discard_pattern = r'SESSION_BUSY\.discard\([^)]+\)'
    sleep_pattern = r'await\s+asyncio\.sleep\(3\.0\)'
    
    # 모든 SESSION_BUSY.discard() 호출 찾기
    discard_matches = list(re.finditer(discard_pattern, content))
    sleep_matches = list(re.finditer(sleep_pattern, content))
    
    print(f"  발견된 SESSION_BUSY.discard() 호출: {len(discard_matches)}개")
    print(f"  발견된 await asyncio.sleep(3.0) 호출: {len(sleep_matches)}개")
    
    # 각 discard 호출 전에 sleep이 있는지 확인
    all_safe = True
    for discard_match in discard_matches:
        discard_pos = discard_match.start()
        # 이전 200자 내에 sleep이 있는지 확인
        before_text = content[max(0, discard_pos-200):discard_pos]
        if "await asyncio.sleep(3.0)" not in before_text:
            print(f"  ❌ SESSION_BUSY.discard() 호출 전에 await asyncio.sleep(3.0) 없음 (위치: {discard_pos})")
            all_safe = False
    
    if all_safe:
        print("  ✅ 모든 SESSION_BUSY.discard() 호출이 await asyncio.sleep(3.0) 후에 실행됨")
    
    # finally 블록 확인
    if "finally:" in content:
        finally_count = content.count("finally:")
        print(f"  발견된 finally 블록: {finally_count}개")
        
        # finally 블록 내에 SESSION_BUSY.discard()가 있는지 확인
        finally_with_discard = 0
        for match in re.finditer(r'finally\s*:.*?SESSION_BUSY\.discard', content, re.DOTALL):
            finally_with_discard += 1
        
        print(f"  finally 블록 내 SESSION_BUSY.discard() 호출: {finally_with_discard}개")
    
    print("\n" + "="*50)
    if all_safe:
        print("✅ verified: RESOLVE_LOCK_VULNERABILITIES 적용 완료")
        return 0
    else:
        print("❌ 검증 실패: 일부 문제가 발견되었습니다.")
        return 1

if __name__ == "__main__":
    import sys
    sys.exit(main())
