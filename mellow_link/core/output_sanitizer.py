"""
Output Sanitizer - 사용자 응답 정제

1. Tool JSON leakage 방지
2. 한국어만 출력 가드
3. 페르소나 발명/전환 차단
"""
import re
import logging
from typing import Optional, Tuple, Any, Iterable

logger = logging.getLogger(__name__)

# Tool JSON 패턴
TOOL_JSON_PATTERNS = [
    r'\{"name"\s*:\s*"[^"]+",\s*"arguments"\s*:\s*\{[^}]*\}\}',
    r'\{"tool"\s*:\s*"[^"]+",\s*"args"\s*:\s*\{[^}]*\}\}',
    r'```json\s*\{[^`]*"name"\s*:\s*"[^"]+"[^`]*\}\s*```',
    r'```\s*\{[^`]*"name"\s*:\s*"[^"]+"[^`]*\}\s*```',
]

# 페르소나 전환 선언 패턴 (무단 페르소나 전환/메타 지시 차단)
# 주의: "Eve", "에브", "이브" 단어 자체는 허용 (alias)
# 차단 대상: 전환 선언, 메타 시스템 재정의 문장
PERSONA_SWITCH_PATTERNS = [
    r'이제\s+.*로\s+말하겠',  # "이제 Eve로 말하겠습니다"
    r'이제\s+.*페르소나.*변환',  # "이제 Eve 페르소나로 변환합니다"
    r'페르소나.*변환',  # "페르소나 변환"
    r'persona.*변환',  # "persona 변환"
    r'persona.*style',  # "persona style"
    r'확인.*계속',  # "확인 후 계속"
    r'확인.*진행',  # "확인 후 진행하겠습니다"
    r'주의사항.*다음',  # "주의사항은 다음과 같습니다"
    r'시스템의\s+히든\s*브레인',  # "시스템의 히든 브레인"
    r'다음\s+규칙을\s+따라',  # "다음 규칙을 따라"
    r'나는\s+이제\s+[가-힣\w]+\s+페르소나',  # "나는 이제 X 페르소나" - 새로운 페르소나 발명
    r'페르소나를\s+[가-힣\w]+\s*로?\s*바꿔',  # "페르소나를 X로 바꿔" - 페르소나 전환
    r'새로운\s+페르소나',  # 새로운 페르소나 언급
    r'페르소나\s+전환',  # 페르소나 전환 언급
]

# Meta-confirmation 패턴 (확인 요청 제거)
META_CONFIRMATION_PATTERNS = [
    r'계속할까요\?',
    r'계속하시겠습니까\?',
    r'진행할까요\?',
    r'请确认是否继续',  # 중국어 확인 요청
    r'続けますか',  # 일본어 확인 요청
    r'続けますか\?',
    r'Should I continue\?',
    r'Do you want me to continue\?',
    r'계속 진행하시겠습니까',
]

# 한국어 범위 (한글 + 공백 + 구두점)
KOREAN_CHAR_RANGE = r'[\uAC00-\uD7A3\u1100-\u11FF\u3130-\u318F]'
# 중국어 범위
CHINESE_CHAR_RANGE = r'[\u4E00-\u9FFF]'
# 일본어 범위
JAPANESE_CHAR_RANGE = r'[\u3040-\u309F\u30A0-\u30FF]'


