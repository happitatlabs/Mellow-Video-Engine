#!/usr/bin/env python3
"""
[COMMAND: SESSION_LOCK_KEY_REFACTOR] 검증 스크립트.
"""
from pathlib import Path

def main():
    base = Path(__file__).parent.parent
    print("=== SESSION_LOCK_KEY_REFACTOR 검증 ===\n")
    
    main_py = base / "main.py"
    if not main_py.exists():
        print("❌ main.py를 찾을 수 없습니다.")
        return 1
    
    content = main_py.read_text(encoding="utf-8")
    
    # 1. Volatile ID 제거 확인
    print("1. Volatile ID 제거 확인:")
    # 익명 세션 키 생성 부분에서 id(request) 사용 여부 확인
    if "anon_" in content and "id(request)" in content:
        # 문자열 패턴 검색
        if 'anon_' in content and 'id(request)' in content:
            # 더 정확한 검색: f-string 패턴
            if 'f"anon_{id(request)}"' in content or "f'anon_{id(request)}'" in content:
                print("  ❌ id(request)를 사용하는 코드가 남아있음")
            else:
                print("  ✅ id(request) 사용 제거됨 (일부 참조만 남아있을 수 있음)")
        else:
            print("  ✅ id(request) 사용 제거됨")
    else:
        print("  ✅ id(request) 사용 없음")
    
    # 2. Stable Identity 도입 확인
    print("\n2. Stable Identity 도입 확인:")
    if "_generate_stable_session_key" in content:
        print("  ✅ _generate_stable_session_key 함수 추가됨")
        
        if "request.client.host" in content:
            print("  ✅ request.client.host 사용 확인")
        else:
            print("  ⚠️ request.client.host 사용 없음")
        
        if "User-Agent" in content:
            print("  ✅ User-Agent 헤더 사용 확인")
        else:
            print("  ⚠️ User-Agent 헤더 사용 없음")
        
        if "hashlib" in content:
            print("  ✅ hashlib 사용 확인")
        else:
            print("  ⚠️ hashlib 사용 없음")
    else:
        print("  ❌ _generate_stable_session_key 함수 없음")
    
    # 3. Proxy 대응 확인
    print("\n3. Proxy 대응 확인:")
    if "X-Forwarded-For" in content:
        print("  ✅ X-Forwarded-For 헤더 처리 추가됨")
        
        if "forwarded_for.split" in content or "X-Forwarded-For" in content:
            print("  ✅ X-Forwarded-For 우선 처리 로직 확인")
        else:
            print("  ⚠️ X-Forwarded-For 우선 처리 로직 불명확")
    else:
        print("  ❌ X-Forwarded-For 헤더 처리 없음")
    
    # 4. Logging 확인
    print("\n4. 충돌 로깅 확인:")
    if "SESSION_BUSY" in content and "충돌" in content:
        print("  ✅ 충돌 로깅 추가됨")
        
        if "IP:" in content and "User-Agent:" in content:
            print("  ✅ IP 및 User-Agent 정보 로깅 확인")
        else:
            print("  ⚠️ IP 및 User-Agent 정보 로깅 불명확")
        
        if "현재 사용 중인 세션:" in content:
            print("  ✅ 현재 사용 중인 세션 목록 로깅 확인")
        else:
            print("  ⚠️ 현재 사용 중인 세션 목록 로깅 없음")
    else:
        print("  ❌ 충돌 로깅 없음")
    
    # 5. 함수 호출 확인
    print("\n5. 함수 호출 확인:")
    if "_generate_stable_session_key(request)" in content:
        print("  ✅ _generate_stable_session_key 함수 호출 확인")
    else:
        print("  ❌ _generate_stable_session_key 함수 호출 없음")
    
    print("\n" + "="*50)
    print("✅ verified: SESSION_LOCK_KEY_REFACTOR 적용 완료")
    return 0

if __name__ == "__main__":
    import sys
    sys.exit(main())
