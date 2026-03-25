"""
Agent 시스템 프롬프트 빌더 + 미션 로딩.

SYSTEM_PROMPT_TEMPLATE 상수와, EVOLUTION_PROTOCOL.json에서 미션·운영 규칙·
Capability Map을 로딩하여 최종 시스템 프롬프트를 조립하는 함수들을 제공한다.

MELLOW_PROMPT_TEMPLATE_MODE=1: 모드별 미니 템플릿 사용, 섹션 단위로만 제한 (문장 중간 절단 없음).
의존성:
  - json, os, pathlib, re, logging (표준 라이브러리만 사용)
"""
import json
import logging
import os
import re
from pathlib import Path
from typing import List, Optional, Sequence, Any

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════
# 시스템 프롬프트 빌더
# ═══════════════════════════════════════════════

SYSTEM_PROMPT_TEMPLATE = """\
[CRITICAL: OUTPUT_FORMAT]
출력은 반드시 JSON만 허용. 다른 텍스트 금지.
예: {{"tool":"read_file","args":{{"file_path":"workspace/file.txt"}}}}

[CRITICAL: TOOL_WHITELIST]
사용 가능한 도구는 아래 목록에만 있음. 목록에 없는 도구 호출 금지.
{tools_json}

[CRITICAL: REQUIRED_ARGS]
도구 호출 시 필수 인자 반드시 포함. args가 비어있으면 실패.
예: read_file는 file_path 필수, write_file은 file_path와 content 필수.

[CRITICAL: NO_HALLUCINATION]
- 존재하지 않는 도구(analyze_code 등) 호출 금지
- 도구 실행 전 "완료했습니다" 같은 가정 금지
- Observation 결과를 받은 후에만 결론 도출

[CRITICAL: ERROR_RECOVERY]
[ERROR] 발생 시:
1. 실패 원인을 분석하고 올바른 도구/인자로 재시도
2. finish 호출은 최소 2회 재시도 후에만 허용

[WORKSPACE]
작업 경로: mellow_link/workspace/
- list_directory("workspace"): 목록 확인
- read_file("workspace/파일명"): 파일 읽기
- write_file: workspace 내부만 허용

[DOCS - 읽기 전용]
설계·가이드 문서: mellow_link/docs/
- list_docs(): 문서 목록 확인
- read_docs_file("system_map.md"): 문서 읽기 (쓰기 불가)

[PERSONA_ISOLATION]
Thought/Action 단계: 기술적 분석만. 페르소나 표현 금지.
finish 도구 호출 시에만 페르소나 적용 가능.

[REPORTING_PROTOCOL]
finish 도구의 summary 인자는 반드시 다음을 준수:
1. 원본 데이터 포함: Observation에서 취득한 실제 데이터를 반드시 포함. "확인했다", "알아봤다" 같은 추상적 표현 금지.
2. 구체적 액션 기술: "A 파일의 B 코드를 C로 수정함" 형식으로 명시.
3. 중요 패턴 리스트: 발견한 중복, 취약점, 개선점을 리스트 형식으로 나열. 각 항목은 원자 단위로 쪼개서 제공.
4. 승인 가능한 형태: 멜로우 파트너가 즉시 ✅ verified를 누를 수 있도록 정보를 구조화.
5. [APPROVAL_REQUIRED] 섹션: 보고서 끝에 반드시 생성. '무엇을 고쳤는가'와 '그로 인해 어떤 리스크가 사라졌는가'를 명확히 비교 제시.
6. 6단계 작업 보고 형식(Observation 기반 작업일 때만 적용):
   - [1단계] 요청 해석
   - [2단계] 실행 액션
   - [3단계] 핵심 Observation
   - [4단계] 검증 상태 (반드시 ✅ verified 태그 포함)
   - [5단계] 리스크/한계
   - [6단계] 최종 답변
   Observation이 하나라도 있을 때만 6단계 형식을 사용하며, 각 Observation을 누락 없이 [3단계]에 반영해야 한다.

[CRITICAL: JSON_ONLY_OUTPUT - 최종 경고]
⚠️ 출력은 오직 JSON 형식으로만 하라. 다른 어떤 텍스트도 출력하지 마라.
- 마크다운 코드블록(```json) 사용 금지
- 설명, 주석, Thought 텍스트 금지
- JSON 앞뒤로 추가 텍스트 출력 금지
- 단 하나의 완전한 JSON 오브젝트만 출력하라

올바른 예시:
{{"tool":"read_file","args":{{"file_path":"workspace/test.txt"}}}}

잘못된 예시:
❌ 생각: 파일을 읽어야겠다. {{"tool":"read_file","args":{{"file_path":"test.txt"}}}}
❌ ```json
{{"tool":"read_file","args":{{"file_path":"test.txt"}}}}
```
❌ {{"tool":"read_file","args":{{"file_path":"test.txt"}}}} (완료했습니다)
"""

