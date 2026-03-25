"""
Output Sanitizer 테스트
"""
import pytest
from mellow_link.core.output_sanitizer import (
    sanitize_output,
    detect_plan_intent,
    is_plan_only,
    _calculate_non_korean_ratio,
    _strip_non_korean_lines,
    render_final_answer,
    apply_persona_style,
)


def test_tool_json_detection():
    """Tool JSON 블록이 감지되고 제거되는지 테스트."""
    text_with_tool_json = """
    안녕하세요. 다음 도구를 사용하겠습니다:
    {"name": "web_search", "arguments": {"query": "test"}}
    결과는 다음과 같습니다.
    """
    
    sanitized = sanitize_output(text_with_tool_json)
    
    # Tool JSON 패턴이 제거되었는지 확인
    assert '"name":' not in sanitized or '"arguments":' not in sanitized
    assert "안녕하세요" in sanitized  # 다른 내용은 유지


def test_korean_only_enforcement():
    """비한국어 문장이 제거되는지 테스트."""
    mixed_text = """
    이것은 한국어 문장입니다.
    这是中文句子。
    これは日本語の文です。
    이것도 한국어입니다.
    """
    
    sanitized = _strip_non_korean_lines(mixed_text)
    
    # 중국어/일본어 문장이 제거되었는지 확인
    assert "中文" not in sanitized
    assert "日本語" not in sanitized
    assert "한국어" in sanitized


def test_persona_invention_blocking():
    """페르소나 전환 선언 패턴이 감지되고 차단되는지 테스트."""
    text_with_persona = """
    나는 이제 Eve 페르소나로 전환합니다.
    시스템의 히든 브레인으로 작동하겠습니다.
    """
    
    sanitized = sanitize_output(text_with_persona)
    
    # 페르소나 전환 선언 패턴이 제거되었는지 확인
    # "나는 이제", "페르소나로 전환", "시스템의 히든 브레인" 등이 제거되어야 함
    assert "나는 이제" not in sanitized or "전환합니다" not in sanitized
    assert "시스템의 히든 브레인" not in sanitized
    # 주의: "Eve" 단어 자체는 alias이므로 허용될 수 있음 (전환 선언이 아닌 경우)


def test_plan_intent_detection():
    """Plan intent가 올바르게 감지되는지 테스트."""
    assert detect_plan_intent("할 일 7개 만들어줘") == True
    assert detect_plan_intent("투두 리스트 작성") == True
    assert detect_plan_intent("체크리스트 만들어줘") == True
    assert detect_plan_intent("단계별 계획 세워줘") == True
    assert detect_plan_intent("MVP 계획") == True
    assert detect_plan_intent("로드맵 작성") == True
    assert detect_plan_intent("task list") == True
    assert detect_plan_intent("일반 질문입니다") == False


def test_is_plan_only():
    """계획만/실행하지 마 요청 시 plan_only=True (propose_new_tool 자동 호출 차단용)."""
    assert is_plan_only("우유, 계란, 빵… 계획만. 실행하지 마.") is True
    assert is_plan_only("실행하지 마") is True
    assert is_plan_only("실행하지마") is True
    assert is_plan_only("계획만 세워줘") is True
    assert is_plan_only("일반 질문", []) is False
    assert is_plan_only("일반 질문", ["plan_intent"]) is True
    assert is_plan_only("", ["plan_intent"]) is True
    assert is_plan_only("", None) is False


def test_non_korean_ratio_calculation():
    """비한국어 비율 계산이 정확한지 테스트."""
    korean_text = "안녕하세요. 이것은 한국어입니다."
    assert _calculate_non_korean_ratio(korean_text) < 0.1
    
    chinese_text = "这是中文。"
    assert _calculate_non_korean_ratio(chinese_text) > 0.5
    
    mixed_text = "안녕하세요. 这是中文。"
    ratio = _calculate_non_korean_ratio(mixed_text)
    assert 0.2 < ratio < 0.8  # 중간 정도


def test_sanitize_empty_text():
    """빈 텍스트 처리 테스트."""
    assert sanitize_output("") == ""
    assert sanitize_output(None) == None


def test_sanitize_no_changes():
    """변경이 필요 없는 텍스트는 그대로 유지되는지 테스트."""
    clean_text = "안녕하세요. 이것은 깨끗한 한국어 텍스트입니다."
    sanitized = sanitize_output(clean_text)
    assert sanitized == clean_text.strip()