def sanitize_output(
    text: str,
    llm_service: Optional[Any] = None,
    mode: str = "fast",
    is_admin: bool = False,
    active_persona_id: Optional[str] = None
) -> str:
    """
    사용자 응답을 정제하여:
    1. Tool JSON 블록 제거
    2. 한국어만 출력 강제 (코드 블록 제외)
    3. 페르소나 발명 차단 (설정된 페르소나 제외)
    4. Meta-confirmation 제거
    
    Args:
        text: 원본 텍스트
        llm_service: LLM 서비스 (재작성 필요 시 사용, 현재 미사용)
        mode: 모드 (fast/thinking/thinking-lite)
        is_admin: Admin 사용자 여부 (Admin persona tone 허용)
        active_persona_id: 현재 활성 페르소나 ID (예: "aventurine", None이면 차단)
    
    Returns:
        정제된 텍스트
    """
    if not text or not isinstance(text, str):
        return text
    
    original_text = text
    sanitized = text
    
    logger.debug(f"[OUTPUT_SANITIZER] Before sanitize: len={len(text)}, is_admin={is_admin}, persona_id={active_persona_id}, mode={mode}")
    
    # 1. Tool JSON 제거
    tool_json_detected = False
    for pattern in TOOL_JSON_PATTERNS:
        matches = re.findall(pattern, sanitized, re.IGNORECASE | re.DOTALL)
        if matches:
            tool_json_detected = True
            logger.warning(f"[OUTPUT_SANITIZER] Tool JSON detected -> stripping {len(matches)} blocks")
            sanitized = re.sub(pattern, '', sanitized, flags=re.IGNORECASE | re.DOTALL)
    
    # 2. 무단 페르소나 전환 선언 차단
    # 주의: "Eve", "에브", "이브" 단어 자체는 허용 (alias)
    # 차단 대상: 전환 선언, 메타 시스템 재정의 문장만
    persona_switch_detected = False
    before_persona_check = sanitized
    sanitized = strip_unauthorized_persona_switch(sanitized)
    if sanitized != before_persona_check:
        persona_switch_detected = True
        logger.warning(f"[PERSONA_GUARD] Unauthorized persona switch attempt detected -> blocking (is_admin={is_admin}, persona_id={active_persona_id})")
    
    # 2.5. Meta-confirmation 제거
    meta_confirmation_detected = False
    for pattern in META_CONFIRMATION_PATTERNS:
        if re.search(pattern, sanitized, re.IGNORECASE):
            meta_confirmation_detected = True
            logger.debug(f"[OUTPUT_SANITIZER] Meta-confirmation detected -> removing")
            sanitized = re.sub(pattern, '', sanitized, flags=re.IGNORECASE)
    
    # 정리: 빈 줄 제거
    sanitized = re.sub(r'\n\s*\n\s*\n+', '\n\n', sanitized)
    
    # 3. 한국어만 출력 가드
    non_korean_ratio = _calculate_non_korean_ratio(sanitized)
    # 비한국어 문장 제거 (비율이 높거나 중국어/일본어 문장이 포함된 경우)
    # 중국어/일본어 문장은 비율과 관계없이 제거
    has_cjk_lines = bool(re.search(CHINESE_CHAR_RANGE, sanitized) or re.search(JAPANESE_CHAR_RANGE, sanitized))
    if non_korean_ratio > 0.3 or has_cjk_lines:  # 30% 이상 비한국어 또는 CJK 문장 포함
        if non_korean_ratio > 0.3:
            logger.warning(f"[LANGUAGE_GUARD] Non-Korean ratio {non_korean_ratio:.2%} detected -> enforcing Korean-only")
        if has_cjk_lines:
            logger.warning(f"[LANGUAGE_GUARD] CJK (Chinese/Japanese) lines detected -> removing")
        # 비한국어 문장 제거 (재작성은 AgentBrain에서 비동기로 처리 가능)
        sanitized = _strip_non_korean_lines(sanitized)
        if len(sanitized.strip()) < len(original_text.strip()) * 0.5:  # 너무 많이 제거되면 메시지 추가
            sanitized += "\n\n(일부 문장이 제거되었습니다: 언어 정책)"
    
    # Tool JSON이 감지되었고 제거되었으면 재작성 시도 (비동기 호출은 AgentBrain에서 처리)
    # 여기서는 동기적으로 처리 가능한 부분만 처리
    
    if sanitized != original_text:
        logger.info(
            f"[OUTPUT_SANITIZER] After sanitize: len={len(original_text)} -> {len(sanitized)} chars, "
            f"is_admin={is_admin}, persona_id={active_persona_id}, "
            f"changes: tool_json={tool_json_detected}, persona_switch={persona_switch_detected}, "
            f"meta_conf={meta_confirmation_detected}, non_kr_ratio={non_korean_ratio:.2%}"
        )
    
    return sanitized.strip()


def strip_unauthorized_persona_switch(text: str) -> str:
    """
    무단 페르소나 전환 선언/메타 지시 제거.
    
    "Eve", "에브", "이브" 단어 자체는 허용 (alias).
    전환 선언 패턴만 차단합니다.
    
    Args:
        text: 원본 텍스트
    
    Returns:
        전환 선언이 제거된 텍스트
    """
    if not text:
        return text
    
    sanitized = text
    for pattern in PERSONA_SWITCH_PATTERNS:
        if re.search(pattern, sanitized, re.IGNORECASE):
            logger.debug(f"[PERSONA_GUARD] Persona switch pattern detected: {pattern}")
            sanitized = re.sub(pattern, '', sanitized, flags=re.IGNORECASE)
    
    return sanitized