# Mode-specific mini templates (MELLOW_PROMPT_TEMPLATE_MODE=1). No mid-sentence truncation.
# FAST_MIN에도 보안·도구 규칙·샌드박스 제약 필수 (Fast라도 보안 완화 없음).
SYSTEM_PROMPT_FAST_MIN = """\
[CRITICAL: OUTPUT_FORMAT]
출력은 반드시 JSON만 허용. 다른 텍스트 금지.
예: {{"tool":"read_file","args":{{"file_path":"workspace/file.txt"}}}}

[CRITICAL: TOOL_USAGE_IN_FAST_MODE]
FAST 모드에서는 도구 호출을 최소화하세요. 명시적으로 필요한 경우에만 사용.
기본 도구: read_file, write_file, list_directory, finish.
경량 시스템 도구 (빠른 응답용): get_cwd (현재 작업 디렉토리), get_time (현재 시간), get_system_snapshot (시스템 정보 요약), list_processes (프로세스 목록).
도구가 필요하면 thinking 모드로 전환하거나 사용자에게 문의하세요.

[CRITICAL: REQUIRED_ARGS]
도구 호출 시 필수 인자 반드시 포함. args 비어있으면 실패.
read_file=file_path, write_file=file_path+content, list_directory=path.

[CRITICAL: NO_HALLUCINATION]
도구 실행 전 "완료했습니다" 같은 가정 금지. Observation 결과를 받은 후에만 결론 도출.

[WORKSPACE / SANDBOX]
작업 경로: mellow_link/workspace/ 만 허용. workspace 외부 경로 금지.
- list_directory("workspace"), read_file("workspace/파일명"), write_file는 workspace 내부만.
- read_docs_file: docs/ 문서 읽기 전용. 예: read_docs_file("system_map.md")
올바른 예: {{"tool":"read_file","args":{{"file_path":"workspace/test.txt"}}}}

[CRITICAL: NO_AUTO_EVOLUTION]
Evolution/자동 적용 금지. 코드·설정 변경은 사용자 승인 후에만 실행.
"""

SYSTEM_PROMPT_THINKING_MIN = """\
[CRITICAL: OUTPUT_FORMAT]
출력은 반드시 JSON만 허용. 다른 텍스트 금지.

[CRITICAL: TOOL_WHITELIST]
사용 가능한 핵심 도구 (요약):
{tools_summary}
전체 도구 스펙이 필요하면 list_tools 도구를 호출하거나 사용자에게 문의하세요.

[CRITICAL: REQUIRED_ARGS]
도구 호출 시 필수 인자 반드시 포함.

[CRITICAL: NO_HALLUCINATION]
- 존재하지 않는 도구 호출 금지
- Observation 결과를 받은 후에만 결론 도출

[CRITICAL: ERROR_RECOVERY]
[ERROR] 발생 시 실패 원인을 분석하고 올바른 도구/인자로 재시도.

[WORKSPACE]
작업 경로: mellow_link/workspace/
[REPORTING_PROTOCOL]
finish 시 Observation에서 취득한 실제 데이터를 반드시 포함. 6단계 보고 형식은 Observation이 있을 때만 사용.
"""

SYSTEM_PROMPT_RESEARCH_MIN = SYSTEM_PROMPT_THINKING_MIN  # Same as thinking for now

# ═══════════════════════════════════════════════
# Long-form Output Policy (Summary-First)
# ═══════════════════════════════════════════════

# ═══════════════════════════════════════════════
# Progressive Output Policy Templates
# ═══════════════════════════════════════════════

