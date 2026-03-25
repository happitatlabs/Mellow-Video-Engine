"""
Long-form Output Policy 테스트

장문 요청 감지 및 확장 모드 감지 기능을 테스트합니다.
"""
import os
import sys
from pathlib import Path

# 프로젝트 루트를 경로에 추가
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from mellow_link.core.agent_prompts import (
    _is_long_form_request,
    _is_expansion_request,
    OUTPUT_POLICY_BLOCK,
)


def test_long_form_detection_keywords_ko():
    """한글 키워드 기반 장문 요청 감지 테스트"""
    test_cases = [
        ("이 프로젝트를 분석해줘", True),
        ("두 가지 방법을 비교해봐", True),
        ("시장 전망을 알려줘", True),
        ("전략을 수립해줘", True),
        ("파일을 읽어줘", False),  # 일반 요청
        ("안녕하세요", False),  # 짧은 인사
    ]
    
    for query, expected in test_cases:
        result = _is_long_form_request(query)
        assert result == expected, f"'{query}': expected {expected}, got {result}"
        print(f"✅ '{query}' -> {result}")


def test_long_form_detection_keywords_en():
    """영어 키워드 기반 장문 요청 감지 테스트"""
    test_cases = [
        ("Analyze this project", True),
        ("Compare two methods", True),
        ("Explain the strategy", True),
        ("Investigate the issue", True),
        ("Read the file", False),  # 일반 요청
        ("Hello", False),  # 짧은 인사
    ]
    
    for query, expected in test_cases:
        result = _is_long_form_request(query)
        assert result == expected, f"'{query}': expected {expected}, got {result}"
        print(f"✅ '{query}' -> {result}")


def test_long_form_detection_length():
    """길이 기반 장문 요청 감지 테스트"""
    # 기본 임계값 30자
    short_query = "짧은 질문"  # 5자
    long_query = "이것은 매우 긴 질문입니다. " * 2  # 30자 이상
    
    assert not _is_long_form_request(short_query), "짧은 질문은 감지되지 않아야 함"
    assert _is_long_form_request(long_query), "긴 질문은 감지되어야 함"
    print(f"✅ 길이 기반 감지: 짧은 질문={not _is_long_form_request(short_query)}, 긴 질문={_is_long_form_request(long_query)}")


def test_expansion_detection_ko():
    """한글 확장 요청 감지 테스트"""
    test_cases = [
        ("확장", True),  # exact match
        ("확장해줘", True),
        ("더 자세히", True),  # exact match
        ("더 자세히 설명해줘", True),
        ("자세히", True),  # exact match
        ("자세히 설명해줘", True),
        ("상세히 알려줘", True),
        ("계속", True),  # exact match
        ("전체", True),  # exact match
        ("풀버전", True),  # exact match
        ("더 보여줘", True),
        ("전체 답변을 원해", True),
        ("안녕", False),  # 일반 인사
        ("파일을 읽어줘", False),  # 일반 요청
    ]
    
    for query, expected in test_cases:
        result = _is_expansion_request(query)
        assert result == expected, f"'{query}': expected {expected}, got {result}"
        print(f"✅ '{query}' -> {result}")


def test_expansion_detection_en():
    """영어 확장 요청 감지 테스트"""
    test_cases = [
        ("expand", True),  # exact match
        ("Expand please", True),
        ("more detail", True),  # exact match
        ("more details", True),
        ("continue", True),  # exact match
        ("full", True),  # exact match
        ("full answer", True),
        ("show more", True),  # exact match
        ("tell me more", True),  # exact match
        ("complete explanation", True),
        ("Read the file", False),  # 일반 요청
        ("Hello", False),  # 일반 인사
    ]
    
    for query, expected in test_cases:
        result = _is_expansion_request(query)
        assert result == expected, f"'{query}': expected {expected}, got {result}"
        print(f"✅ '{query}' -> {result}")


def test_output_policy_block():
    """OUTPUT_POLICY 블록 내용 확인"""
    assert "[OUTPUT_POLICY]" in OUTPUT_POLICY_BLOCK
    assert "요약" in OUTPUT_POLICY_BLOCK or "summary" in OUTPUT_POLICY_BLOCK.lower()
    assert "800자" in OUTPUT_POLICY_BLOCK or "800" in OUTPUT_POLICY_BLOCK
    assert "확장" in OUTPUT_POLICY_BLOCK
    print("✅ OUTPUT_POLICY 블록 내용 확인 완료")


def test_threshold_from_env():
    """환경변수에서 임계값 읽기 테스트"""
    # 원래 값 저장
    original_threshold = os.getenv("MELLOW_LONG_FORM_THRESHOLD")
    
    try:
        # 임계값 50으로 설정
        os.environ["MELLOW_LONG_FORM_THRESHOLD"] = "50"
        
        # 30자 질문은 감지되지 않아야 함
        query_30 = "a" * 30
        assert not _is_long_form_request(query_30), "30자 질문은 임계값 50에서 감지되지 않아야 함"
        
        # 50자 질문은 감지되어야 함
        query_50 = "a" * 50
        assert _is_long_form_request(query_50), "50자 질문은 임계값 50에서 감지되어야 함"
        
        print("✅ 환경변수 임계값 테스트 통과")
    finally:
        # 원래 값 복원
        if original_threshold:
            os.environ["MELLOW_LONG_FORM_THRESHOLD"] = original_threshold
        elif "MELLOW_LONG_FORM_THRESHOLD" in os.environ:
            del os.environ["MELLOW_LONG_FORM_THRESHOLD"]


if __name__ == "__main__":
    print("=" * 60)
    print("Long-form Output Policy 테스트 시작")
    print("=" * 60)
    
    try:
        test_long_form_detection_keywords_ko()
        test_long_form_detection_keywords_en()
        test_long_form_detection_length()
        test_expansion_detection_ko()
        test_expansion_detection_en()
        test_output_policy_block()
        test_threshold_from_env()
        
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