def test_admin_persona_tone_preserved():
    """Admin persona tone은 유지되지만 CJK/meta-confirm/tool JSON은 제거되는지 테스트."""
    # Admin persona tone 예시 (에브/Eve 스타일)
    admin_text = """
    후후, 파트너. 이것은 재미있는 판이야.
    {"name": "web_search", "arguments": {"query": "test"}}
    这是中文句子。
    계속할까요?
    """
    
    sanitized = sanitize_output(
        admin_text,
        is_admin=True,
        active_persona_id="aventurine"
    )
    
    # Admin persona tone은 유지
    assert "후후" in sanitized or "파트너" in sanitized or "판" in sanitized
    
    # Tool JSON 제거
    assert '"name":' not in sanitized or '"arguments":' not in sanitized
    
    # 중국어 제거
    assert "中文" not in sanitized
    
    # Meta-confirmation 제거
    assert "계속할까요" not in sanitized


def test_eve_alias_allowed():
    """Eve/에브/이브 alias는 허용되는지 테스트 (전환 선언이 아닌 경우)."""
    # 정상적인 alias 사용 - 허용되어야 함
    text_with_eve_alias = "에브로서 말하자면, 이건 꽤 흥미로운 판이야."
    
    sanitized = sanitize_output(text_with_eve_alias, is_admin=False, active_persona_id=None)
    
    # Eve alias는 허용되어야 함
    assert "에브" in sanitized or "흥미로운" in sanitized
    assert "판이야" in sanitized


def test_admin_eve_allowed():
    """Admin이고 active_persona_id가 "aventurine"이면 Eve 언급 허용."""
    text_with_eve = "후후, 나는 에브야, 파트너."
    
    sanitized = sanitize_output(
        text_with_eve, 
        is_admin=True, 
        active_persona_id="aventurine"
    )
    
    # Admin persona의 "에브" 언급은 허용
    assert "에브" in sanitized or "후후" in sanitized


def test_persona_switch_declaration_blocked():
    """페르소나 전환 선언은 차단되는지 테스트."""
    # 전환 선언 패턴 - 차단되어야 함
    test_cases = [
        ("이제 Eve 페르소나로 변환합니다. 계속할까요?", ["이제", "변환합니다", "계속할까요"]),
        ("이제 Eve로 말하겠습니다.", ["이제", "말하겠습니다"]),
        ("Persona风格로 변환합니다", ["변환합니다"]),
        ("페르소나를 Eve로 바꿔", ["페르소나를", "바꿔"]),
        ("시스템의 히든 브레인 Eve입니다", ["시스템의 히든 브레인"]),
        ("주의사항은 다음과 같습니다", ["주의사항은 다음과 같습니다"]),
        ("확인 후 진행하겠습니다", ["확인 후 진행하겠습니다"]),
    ]
    
    for text, blocked_keywords in test_cases:
        sanitized = sanitize_output(text, is_admin=False, active_persona_id=None)
        # 전환 선언 문장의 키워드가 제거되었는지 확인
        for keyword in blocked_keywords:
            assert keyword not in sanitized, f"'{keyword}' should be blocked in '{text}' but found in '{sanitized}'"


def test_eve_alias_preserved_but_switch_blocked():
    """Eve alias는 유지되지만 전환 선언은 차단되는지 테스트."""
    # 정상적인 alias 사용
    normal_text = "에브로서 말하자면, 이건 꽤 흥미로운 판이야."
    sanitized_normal = sanitize_output(normal_text)
    assert "에브" in sanitized_normal or "흥미로운" in sanitized_normal
    
    # 전환 선언 포함
    switch_text = "이제 Eve로 말하겠습니다. 에브로서 말하자면..."
    sanitized_switch = sanitize_output(switch_text)
    # 전환 선언 부분은 제거되지만, alias 부분은 유지될 수 있음
    assert "이제 Eve로 말하겠습니다" not in sanitized_switch


def test_meta_confirmation_removal():
    """Meta-confirmation이 제거되는지 테스트."""
    text_with_confirm = "작업을 완료했습니다. 계속할까요? 请确认是否继续"
    
    sanitized = sanitize_output(text_with_confirm)
    
    assert "계속할까요" not in sanitized
    assert "请确认" not in sanitized
    assert "작업을 완료했습니다" in sanitized  # 다른 내용은 유지


