"""
Progressive Output Policy 테스트

3단계 확장 레벨 및 thinking-lite 모드 감지 기능을 테스트합니다.
"""
import os
import sys
from pathlib import Path

# 프로젝트 루트를 경로에 추가
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from mellow_link.core.agent_prompts import (
    _get_expansion_level,
    _is_expansion_request,
    _is_long_form_request,
    _get_output_policy_block,
)


def test_expansion_level_detection():
    """확장 레벨 감지 테스트"""
    test_cases = [
        ("확장", 1),
        ("확장2", 2),
        ("확장3", 3),
        ("expand", 1),
        ("expand2", 2),
        ("expand3", 3),
        ("더 자세히 설명해줘", 1),
        ("확장해줘", 1),
        ("안녕", 0),
        ("파일을 읽어줘", 0),
    ]
    
    for query, expected_level in test_cases:
        result = _get_expansion_level(query)
        assert result == expected_level, f"'{query}': expected level {expected_level}, got {result}"
        print(f"✅ '{query}' -> level {result}")


def test_expansion_request_detection():
    """확장 요청 감지 테스트 (기존 호환성)"""
    test_cases = [
        ("확장", True),
        ("확장2", True),
        ("확장3", True),
        ("더 자세히", True),
        ("안녕", False),
    ]
    
    for query, expected in test_cases:
        result = _is_expansion_request(query)
        assert result == expected, f"'{query}': expected {expected}, got {result}"
        print(f"✅ '{query}' -> {result}")


def test_output_policy_block_selection():
    """OUTPUT_POLICY 블록 선택 테스트"""
    # Summary-first (level 0)
    block_0 = _get_output_policy_block(expansion_level=0, is_thinking_lite=False)
    assert "[요약 개요]" in block_0
    assert "800자 이내" in block_0
    print("✅ Level 0 (summary-first) block selected")
    
    # Expand v1 (level 1)
    block_1 = _get_output_policy_block(expansion_level=1, is_thinking_lite=False)
    assert "확장 v1" in block_1 or "상세한 응답" in block_1
    assert "1800자 이내" in block_1 or "1800" in block_1
    print("✅ Level 1 (expand v1) block selected")
    
    # Expand v2 (level 2)
    block_2 = _get_output_policy_block(expansion_level=2, is_thinking_lite=False)
    assert "확장 v2" in block_2 or "사례" in block_2 or "비유" in block_2
    assert "2500자 이내" in block_2 or "2500" in block_2
    print("✅ Level 2 (expand v2) block selected")
    
    # Expand v3 (level 3)
    block_3 = _get_output_policy_block(expansion_level=3, is_thinking_lite=False)
    assert "확장 v3" in block_3 or "기술" in block_3 or "논문" in block_3
    assert "3500자 이내" in block_3 or "3500" in block_3
    print("✅ Level 3 (expand v3) block selected")
    
    # Thinking-lite
    block_lite = _get_output_policy_block(expansion_level=0, is_thinking_lite=True)
    assert "Thinking-Lite" in block_lite or "thinking-lite" in block_lite.lower()
    assert "12줄 이내" in block_lite or "900자 이내" in block_lite
    assert "최대 1개 도구 호출" in block_lite or "1개 도구" in block_lite
    print("✅ Thinking-lite block selected")


def test_long_form_detection():
    """장문 요청 감지 테스트"""
    test_cases = [
        ("이 프로젝트를 분석해줘", True),
        ("비교해봐", True),
        ("안녕하세요", False),
        ("a" * 30, True),  # 길이 기반
        ("a" * 20, False),  # 길이 미만
    ]
    
    for query, expected in test_cases:
        result = _is_long_form_request(query)
        assert result == expected, f"'{query[:30]}...': expected {expected}, got {result}"
        print(f"✅ '{query[:30]}...' -> {result}")


def test_progressive_disclosure_flow():
    """Progressive Disclosure 흐름 테스트"""
    # 시나리오 1: 장문 질문 -> summary-first
    long_query = "이 프로젝트의 아키텍처를 분석해줘"
    assert _is_long_form_request(long_query), "장문 요청 감지 실패"
    assert _get_expansion_level(long_query) == 0, "확장 레벨이 0이어야 함"
    print("✅ 시나리오 1: 장문 질문 -> summary-first")
    
    # 시나리오 2: 확장 요청 -> expand v1
    expand_query = "확장"
    assert _get_expansion_level(expand_query) == 1, "확장 레벨이 1이어야 함"
    print("✅ 시나리오 2: 확장 요청 -> expand v1")
    
    # 시나리오 3: 확장2 요청 -> expand v2
    expand2_query = "확장2"
    assert _get_expansion_level(expand2_query) == 2, "확장 레벨이 2이어야 함"
    print("✅ 시나리오 3: 확장2 요청 -> expand v2")
    
    # 시나리오 4: 확장3 요청 -> expand v3
    expand3_query = "확장3"
    assert _get_expansion_level(expand3_query) == 3, "확장 레벨이 3이어야 함"
    print("✅ 시나리오 4: 확장3 요청 -> expand v3")


if __name__ == "__main__":
    print("=" * 60)
    print("Progressive Output Policy 테스트 시작")
    print("=" * 60)
    
    try:
        test_expansion_level_detection()
        test_expansion_request_detection()
        test_output_policy_block_selection()
        test_long_form_detection()
        test_progressive_disclosure_flow()
        
        print("=" * 60)
        print("✅ 모든 테스트 통과!")
        print("=" * 60)
    except AssertionError as e:
        print(f"❌ 테스트 실패: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"❌ 예상치 못한 오류: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