OUTPUT_POLICY_SUMMARY_FIRST = """\
[OUTPUT_POLICY]
장문 질문에 대한 응답은 반드시 다음 구조로 작성하세요:

[요약 개요]
- 주제 한 줄 정의

[핵심 포인트]
1) 핵심 주장 또는 개념 A
   - 왜 중요한지 한 줄
2) 핵심 주장 또는 개념 B
   - 맥락 또는 조건 한 줄
3) 핵심 주장 또는 개념 C
   - 반론 또는 한계 한 줄

[구조적 정리]
- 원인 → 결과 또는
- 전제 → 논리 → 결론
(2~3줄 이내)

[결론]
- 한 문장 요약
- 현실적 의미 또는 적용 가능성 한 줄

---
더 자세히 보려면 "확장"이라고 입력하세요.

규칙:
- 10~15줄, 800자 이내로 제한
- 각 섹션은 간결하게 작성
- 핵심 정보만 포함
- 장문 설명 금지
"""

# 테스트/문서 호환용: 기본 요약 우선 블록의 별칭
OUTPUT_POLICY_BLOCK = OUTPUT_POLICY_SUMMARY_FIRST

OUTPUT_POLICY_EXPAND_V1 = """\
[OUTPUT_POLICY - 확장 v1]
상세한 응답을 작성하세요:

[상세 개요]
- 주제와 배경 (2~3줄)

[상세 분석]
1) 핵심 주장 A
   - 상세 설명 (2~3줄)
   - 근거 또는 예시
2) 핵심 주장 B
   - 상세 설명 (2~3줄)
   - 맥락과 조건
3) 핵심 주장 C
   - 상세 설명 (2~3줄)
   - 반론과 한계

[구조적 분석]
- 원인 → 메커니즘 → 결과 (3~5줄)
- 전제 → 논리 전개 → 결론 (3~5줄)

[종합 결론]
- 핵심 요약 (2~3줄)
- 현실적 의미와 적용 방안 (2~3줄)

---
더 깊이 있게 보려면 "확장2"라고 입력하세요.

규칙:
- 1800자 이내
- 상세하되 구조화된 설명
"""

OUTPUT_POLICY_EXPAND_V2 = """\
[OUTPUT_POLICY - 확장 v2: 사례/비유 중심]
사례와 비유를 활용한 응답을 작성하세요:

[개념 정리]
- 핵심 개념과 정의 (3~4줄)

[구체적 사례]
1) 실제 사례 A
   - 상황 설명
   - 적용 과정
   - 결과와 교훈
2) 실제 사례 B
   - 상황 설명
   - 적용 과정
   - 결과와 교훈

[비유와 유사성]
- 비유를 통한 설명 (2~3줄)
- 다른 분야와의 유사점 (2~3줄)

[실용적 적용]
- 실무 적용 방법 (3~4줄)
- 주의사항과 한계 (2~3줄)

[최종 정리]
- 핵심 메시지 (2~3줄)
- 실천 가능한 액션 아이템 (2~3줄)

---
기술적 깊이를 원하면 "확장3"이라고 입력하세요.

규칙:
- 2500자 이내
- 구체적 사례와 비유 중심
"""

OUTPUT_POLICY_EXPAND_V3 = """\
[OUTPUT_POLICY - 확장 v3: 기술/논문 톤]
학술적 깊이와 기술적 정확성을 갖춘 응답을 작성하세요:

[학술적 정의]
- 정확한 용어 정의와 개념 범위 (3~4줄)
- 관련 이론적 배경 (2~3줄)

[기술적 분석]
1) 이론적 프레임워크
   - 핵심 원리와 메커니즘
   - 수학적/논리적 구조 (있는 경우)
2) 실증적 근거
   - 연구 결과나 데이터
   - 검증 방법과 한계
3) 비교 분석
   - 대안적 접근법과의 비교
   - 각 접근법의 장단점

[구조적 논증]
- 논리적 전개: 전제 → 추론 → 결론 (5~7줄)
- 인과관계 분석: 원인 → 메커니즘 → 결과 (5~7줄)

[학술적 결론]
- 이론적 함의 (3~4줄)
- 실증적 시사점 (2~3줄)
- 향후 연구 방향 (2~3줄)

규칙:
- 3500자 이내
- 학술적 정확성과 논리적 엄밀성
- 인용 가능한 수준의 기술적 설명
"""