def _calculate_non_korean_ratio(text: str) -> float:
    """
    비한국어 비율 계산 (코드 블록 제외).
    
    코드 블록(```...```) 내부의 CJK 문자는 계산에서 제외합니다.
    """
    if not text:
        return 0.0
    
    # 코드 블록 제거 (중첩된 코드 블록도 처리)
    # ```...``` 패턴을 제거하되, 중첩된 경우도 처리
    text_without_code = text
    while '```' in text_without_code:
        text_without_code = re.sub(r'```[^`]*```', '', text_without_code, flags=re.DOTALL, count=1)
    
    total_chars = len([c for c in text_without_code if c.isalnum() or ord(c) >= 0xAC00])
    if total_chars == 0:
        return 0.0
    
    korean_chars = len(re.findall(KOREAN_CHAR_RANGE, text_without_code))
    chinese_chars = len(re.findall(CHINESE_CHAR_RANGE, text_without_code))
    japanese_chars = len(re.findall(JAPANESE_CHAR_RANGE, text_without_code))
    
    non_korean_chars = chinese_chars + japanese_chars
    # 영어/숫자는 허용 (기술 용어 등)
    
    return non_korean_chars / total_chars if total_chars > 0 else 0.0


def _strip_non_korean_lines(text: str) -> str:
    """
    비한국어 문장 제거 (코드 블록 제외).
    
    코드 블록(```...```) 내부는 CJK 검사에서 제외합니다.
    """
    lines = text.split('\n')
    filtered_lines = []
    in_code_block = False
    
    for line in lines:
        # 코드 블록 시작/종료 감지
        if '```' in line:
            in_code_block = not in_code_block
            filtered_lines.append(line)  # 코드 블록 마커는 항상 유지
            continue
        
        # 코드 블록 내부는 CJK 검사 제외
        if in_code_block:
            filtered_lines.append(line)
            continue
        
        # 한국어가 있거나, 기술 용어만 있으면 유지
        has_korean = bool(re.search(KOREAN_CHAR_RANGE, line))
        has_chinese = bool(re.search(CHINESE_CHAR_RANGE, line))
        has_japanese = bool(re.search(JAPANESE_CHAR_RANGE, line))
        
        if has_korean or (not has_chinese and not has_japanese):
            filtered_lines.append(line)
    
    return '\n'.join(filtered_lines)


# 재작성 함수들은 필요시 AgentBrain에서 비동기로 호출 가능
# 현재는 동기적 정제(제거)만 수행


def detect_plan_intent(user_input: str) -> bool:
    """
    Plan/To-do 요청 의도 감지.
    
    Returns:
        True if plan intent detected
    """
    if not user_input:
        return False
    
    plan_keywords = [
        "할 일", "투두", "todo", "체크리스트", "checklist",
        "단계", "계획", "plan", "mvp", "로드맵", "roadmap",
        "task list", "작업 목록", "순서", "절차"
    ]
    
    user_lower = user_input.lower()
    return any(keyword in user_lower for keyword in plan_keywords)


def is_plan_only(user_input: str, detected_flags: Optional[Iterable[str]] = None) -> bool:
    """
    "계획만 / 실행하지 마 / 먼저 계획" 요청 여부.
    - user_input에 "실행하지 마", "계획만", "먼저 계획" 포함 시 True
    - detected_flags에 "plan_intent"가 있으면 True (plan_intent가 강하게 잡힌 경우)
    """
    if not user_input and not detected_flags:
        return False
    text = (user_input or "").strip()
    if text:
        lower = text.lower()
        if "실행하지 마" in lower or "실행하지마" in lower:
            return True
        if "계획만" in lower:
            return True
        if "먼저 계획" in lower:
            return True
    flags = list(detected_flags) if detected_flags is not None else []
    if "plan_intent" in flags:
        return True
    return False


def is_execution_approval(user_input: str) -> bool:
    """
    "실행해" / "진행해" 등 계획 실행 승인 의도 여부.
    plan_only와 분리해, 승인 시 T3 실행 단계 진입을 허용할 때 사용.
    """
    if not user_input:
        return False
    lower = (user_input or "").strip().lower()
    return "실행해" in lower or "진행해" in lower