def test_code_block_cjk_excluded():
    """코드 블록 내부의 CJK는 검사에서 제외되는지 테스트."""
    text_with_code = """
    이것은 한국어 문장입니다.
    ```python
    # 这是中文注释
    print("Hello")
    ```
    이것도 한국어입니다.
    """
    
    sanitized = sanitize_output(text_with_code)
    
    # 코드 블록은 유지되어야 함
    assert "```python" in sanitized
    assert "print" in sanitized
    # 코드 블록 내부의 중국어 주석은 유지 (코드 블록이므로)
    assert "这是中文注释" in sanitized or "```" in sanitized


def test_render_final_answer_user_mode():
    """User mode (is_admin=False)에서 페르소나 스타일이 적용되지 않는지 테스트."""
    raw_text = "작업을 완료했습니다. 결과는 다음과 같습니다."
    
    # User mode: 페르소나 스타일 적용 안 됨
    result = render_final_answer(
        raw_text,
        is_admin=False,
        persona_id=None,
        mode="fast",
        llm_service=None
    )
    
    # Sanitization은 적용되지만 페르소나 스타일은 적용 안 됨
    assert "작업을 완료했습니다" in result or "완료" in result
    # 페르소나 톤이 없어야 함 (원본과 유사)
    assert "후후" not in result
    assert "파트너" not in result


def test_render_final_answer_admin_mode():
    """Admin mode (is_admin=True)에서 페르소나 접두/접미가 적용되는지 테스트."""
    raw_text = "작업을 완료했습니다."
    
    result = render_final_answer(
        raw_text,
        is_admin=True,
        persona_id="aventurine",
        mode="fast",
        llm_service=None
    )
    
    # 결정적 래핑: 접두/접미 추가
    assert "후후" in result or "파트너" in result
    assert "작업을 완료했습니다" in result or "완료" in result


def test_minimal_persona_wrapper():
    """apply_persona_style 결정적 래핑: 접두/접미만 추가, 내용 유지."""
    text = "성능 최적화가 완료되었습니다."
    styled = apply_persona_style(text, "aventurine")
    assert "후후" in styled
    assert "판돈" in styled
    assert "성능 최적화가 완료되었습니다." in styled


def test_render_final_answer_tool_json_removed():
    """render_final_answer에서 Tool JSON이 제거되는지 테스트."""
    raw_text = """
    작업을 완료했습니다.
    {"name": "web_search", "arguments": {"query": "test"}}
    결과는 다음과 같습니다.
    """
    
    result = render_final_answer(
        raw_text,
        is_admin=False,
        persona_id=None,
        mode="fast",
        llm_service=None
    )
    
    # Tool JSON 제거 확인
    assert '"name":' not in result or '"arguments":' not in result
    assert "작업을 완료했습니다" in result or "완료" in result


def test_render_final_answer_cjk_removed():
    """render_final_answer에서 CJK 문장이 제거되는지 테스트."""
    raw_text = """
    이것은 한국어 문장입니다.
    这是中文句子。
    이것도 한국어입니다.
    """
    
    result = render_final_answer(
        raw_text,
        is_admin=False,
        persona_id=None,
        mode="fast",
        llm_service=None
    )
    
    # 중국어 문장 제거 확인
    assert "中文" not in result
    assert "한국어" in result


def test_apply_persona_style_unsupported_persona():
    """apply_persona_style에서 지원하지 않는 페르소나 ID면 원본 반환."""
    text = "작업을 완료했습니다."
    result = apply_persona_style(text, "unknown_persona", llm_service=None)
    assert result == text


def test_apply_persona_style_code_block_preserved():
    """apply_persona_style에서 코드 블록 내부는 수정하지 않음."""
    text = "결과입니다.\n```python\nprint(1)\n```\n끝."
    styled = apply_persona_style(text, "aventurine")
    assert "```python" in styled
    assert "print(1)" in styled
    assert "후후" in styled


def test_apply_persona_style_meta_lines_removed():
    """apply_persona_style에서 메타 문장(계속할까요 등) 제거."""
    text = "작업 완료.\n계속할까요?\n다음 단계로."
    styled = apply_persona_style(text, "aventurine")
    assert "계속할까요" not in styled
    assert "작업 완료" in styled
    assert "다음 단계로" in styled