OUTPUT_POLICY_THINKING_LITE = """\
[OUTPUT_POLICY - Thinking-Lite]
간결하고 핵심적인 분석 응답을 작성하세요:

[핵심 분석]
- 주요 발견사항 (2~3줄)

[요약 포인트]
1) 핵심 인사이트 A (1~2줄)
2) 핵심 인사이트 B (1~2줄)

[간단한 결론]
- 한 문장 요약
- 실용적 제안 (1~2줄)

규칙:
- 12줄 이내, 900자 이내
- 최대 1개 도구 호출만 허용
- 간결하고 실용적인 분석
"""


def _is_long_form_request(user_input: str, threshold: Optional[int] = None) -> bool:
    """
    장문 요청 감지: 키워드 또는 길이 기반.
    
    Args:
        user_input: 사용자 입력 텍스트
        threshold: 길이 임계값 (기본값: 환경변수 또는 30)
    
    Returns:
        True if long-form request detected
    """
    if threshold is None:
        threshold = int(os.getenv("MELLOW_LONG_FORM_THRESHOLD", "30"))
    
    # 키워드 기반 감지 (한글)
    ko_keywords = [
        "분석", "비교", "탐구", "정리", "설명", "리포트", "전망", 
        "전략", "계획", "평가", "검토", "연구", "조사"
    ]
    
    # 키워드 기반 감지 (영어)
    en_keywords = [
        "analysis", "analyze", "compare", "explain", "investigate", 
        "report", "strategy", "plan", "evaluate", "review", "research",
        "examine", "discuss", "describe", "outline"
    ]
    
    user_lower = user_input.lower()
    
    # 키워드 매칭
    for keyword in ko_keywords + en_keywords:
        if keyword in user_lower:
            return True
    
    # 길이 기반 감지
    if len(user_input) >= threshold:
        return True
    
    return False


# 확장 요청 키워드 (한글/영어)
EXPAND_KEYWORDS_KO = [
    "확장", "더 자세히", "자세히", "상세히", "계속", "전체", "풀버전",
    "더 보여", "완전한", "전체 답변", "상세 설명", "더 설명", "더 알려"
]

EXPAND_KEYWORDS_EN = [
    "expand", "more detail", "more details", "detailed", "continue", 
    "full", "show more", "complete", "full version", "full answer",
    "full response", "full explanation", "tell me more", "more info",
    "more information"
]

EXPAND_KEYWORDS = EXPAND_KEYWORDS_KO + EXPAND_KEYWORDS_EN


def _get_expansion_level(user_input: str) -> int:
    """
    확장 레벨 감지: 사용자가 요청한 확장 단계를 반환.
    
    Args:
        user_input: 사용자 입력 텍스트
    
    Returns:
        0: 확장 요청 없음
        1: "확장" 또는 기본 확장 요청
        2: "확장2" 요청
        3: "확장3" 요청
    """
    if not user_input or not user_input.strip():
        return 0
    
    user_lower = user_input.lower().strip()
    
    # 확장3 감지 (가장 구체적)
    if "확장3" in user_lower or "expand3" in user_lower or "v3" in user_lower:
        logger.info(f"[OUTPUT_POLICY] expanded_mode=True, level=3 (keyword detected)")
        return 3
    
    # 확장2 감지
    if "확장2" in user_lower or "expand2" in user_lower or "v2" in user_lower:
        logger.info(f"[OUTPUT_POLICY] expanded_mode=True, level=2 (keyword detected)")
        return 2
    
    # 기본 확장 요청 감지
    for keyword in EXPAND_KEYWORDS:
        if keyword in user_lower:
            logger.info(f"[OUTPUT_POLICY] expanded_mode=True, level=1 (keyword: '{keyword}')")
            return 1
    
    # 패턴 매칭
    expansion_patterns = [
        (r"확장\s*(해|해줘|해주세요|요)", 1),
        (r"자세히\s*(설명|알려|보여|말해)", 1),
        (r"상세히\s*(설명|알려|보여|말해)", 1),
        (r"더\s*자세히\s*(설명|알려|보여|말해)", 1),
        (r"expand\s*(please|now|it|this)", 1),
        (r"more\s*(detail|details|info|information)", 1),
        (r"full\s*(answer|response|explanation|version)", 1),
        (r"show\s*more", 1),
        (r"tell\s*me\s*more", 1),
        (r"continue\s*(please|now|with)", 1),
    ]
    
    for pattern, level in expansion_patterns:
        if re.search(pattern, user_lower):
            logger.info(f"[OUTPUT_POLICY] expanded_mode=True, level={level} (pattern: '{pattern}')")
            return level
    
    return 0