# 결정적 페르소나 래핑용 (코드 블록 보존, 메타 문장 제거)
_CODE_BLOCK_RE = re.compile(r"```.*?```", re.DOTALL)
_META_LINES_RE = re.compile(
    r"(계속할까요|확인\s*후|주의사항|페르소나\s*변환|persona\s*style|请确认|continue\?)",
    re.IGNORECASE,
)


def _split_code_blocks(text: str):
    """텍스트를 코드 블록과 비코드 블록으로 분리. (is_code, chunk) 튜플 리스트 반환."""
    parts = []
    last = 0
    for m in _CODE_BLOCK_RE.finditer(text):
        if m.start() > last:
            parts.append((False, text[last : m.start()]))
        parts.append((True, m.group(0)))
        last = m.end()
    if last < len(text):
        parts.append((False, text[last:]))
    return parts


def apply_persona_style(text: str, persona_id: str, llm_service: Optional[Any] = None) -> str:
    """
    결정적 최소 페르소나 스타일링 (LLM 호출 없음).
    - 내용 변경 없음, 코드 블록 보존
    - 길이 10% 초과 금지, 절대 200자 초과 방지
    - 메타 문장 제거, 접두/접미 최대 1줄씩
    """
    if not text or persona_id not in ("aventurine", "aventurine_admin"):
        return text

    original = text

    # 1) 코드 블록 분리 후 비코드 구간에서만 메타 문장 제거
    parts = _split_code_blocks(text)
    cleaned = []
    for is_code, chunk in parts:
        if is_code:
            cleaned.append(chunk)
        else:
            lines = chunk.splitlines()
            lines = [ln for ln in lines if not _META_LINES_RE.search(ln)]
            cleaned.append("\n".join(lines))

    text = "".join(cleaned).strip()

    if not text:
        return original

    # 2) 최소 래핑 (접두/접미 각 1줄)
    prefix = "후후, 파트너."
    suffix = "오늘 판돈은 여기까지."

    styled = f"{prefix}\n{text}\n{suffix}"

    # 3) 길이 가드 (10% 초과 금지)
    if len(styled) > int(len(text) * 1.10) + 30:
        styled = f"{prefix}\n{text}"

    # 4) 절대 과증가 방지
    if len(styled) > len(original) + 200:
        return original

    return styled


def render_final_answer(
    raw_text: str,
    is_admin: bool,
    persona_id: Optional[str],
    mode: str,
    llm_service: Optional[Any] = None,
) -> str:
    """
    최종 사용자 응답 렌더링 (단일 진입점).
    
    파이프라인:
    1. sanitize_output: Tool JSON 제거, 한국어 전용, 전환 선언 차단
    2. apply_persona_style: Admin 전용 페르소나 스타일 적용
    
    중요:
    - 이 함수는 최종 사용자 응답에만 사용되어야 합니다.
    - 내부 추론/계획/도구 실행 단계에서는 절대 호출하지 마세요.
    - Progress UI 이벤트는 이 함수를 거치지 않아야 합니다.
    
    Args:
        raw_text: 원본 텍스트 (LLM 출력 또는 요약)
        is_admin: Admin 사용자 여부
        persona_id: 페르소나 ID (예: "aventurine", None)
        mode: 모드 (fast/thinking/thinking-lite)
        llm_service: LLM 서비스 (페르소나 스타일 적용용, Optional)
    
    Returns:
        최종 렌더링된 텍스트
    """
    if not raw_text:
        return raw_text
    
    # 1. Sanitization (항상 실행)
    sanitized = sanitize_output(
        raw_text,
        llm_service=None,  # sanitization에는 LLM 불필요
        mode=mode,
        is_admin=is_admin,
        active_persona_id=persona_id
    )
    
    # 2. Persona styling (Admin 전용, 결정적 최소 래핑, LLM 호출 없음)
    if is_admin and persona_id:
        normalized_persona_id = "aventurine_admin" if persona_id in ("aventurine", "aventurine_admin") else persona_id
        final_text = apply_persona_style(sanitized, normalized_persona_id, llm_service=None)
        logger.debug(f"[RENDER_FINAL] Persona style applied (is_admin={is_admin}, persona_id={persona_id})")
        return final_text
    
    # Non-admin 또는 persona_id가 없으면 sanitized만 반환
    logger.debug(f"[RENDER_FINAL] No persona style (is_admin={is_admin}, persona_id={persona_id})")
    return sanitized
