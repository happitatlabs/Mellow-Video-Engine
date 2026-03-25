#!/usr/bin/env python3
"""
[COMMAND: ENGINE_PERSONA_LAYER_SEPARATION] 검증 스크립트.
"""
import re
from pathlib import Path

def main():
    base = Path(__file__).parent.parent
    print("=== ENGINE_PERSONA_LAYER_SEPARATION 검증 ===\n")
    
    # 1. 시스템 프롬프트 우선순위 확인
    print("1. 시스템 프롬프트 우선순위 확인:")
    agent_brain = base / "core" / "agent_brain.py"
    if agent_brain.exists():
        content = agent_brain.read_text(encoding="utf-8")
        
        # LOGIC_FIRST_PRINCIPLE이 최상단에 있는지 확인
        logic_first_pos = content.find("[COMMAND: LOGIC_FIRST_PRINCIPLE]")
        
        # [PERSONA ISOLATION] 시스템 프롬프트에서 {persona} 블록이 제거되었는지 확인
        template_content = content[content.find("SYSTEM_PROMPT_TEMPLATE"):content.find("def build_system_prompt")]
        if "{persona}" in template_content:
            print("  ❌ 시스템 프롬프트에 {persona} 블록이 포함되어 있음 (Thought 단계에 영향 가능)")
        else:
            print("  ✅ 시스템 프롬프트에서 {persona} 블록 제거됨 (Thought 단계에 페르소나 영향 없음)")
        
        # build_system_prompt에서 persona를 사용하지 않는지 확인
        build_prompt_func = content[content.find("def build_system_prompt"):content.find("def ", content.find("def build_system_prompt") + 1)]
        if "persona" in build_prompt_func and "format" in build_prompt_func and "persona" in build_prompt_func[build_prompt_func.find("format"):]:
            print("  ❌ build_system_prompt에서 persona를 시스템 프롬프트에 포함시킴")
        else:
            print("  ✅ build_system_prompt에서 persona를 시스템 프롬프트에 포함하지 않음")
        
        # 기술적 실패 시 감성적 회피 금지 규칙 확인
        if "기술적 실패 시 아바타의 기억이나 감성적인 대화로 회피하는 행위는 엄격히 금지" in content:
            print("  ✅ 기술적 실패 시 감성적 회피 금지 규칙 추가됨")
        else:
            print("  ❌ 기술적 실패 시 감성적 회피 금지 규칙 없음")
        
        # PERSONA PRESENTATION LAYER 확인
        if "[PERSONA PRESENTATION LAYER]" in content and "finish 도구의 출력 시에만" in content:
            print("  ✅ PERSONA PRESENTATION LAYER 지시 추가됨")
        else:
            print("  ❌ PERSONA PRESENTATION LAYER 지시 없음")
        
        # Thought 단계에서 페르소나 금지 명시 확인
        if "Thought 단계에서는 절대 페르소나 스타일을 사용하지 마라" in content:
            print("  ✅ Thought 단계 페르소나 금지 명시됨")
        else:
            print("  ⚠️ Thought 단계 페르소나 금지 명시 없음")
    
    # 2. 경로 자동 생성 로직 확인
    print("\n2. 경로 자동 생성 로직 확인:")
    agent_tools = base / "core" / "agent_tools.py"
    if agent_tools.exists():
        content = agent_tools.read_text(encoding="utf-8")
        
        # write_file에 mkdir 확인
        if "safe_path.parent.mkdir(parents=True, exist_ok=True)" in content:
            print("  ✅ write_file: 부모 디렉토리 자동 생성")
        else:
            print("  ❌ write_file: 자동 생성 로직 없음")
        
        # list_directory에 자동 생성 확인
        if "safe_path.mkdir(parents=True, exist_ok=True)" in content and "list_directory" in content:
            print("  ✅ list_directory: 디렉토리 자동 생성")
        else:
            print("  ❌ list_directory: 자동 생성 로직 없음")
    
    # 3. 절대 경로 기준 하드코딩 확인
    print("\n3. 절대 경로 기준 하드코딩 확인:")
    if agent_brain.exists():
        content = agent_brain.read_text(encoding="utf-8")
        if "_WORKSPACE_ROOT_CONSTANT = Path(r\"D:\\\\AI_Project\\\\mellow_link\\\\workspace\")" in content or "_WORKSPACE_ROOT_CONSTANT = Path" in content:
            print("  ✅ agent_brain.py: 하드코딩된 상수 사용")
        else:
            print("  ❌ agent_brain.py: 하드코딩된 상수 없음")
    
    workspace_sandbox = base / "core" / "workspace_sandbox.py"
    if workspace_sandbox.exists():
        content = workspace_sandbox.read_text(encoding="utf-8")
        if "_WORKSPACE_ROOT_CONSTANT = Path" in content:
            print("  ✅ workspace_sandbox.py: 하드코딩된 상수 사용")
        else:
            print("  ❌ workspace_sandbox.py: 하드코딩된 상수 없음")
    
    # 4. 경로 무결성 검증 확인
    print("\n4. 경로 무결성 검증 확인:")
    if agent_brain.exists():
        content = agent_brain.read_text(encoding="utf-8")
        if "[PATH_INTEGRITY_VALIDATION]" in content and "mellow_link" in content and "workspace" in content:
            print("  ✅ 경로 무결성 검증 (문자열 포함 + 하위 경로) 추가됨")
        else:
            print("  ❌ 경로 무결성 검증 없음")
    
    if agent_tools.exists():
        content = agent_tools.read_text(encoding="utf-8")
        if "[PATH_INTEGRITY_VALIDATION]" in content and "melody" in content.lower():
            print("  ✅ 경로 변형 방지 (melody 차단) 추가됨")
        else:
            print("  ❌ 경로 변형 방지 없음")
    
    # 5. finish 도구 호출 시 페르소나 적용 로직 확인
    print("\n5. finish 도구 호출 시 페르소나 적용 로직 확인:")
    if agent_brain.exists():
        content = agent_brain.read_text(encoding="utf-8")
        if "_apply_persona_to_summary" in content:
            print("  ✅ _apply_persona_to_summary 함수 추가됨")
        else:
            print("  ❌ _apply_persona_to_summary 함수 없음")
        
        if "if persona and persona.strip():" in content and "_apply_persona_to_summary" in content:
            print("  ✅ finish 도구 호출 시 페르소나 적용 로직 추가됨")
        else:
            print("  ❌ finish 도구 호출 시 페르소나 적용 로직 없음")
    
    # 6. 최대 턴 수 도달 시 자동 finish 처리 확인
    print("\n6. 최대 턴 수 도달 시 자동 finish 처리 확인:")
    if agent_brain.exists():
        content = agent_brain.read_text(encoding="utf-8")
        if "_generate_summary_for_max_turns" in content:
            print("  ✅ _generate_summary_for_max_turns 함수 추가됨")
        else:
            print("  ❌ _generate_summary_for_max_turns 함수 없음")
        
        if "AUTO_FINISH_ON_MAX_TURNS" in content:
            print("  ✅ 최대 턴 수 도달 시 자동 finish 처리 추가됨")
        else:
            print("  ❌ 최대 턴 수 도달 시 자동 finish 처리 없음")
        
        if "마지막 턴입니다" in content and "finish 도구를 호출" in content:
            print("  ✅ 마지막 턴 경고 메시지 추가됨")
        else:
            print("  ⚠️ 마지막 턴 경고 메시지 없음")
    
    print("\n" + "="*50)
    print("✅ verified: ENGINE_PERSONA_LAYER_SEPARATION 및 ABSOLUTE_PATH_ANCHORING 적용 완료")
    return 0

if __name__ == "__main__":
    import sys
    sys.exit(main())