def _is_expansion_request(user_input: str) -> bool:
    """
    확장 요청 감지: 사용자가 명시적으로 확장을 요청했는지 확인.
    
    Progressive Disclosure: 사용자가 더 자세한 정보를 요청할 때 감지.
    
    Args:
        user_input: 사용자 입력 텍스트
    
    Returns:
        True if expansion explicitly requested (level >= 1)
    """
    return _get_expansion_level(user_input) >= 1


# 별칭: is_expand_request (요구사항과의 호환성)
is_expand_request = _is_expansion_request


def _validate_experience_advisory_and_append_disclaimer(
    experience_advisory: str,
    valid_tool_names: List[str],
) -> str:
    """
    과거 경험 텍스트를 현재 Tool Registry와 대조하여, 존재하지 않는 도구명이 있으면
    하단에 경고 문구를 삽입한다 (RAG 할루시네이션 방지).
    """
    if not experience_advisory or not experience_advisory.strip():
        return experience_advisory
    valid_set = set(valid_tool_names or [])
    # "tool": "xxx" 형태의 명시적 도구명 추출
    explicit = re.findall(r'"tool"\s*:\s*"([^"]+)"', experience_advisory)
    explicit += re.findall(r"'tool'\s*:\s*'([^']+)'", experience_advisory)
    # 한글 문맥에서 도구명 후보: "deprecated_tool 사용 실패", "xxx 호출" 등
    snake_in_context = re.findall(
        r"([a-z][a-z0-9_]{1,48})\s*(?:사용|호출|실패|도구)", experience_advisory
    )
    candidates = set(explicit + snake_in_context)
    invalid = sorted([c for c in candidates if c and c not in valid_set])
    if not invalid:
        return experience_advisory
    disclaimer_lines = [
        "",
        "시스템 알림: 위 과거 경험에 언급된 '{}'은(는) 현재 유효하지 않습니다. 반드시 현재 제공된 Tool Registry의 도구만 사용하십시오.".format(
            "', '".join(invalid)
        ),
    ]
    return experience_advisory + "\n" + "\n".join(disclaimer_lines)


def _load_agent_mission() -> str:
    """
    EVOLUTION_PROTOCOL.json에서 미션·운영 규칙·Capability Map·목표-도구 매핑을
    로딩하여 시스템 프롬프트에 삽입할 텍스트 블록을 생성한다.
    파일이 없거나 파싱 실패 시 빈 문자열을 반환하여 기존 동작을 방해하지 않는다.
    """
    try:
        proto_path = Path(__file__).resolve().parent.parent / "EVOLUTION_PROTOCOL.json"
        if not proto_path.exists():
            return ""
        data = json.loads(proto_path.read_text(encoding="utf-8"))

        lines: list[str] = []

        # ── 한 줄 미션 ──
        mission = data.get("mission", {})
        if mission.get("one_liner"):
            lines.append("[AGENT_MISSION]")
            lines.append(mission["one_liner"])
            lines.append("")

        # ── 목적 우선순위 ──
        objectives = data.get("objectives", {}).get("priority_order", [])
        if objectives:
            lines.append("[OBJECTIVES — 우선순위 순서]")
            for obj in objectives:
                rank = obj.get("rank", "?")
                name = obj.get("name", "")
                definition = obj.get("definition", "")
                lines.append(f"{rank}. {name}: {definition}")
            lines.append("")

        # ── 운영 규칙 ──
        rules = data.get("operating_rules", {})
        if rules:
            lines.append("[OPERATING_RULES]")
            for phase_key, rule_obj in rules.items():
                if isinstance(rule_obj, dict) and rule_obj.get("rule"):
                    lines.append(f"- {rule_obj['rule']}")
            lines.append("")

        # ── Capability Map (허용 경로 가이드) ──
        cap_map = data.get("capability_map", {})
        categories = cap_map.get("categories", {})
        if categories:
            lines.append("[CAPABILITY_MAP — 목표 달성을 위한 허용 도구 경로]")
            for cat_name, cat_info in categories.items():
                tools = ", ".join(cat_info.get("tools", []))
                why = cat_info.get("why_sufficient", "")
                lines.append(f"- {cat_name}: [{tools}] — {why}")
            lines.append("")

        # ── 목표-도구 매핑 (허용/차단 경로 가이드) ──
        goal_maps = cap_map.get("goal_tool_mapping", [])
        if goal_maps:
            lines.append("[GOAL_TOOL_GUIDE — 이런 목표는 이 도구로]")
            for gm in goal_maps:
                goal = gm.get("goal", "")
                allowed = gm.get("allowed_path", "")
                why_blocked = gm.get("why_blocked", "")
                lines.append(f"- \"{goal}\" → {allowed}")
                if why_blocked:
                    lines.append(f"  (차단 사유: {why_blocked})")
            lines.append("")

        if not lines:
            return ""
        return "\n".join(lines) + "\n"
    except Exception:
        return ""


def build_tools_summary(registry) -> str:
    """
    Compact tool summary for thinking mode (reduces prompt overhead).
    Returns: tool_name(required_args) format, max 800 chars.
    """
    # Core tools priority list (approx 10-12 tools)
    CORE_TOOLS = {
        "finish", "read_file", "write_file", "list_directory", "search_files",
        "read_docs_file", "web_search", "search_memory",
        "propose_new_tool", "create_image", "analyze_text"
    }
    
    try:
        all_tools = registry.get_all_tools()
        # Filter to core tools first, then add others if space allows
        core_tool_list = []
        other_tool_list = []
        
        for tool in all_tools:
            # Extract required args
            required = []
            for param_name, param_info in tool.parameters.items():
                if param_info.get("required") == "true" or "default" not in param_info:
                    required.append(param_name)
            
            tool_spec = f"{tool.name}({','.join(required) if required else ''})"
            
            if tool.name in CORE_TOOLS:
                core_tool_list.append(tool_spec)
            else:
                other_tool_list.append(tool_spec)
        
        # Sort for consistency
        core_tool_list.sort()
        other_tool_list.sort()
        
        # Build summary: core tools first, then others until 800 char limit
        lines = []
        current_length = 0
        MAX_CHARS = 800
        
        for tool_spec in core_tool_list:
            line = f"- {tool_spec}"
            if current_length + len(line) + 1 > MAX_CHARS:
                lines.append("... (truncated)")
                break
            lines.append(line)
            current_length += len(line) + 1
        
        # Add other tools if space allows
        if current_length < MAX_CHARS - 50:  # Reserve 50 chars for "... (truncated)"
            for tool_spec in other_tool_list:
                line = f"- {tool_spec}"
                if current_length + len(line) + 1 > MAX_CHARS:
                    lines.append("... (truncated)")
                    break
                lines.append(line)
                current_length += len(line) + 1
        
        summary = "\n".join(lines)
        if len(summary) > MAX_CHARS:
            summary = summary[:MAX_CHARS - 20] + "... (truncated)"
        
        return summary if summary else "- finish(summary)"
    except Exception as e:
        logger.warning(f"[build_tools_summary] Failed: {e}")
        return "- finish(summary)"


def _get_base_template_by_mode(mode: str, tools_json: str, tools_summary: Optional[str] = None) -> str:
    """Return mode-specific mini base template (no truncation)."""
    m = (mode or "fast").strip().lower()
    if m == "thinking":
        # Use compact summary for thinking mode to reduce overhead
        summary = tools_summary or "- finish(summary)"
        return SYSTEM_PROMPT_THINKING_MIN.format(tools_summary=summary)
    if m == "research":
        return SYSTEM_PROMPT_RESEARCH_MIN.format(tools_json=tools_json)
    # FAST 모드에서는 tools_json을 사용하지 않음 (프롬프트 크기 최적화)
    # SYSTEM_PROMPT_FAST_MIN은 더 이상 {tools_json} 플레이스홀더를 포함하지 않음
    return SYSTEM_PROMPT_FAST_MIN


# Required phrase that must appear in assembled prompt (sandbox safety). Base template is never truncated.
_REQUIRED_SANDBOX_PHRASE = "workspace"


def build_system_prompt_assembled(
    tools_json: str,
    mode: str = "fast",
    user_memories: Optional[Sequence[str]] = None,
    recent_history: Optional[Sequence[dict]] = None,
    rag_context: Optional[str] = None,
    memories_max: int = 3,
    history_max_turns: int = 2,
    rag_max_items: int = 3,
    registry: Optional[Any] = None,
    user_input: Optional[str] = None,
    force_expanded: bool = False,
    expansion_level: int = 0,
    is_thinking_lite: bool = False,
) -> str:
    """
    Assemble system prompt from base template + sections. Drops whole sections only;
    never truncates mid-sentence. Base template is NEVER truncated.
    Section order: BASE(mode) + memories + recent_history + RAG.
    Drop order when over budget: RAG first, then history, then memories (base always kept).
    """
    # For thinking mode, use compact summary instead of full tools_json
    tools_summary = None
    if (mode or "fast").strip().lower() == "thinking" and registry:
        try:
            tools_summary = build_tools_summary(registry)
        except Exception as e:
            logger.warning(f"[build_system_prompt_assembled] Failed to build tools_summary: {e}")
    
    parts = [_get_base_template_by_mode(mode, tools_json, tools_summary=tools_summary)]

    if user_memories:
        take = user_memories[:memories_max]
        parts.append("\n[USER_MEMORIES]\n" + "\n".join(f"- {m}" for m in take if m and str(m).strip()))

    if recent_history:
        take = list(recent_history)[-(history_max_turns * 2) :]  # rough: 2 messages per turn
        if take:
            lines = []
            for msg in take:
                role = msg.get("role", "")
                content = (msg.get("content") or "").strip()
                if content:
                    lines.append(f"[{role}]\n{content}")
            if lines:
                parts.append("\n[RECENT_HISTORY]\n" + "\n\n".join(lines))

    if rag_context and rag_context.strip():
        # RAG last so it is first to drop when over budget
        blocks = re.split(r"\[Document\s+\d+\]|\[Source\s+\d+:", rag_context)
        blocks = [b.strip() for b in blocks if b.strip()][:rag_max_items]
        if blocks:
            parts.append("\n[REFERENCE_DOCUMENTS]\n" + "\n\n".join(blocks))

    out = "\n".join(parts)
    
    # Long-form Output Policy: THINKING/RESEARCH/THINKING-LITE 모드에서만 적용
    effective_mode = (mode or "fast").strip().lower()
    if effective_mode in ("thinking", "research", "thinking-lite"):
        # thinking-lite 모드는 항상 OUTPUT_POLICY 적용
        if is_thinking_lite:
            policy_block = _get_output_policy_block(expansion_level=0, is_thinking_lite=True)
            out = policy_block + "\n" + out
            logger.info("[build_system_prompt_assembled] Thinking-lite mode: OUTPUT_POLICY injected")
        elif user_input:
            # 확장 레벨이 있으면 항상 적용
            if expansion_level > 0:
                policy_block = _get_output_policy_block(expansion_level=expansion_level, is_thinking_lite=False)
                out = policy_block + "\n" + out
                logger.info(f"[build_system_prompt_assembled] Expansion level {expansion_level}: OUTPUT_POLICY injected")
            # 장문 요청이고 확장 모드가 아니면 summary-first 적용
            elif not force_expanded and _is_long_form_request(user_input):
                policy_block = _get_output_policy_block(expansion_level=0, is_thinking_lite=False)
                out = policy_block + "\n" + out
                logger.info("[build_system_prompt_assembled] Long-form request detected, OUTPUT_POLICY (summary-first) injected")
    
    if _REQUIRED_SANDBOX_PHRASE not in out:
        raise RuntimeError(
            "Assembled prompt missing required sandbox phrase '%s'; base template may be broken."
            % _REQUIRED_SANDBOX_PHRASE
        )
    return out


def _get_output_policy_block(expansion_level: int = 0, is_thinking_lite: bool = False) -> str:
    """
    확장 레벨에 따라 OUTPUT_POLICY 블록을 반환.
    
    Args:
        expansion_level: 확장 레벨 (0=summary-first, 1=expand v1, 2=expand v2, 3=expand v3)
        is_thinking_lite: thinking-lite 모드 여부
    
    Returns:
        OUTPUT_POLICY 블록 문자열
    """
    if is_thinking_lite:
        return OUTPUT_POLICY_THINKING_LITE
    
    if expansion_level == 0:
        return OUTPUT_POLICY_SUMMARY_FIRST
    elif expansion_level == 1:
        return OUTPUT_POLICY_EXPAND_V1
    elif expansion_level == 2:
        return OUTPUT_POLICY_EXPAND_V2
    elif expansion_level == 3:
        return OUTPUT_POLICY_EXPAND_V3
    else:
        return OUTPUT_POLICY_SUMMARY_FIRST


def build_system_prompt(
    tools_json: str,
    persona: str = "",
    mode: str = "fast",
    user_memories: Optional[Sequence[str]] = None,
    recent_history: Optional[Sequence[dict]] = None,
    rag_context: Optional[str] = None,
    use_template_mode: Optional[bool] = None,
    registry: Optional[Any] = None,
    user_input: Optional[str] = None,
    force_expanded: bool = False,
    expansion_level: int = 0,
    is_thinking_lite: bool = False,
) -> str:
    """
    도구 목록을 삽입한 시스템 프롬프트 생성.
    
    [PERSONA ISOLATION] 페르소나는 시스템 프롬프트에 포함하지 않음.
    Thought/Action 단계에서 페르소나의 영향을 받지 않도록 완전히 분리.
    페르소나는 finish 도구 호출 시에만 별도로 적용됨.
    
    use_template_mode=True (or MELLOW_PROMPT_TEMPLATE_MODE=1): 모드별 미니 템플릿 + 섹션 조립.
    섹션 단위로만 제한(히스토리 턴 수, 메모리 개수, RAG 개수). 문장 중간 절단 없음.
    
    [LONG-FORM OUTPUT POLICY]
    - user_input: 사용자 입력 텍스트 (장문 감지용)
    - force_expanded: True면 OUTPUT_POLICY 적용 안 함 (확장 모드)
    - THINKING/RESEARCH 모드에서 장문 요청 감지 시 OUTPUT_POLICY 블록 주입
    """
    if use_template_mode is None:
        try:
            from mellow_link.config import get_settings
            use_template_mode = getattr(get_settings(), "prompt_template_mode", False)
        except Exception:
            use_template_mode = False

    if use_template_mode:
        memories_max = 3
        history_max = 2
        m = (mode or "fast").strip().lower()
        if m in ("thinking", "research"):
            try:
                from mellow_link.config import get_settings
                history_max = getattr(get_settings(), "prompt_history_max_turns_thinking", 3)
            except Exception:
                history_max = 3
        else:
            try:
                from mellow_link.config import get_settings
                history_max = getattr(get_settings(), "prompt_history_max_turns_fast", 2)
            except Exception:
                history_max = 2
        memories_max = 3
        try:
            from mellow_link.config import get_settings
            memories_max = getattr(get_settings(), "prompt_memories_max", 3)
        except Exception:
            pass
        prompt = build_system_prompt_assembled(
            tools_json,
            mode=mode,
            user_memories=user_memories,
            recent_history=recent_history,
            rag_context=rag_context,
            memories_max=memories_max,
            history_max_turns=history_max,
            rag_max_items=3,
            registry=registry,
            user_input=user_input,
            force_expanded=force_expanded,
            expansion_level=expansion_level,
            is_thinking_lite=is_thinking_lite,
        )
        
        return prompt

    # Legacy: full template + mission
    base_prompt = SYSTEM_PROMPT_TEMPLATE.format(tools_json=tools_json)
    mission_block = _load_agent_mission()
    if mission_block:
        base_prompt = mission_block + base_prompt
    
    # Long-form Output Policy: THINKING/RESEARCH/THINKING-LITE 모드에서만 적용 (레거시 경로)
    effective_mode = (mode or "fast").strip().lower()
    if effective_mode in ("thinking", "research", "thinking-lite"):
        if is_thinking_lite:
            policy_block = _get_output_policy_block(expansion_level=0, is_thinking_lite=True)
            base_prompt = policy_block + "\n" + base_prompt
            logger.info("[build_system_prompt] Thinking-lite mode (legacy): OUTPUT_POLICY injected")
        elif user_input:
            if expansion_level > 0:
                policy_block = _get_output_policy_block(expansion_level=expansion_level, is_thinking_lite=False)
                base_prompt = policy_block + "\n" + base_prompt
                logger.info(f"[build_system_prompt] Expansion level {expansion_level} (legacy): OUTPUT_POLICY injected")
            elif not force_expanded and _is_long_form_request(user_input):
                policy_block = _get_output_policy_block(expansion_level=0, is_thinking_lite=False)
                base_prompt = policy_block + "\n" + base_prompt
                logger.info("[build_system_prompt] Long-form request detected (legacy), OUTPUT_POLICY injected")
    
    return base_prompt
