"""
Agent 출력 파서 / 보고서 포맷터 / 패턴 추출.

LLM 응답에서 JSON Action을 파싱하고, 6단계 보고서 형식을 생성하며,
성공 패턴과 한계를 추출하는 함수들을 제공한다.

의존성:
  - agent_schemas.py : AgentAction, AgentStep
"""
import json
import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Union

from mellow_link.core.agent_schemas import AgentAction, AgentStep

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════
# Output Parser (LLM 응답 → AgentAction)
# ═══════════════════════════════════════════════

# JSON 블록 추출: ```json ... ``` 또는 { ... } 패턴
_JSON_BLOCK_RE = re.compile(
    r'```(?:json)?\s*(\{.*?\})\s*```'            # fenced code block
    r'|'
    r'(\{"tool"\s*:.*?"args"\s*:\s*\{[^}]*\}\})'  # inline: {"tool":...,"args":{...}}
    r'|'
    r'(\{[^{}]*"tool"\s*:\s*"[^"]+?"[^{}]*\})',   # inline: {"tool":"..."} (no nested)
    re.DOTALL,
)

# Thought만 길고 Action 없음(할루시네이션) 판별 임계: 이 길이 초과 시 재추론 요청
REASONING_ONLY_HALLUCINATION_THRESHOLD = 150


def _filter_prompt_echoing(text: str) -> str:
    """
    [OUTPUT_FILTER] 프롬프트 복창(Prompt Echoing) 텍스트를 제거합니다.
    
    SLM이 시스템 프롬프트의 일부를 그대로 출력하는 경우를 감지하고 제거합니다.
    """
    if not text:
        return text
    
    # 프롬프트 복창 패턴 감지 및 제거
    prompt_echo_patterns = [
        r'\[CRITICAL:.*?\]',  # [CRITICAL: ...] 형태의 프롬프트 복창
        r'\[COMMAND:.*?\]',   # [COMMAND: ...] 형태
        r'⚠️.*?금지',          # 경고 문구 복창
        r'절대.*?금지',        # 금지 문구 복창
        r'반드시.*?해야',      # 지시 문구 복창
        r'Available tools.*?choose from',  # 도구 목록 설명 복창
        r'사용 가능한 도구는.*?목록에만',   # 한글 도구 설명 복창
    ]
    
    filtered = text
    for pattern in prompt_echo_patterns:
        filtered = re.sub(pattern, '', filtered, flags=re.IGNORECASE | re.DOTALL)
    
    # 연속된 공백 정리
    filtered = re.sub(r'\s+', ' ', filtered).strip()
    
    return filtered


def parse_action(llm_response: str) -> Optional[AgentAction]:
    """
    [Robust Parser with Output Filtering]
    LLM 응답에서 "첫 번째 JSON 오브젝트"만 안전하게 파싱합니다.

    - 프롬프트 복창 텍스트를 먼저 필터링합니다.
    - LLM이 JSON 앞뒤로 잡담/설명을 섞거나,
      JSON을 2개 이상 출력하는 경우에도 첫 JSON만 추출해 파싱합니다.
    - json.JSONDecoder().raw_decode를 사용해 "Extra data" 문제를 회피합니다.
    """
    try:
        # [OUTPUT_FILTER] 프롬프트 복창 텍스트 제거
        filtered_response = _filter_prompt_echoing(llm_response)
        
        # 1) 가장 먼저 등장하는 '{'부터 raw_decode로 "첫 JSON"만 파싱
        start_idx = filtered_response.find("{")
        if start_idx == -1:
            return None

        # [JSON_PARSING_SAFETY] 빈 문자열이나 잘못된 형식 체크
        json_candidate = filtered_response[start_idx:].strip()
        if not json_candidate or len(json_candidate) == 0:
            logger.debug("[parse_action] Empty JSON candidate after '{'")
            return None
        
        # [JSON_PARSING_SAFETY] 최소한의 유효한 JSON 형식인지 확인 ({로 시작하고 }가 있어야 함)
        if json_candidate[0] != '{':
            logger.debug(f"[parse_action] JSON candidate doesn't start with '{{': {json_candidate[:50]}")
            return None
        
        # [JSON_PARSING_SAFETY] 빈 객체나 잘못된 형식 체크
        if json_candidate == '{' or json_candidate == '{}' or json_candidate.startswith('{\n\n') or json_candidate.startswith('{\r\n\r\n'):
            logger.debug(f"[parse_action] Invalid JSON format: {json_candidate[:50]}")
            return None

        decoder = json.JSONDecoder()
        data, _end = decoder.raw_decode(json_candidate)

        # 5. 데이터 검증 (tool 이름 확인)
        tool_name = data.get("tool")
        if not isinstance(tool_name, str) or not tool_name:
            return None

        args = data.get("args", {})
        if not isinstance(args, dict):
            args = {}
        return AgentAction(tool=tool_name, args=args)

    except json.JSONDecodeError as e:
        # [JSON_RECOVERY] 불완전한 JSON 복구 시도
        # 예: {"tool":"finish" (닫는 } 누락), {"tool":"finish" (args 누락)
        # [JSON_PARSING_SAFETY] JSONDecodeError를 로깅하되 예외를 전파하지 않음
        logger.debug(f"[parse_action] JSONDecodeError occurred: {e} | Response preview: {llm_response[:200]}")
        
        try:
            filtered_response = _filter_prompt_echoing(llm_response)
            start_idx = filtered_response.find("{")
            if start_idx == -1:
                logger.debug("[parse_action] No '{' found in filtered response")
                return None
            
            # 불완전한 JSON 추출 (첫 번째 {부터 끝까지)
            incomplete_json = filtered_response[start_idx:].strip()
            
            # [JSON_PARSING_SAFETY] 빈 문자열 체크
            if not incomplete_json or len(incomplete_json) == 0:
                logger.debug("[parse_action] Empty incomplete_json after extraction")
                return None
            
            # 복구 시도 1: tool만 있고 args가 없는 경우 (예: {"tool":"finish")
            if '"tool"' in incomplete_json and '"args"' not in incomplete_json:
                tool_match = re.search(r'"tool"\s*:\s*"([^"]+)"', incomplete_json)
                if tool_match:
                    tool_name = tool_match.group(1)
                    # 불완전한 JSON을 완전한 형태로 복구
                    cleaned = incomplete_json.rstrip().rstrip(",").rstrip()
                    # 닫는 } 제거 (있다면)
                    cleaned = cleaned.rstrip("}")
                    # args 추가
                    if cleaned.endswith('"'):
                        # {"tool":"finish" -> {"tool":"finish", "args": {}}
                        recovered = cleaned + ', "args": {}}'
                    else:
                        # {"tool":"finish -> {"tool":"finish", "args": {}}
                        recovered = cleaned + '" , "args": {}}'
                    try:
                        data = json.loads(recovered)
                        tool_name = data.get("tool")
                        if tool_name:
                            args = data.get("args", {})
                            if not isinstance(args, dict):
                                args = {}
                            logging.info(f"[JSON_RECOVERY] Recovered incomplete JSON (missing args): {recovered[:100]}")
                            return AgentAction(tool=str(tool_name), args=args)
                    except json.JSONDecodeError:
                        pass
            
            # 복구 시도 2: 닫는 }만 누락된 경우 (예: {"tool":"finish","args":{})
            if incomplete_json.count("{") > incomplete_json.count("}"):
                recovered = incomplete_json.rstrip().rstrip(",").rstrip()
                if not recovered.endswith("}"):
                    recovered += "}"
                try:
                    data = json.loads(recovered)
                    tool_name = data.get("tool")
                    if tool_name:
                        args = data.get("args", {})
                        if not isinstance(args, dict):
                            args = {}
                        logging.info(f"[JSON_RECOVERY] Recovered incomplete JSON (missing closing brace): {recovered[:100]}")
                        return AgentAction(tool=str(tool_name), args=args)
                except json.JSONDecodeError:
                    pass
            
            # 복구 시도 3: 파이썬 dict 스타일로 파싱
            import ast
            s = llm_response.find("{")
            t = llm_response.rfind("}")
            if s != -1 and t != -1 and t > s:
                candidate = llm_response[s : t + 1]
                obj = ast.literal_eval(candidate)
                if isinstance(obj, dict):
                    tool_name = obj.get("tool")
                    if tool_name:
                        args = obj.get("args", {})
                        if not isinstance(args, dict):
                            args = {}
                        return AgentAction(tool=str(tool_name), args=args)
        except Exception as recovery_error:
            logger.debug(f"[JSON_RECOVERY] Recovery attempt failed: {recovery_error}")

        # JSON 문법이 깨진 경우 (로그 남기고 None 반환, 예외 전파하지 않음)
        logger.warning(f"[parse_action] JSON Decode Error (non-fatal): {e} | Raw preview: {llm_response[:200]}")
        return None
    except Exception as e:
        # [JSON_PARSING_SAFETY] 모든 예외를 잡아서 None 반환 (예외 전파 방지)
        logger.error(f"[parse_action] Parse Action Error (non-fatal): {e} | Response preview: {llm_response[:200]}", exc_info=True)
        return None


# Observation이 실질 정보를 담았는지 검사 (에러만/플레이스홀더만이면 False)
_EMPTY_OR_PLACEHOLDER = frozenset({
    "", "실패", "fail", "failed", "error", "none", "n/a", "null", "no output",
    "no result", "empty", "timeout", "오류", "실패함", "없음",
})
_MIN_SUBSTANTIVE_LEN = 3  # 최소 문자 수 (실질 내용)
_STRUCTURAL_FAIL_STATUS = frozenset({"error", "failed", "failure", "timeout", "blocked"})
_STRUCTURAL_OK_STATUS = frozenset({"ok", "success", "completed"})


def _is_substantive_observation(observation: Optional[Union[str, Dict[str, Any]]]) -> bool:
    """
    도구 실행 결과가 실질적인 정보를 담았는지 검사.
    - 비어있음, 에러 마커만, 플레이스홀더만 → False.
    - finish step은 _has_valid_tool_execution에서 제외됨; 실패한 도구 호출은 카운트 안 함.
    - str: 기존 문자열 규칙.
    - dict: status in ok/success, 또는 row_count > 0, 또는 비어있지 않은 구조적 payload면 유효.
      placeholder/empty dict/실패 status면 False.
    """
    if observation is None:
        return False
    if isinstance(observation, dict):
        ob = observation
        if not ob:
            return False
        status = (ob.get("status") or ob.get("state") or "").strip().lower()
        if status in _STRUCTURAL_FAIL_STATUS:
            return False
        if status in _STRUCTURAL_OK_STATUS:
            return True
        try:
            rc = ob.get("row_count")
            if rc is not None and (isinstance(rc, (int, float)) and int(rc) > 0):
                return True
        except (TypeError, ValueError):
            pass
        if isinstance(ob.get("data"), (list, dict)) and len(ob["data"]) > 0:
            return True
        if ob.get("result") is not None and ob.get("result") != "" and ob.get("result") != []:
            return True
        if ob.get("placeholder") is True or ob.get("pending") is True:
            return False
        if set(ob.keys()) <= {"status", "message"} and (ob.get("message") or "").strip() == "":
            return False
        return bool(ob)
    raw = (observation or "").strip()
    if len(raw) < _MIN_SUBSTANTIVE_LEN:
        return False
    if raw.startswith("[Error]") or raw.startswith("[ERROR]") or raw.startswith("[차단]"):
        return False
    if raw.startswith("[finish 거부]") or "[finish 거부:" in raw:
        return False
    if "[ERROR]" in raw.upper() or raw.startswith("[SECURITY ALERT]"):
        return False
    if raw.startswith("[Recovery:") and len(raw) < 80:
        return False
    low = raw.lower()
    if low in _EMPTY_OR_PLACEHOLDER:
        return False
    if low in ("[종료]", "[exit]"):
        return False
    return True


def _has_valid_tool_execution(steps: List[AgentStep]) -> bool:
    """
    최소 1회 이상의 유효한 도구 호출(Action)과 실질적인 Observation이 기록되었는지 확인.
    - finish가 아닌 도구 호출 + observation이 비어있지 않고, 에러/플레이스홀더만이 아니어야 True.
    - 도구는 호출됐지만 실패(observation이 [Error] 등)이거나 내용이 없으면 False.
    """
    for step in steps:
        if step.action is None or step.action.tool == "finish":
            continue
        if _is_substantive_observation(step.observation):
            return True
    return False


def _normalize_observation_excerpt(raw_observation: str, max_chars: int = 260) -> str:
    """
    Observation 원문을 한 줄 요약으로 정규화한다.
    - 과거 실패 패턴 자동 주입 블록은 제거
    - 경로 자동 교정 안내는 제거
    - 개행은 '/'로 압축
    """
    text = (raw_observation or "").strip()
    if not text:
        return ""

    if "[과거 실패 패턴 분석]" in text:
        text = text.split("[과거 실패 패턴 분석]", 1)[0].strip()
    text = text.replace("\r\n", "\n")

    filtered_lines: List[str] = []
    for line in text.splitlines():
        clean = line.strip()
        if not clean:
            continue
        if clean.startswith("[경로 자동 교정]"):
            continue
        filtered_lines.append(clean)
        if len(filtered_lines) >= 4:
            break

    compact = " / ".join(filtered_lines) if filtered_lines else re.sub(r"\s+", " ", text)
    compact = re.sub(r"\s+", " ", compact).strip()
    if len(compact) > max_chars:
        return compact[:max_chars] + "..."
    return compact


def _collect_core_observations(steps: List[AgentStep]) -> List[Dict[str, Any]]:
    """
    finish summary에 반드시 반영할 핵심 Observation 목록을 생성한다.
    핵심 기준:
    - finish가 아닌 도구의 non-empty Observation
    - placeholder/종료 메시지는 제외
    - 에러 Observation도 누락 방지를 위해 포함
    """
    records: List[Dict[str, Any]] = []
    for step in steps:
        if step.action is None or step.action.tool == "finish":
            continue

        raw = (step.observation or "").strip()
        if not raw:
            continue
        if raw in ("[종료]", "[finish 거부: 도구 미실행]"):
            continue

        excerpt = _normalize_observation_excerpt(raw)
        if not excerpt:
            continue

        upper_raw = raw.upper()
        is_error = (
            raw.startswith("[Error]")
            or "[ERROR]" in upper_raw
            or raw.startswith("[차단]")
            or raw.startswith("[SECURITY ALERT]")
        )
        records.append(
            {
                "turn": step.turn,
                "tool": step.action.tool,
                "excerpt": excerpt,
                "status_tag": "⚠️ possible" if is_error else "✅ verified",
            }
        )
    return records


def _build_six_step_report(
    *,
    user_input: str,
    model_summary: str,
    steps: List[AgentStep],
    finish_reason: str,
) -> str:
    """
    최종 출력을 6단계 보고 형식으로 강제한다.
    Observation이 있는 경우, [3단계]와 [4단계]에 반드시 반영한다.
    """
    core_obs = _collect_core_observations(steps)
    executed_tools: List[str] = []
    seen = set()
    for step in steps:
        if step.action is None or step.action.tool == "finish":
            continue
        tool_name = step.action.tool
        if tool_name not in seen:
            seen.add(tool_name)
            executed_tools.append(tool_name)

    action_lines = [
        f"- ✅ verified 총 {len(core_obs)}건 Observation 수집, 도구 {len(executed_tools)}종 사용",
    ]
    if executed_tools:
        action_lines.append("- ✅ verified 사용 도구: " + ", ".join(executed_tools))
    else:
        action_lines.append("- ⚠️ possible 유효 도구 실행 이력이 없어 Observation 기반 검증 범위가 제한됨")

    observation_lines: List[str] = []
    verification_lines: List[str] = []
    warning_count = 0
    for rec in core_obs:
        observation_lines.append(
            f"- Turn {rec['turn']} | `{rec['tool']}`: {rec['excerpt']}"
        )
        verification_lines.append(
            f"- {rec['status_tag']} Turn {rec['turn']} `{rec['tool']}` 결과 확인"
        )
        if rec["status_tag"] != "✅ verified":
            warning_count += 1

    if not observation_lines:
        observation_lines.append("- ⚠️ possible 관측된 Observation이 없어 요약 기반 보고만 제공")
    if not verification_lines:
        verification_lines.append("- ⚠️ possible 검증 가능한 도구 관측값이 부족함")

    risk_lines = []
    if warning_count > 0:
        risk_lines.append(
            f"- ⚠️ possible 오류/차단 신호 {warning_count}건 존재, 재시도 또는 수동 점검 필요"
        )
    else:
        risk_lines.append("- ✅ verified 수집된 Observation 기준 중대한 오류 신호 없음")
    if finish_reason == "max_turns":
        risk_lines.append("- ⚠️ possible 최대 턴 도달로 후속 작업이 남아 있을 수 있음")

    final_summary = (model_summary or "").strip() or "요청 작업을 완료했습니다."
    report_lines = [
        "[1단계] 요청 해석",
        f"- ✅ verified 사용자 요청: {user_input[:400]}",
        "",
        "[2단계] 실행 액션",
        *action_lines,
        "",
        "[3단계] 핵심 Observation",
        *observation_lines,
        "",
        "[4단계] 검증 상태",
        *verification_lines,
        "",
        "[5단계] 리스크/한계",
        *risk_lines,
        "",
        "[6단계] 최종 답변",
        f"- ✅ verified 결론: {final_summary}",
        "",
        "[APPROVAL_REQUIRED]",
        "- 무엇을 고쳤는가: Observation 기반으로 실행 사실/결과를 6단계 형식으로 구조화해 보고함.",
        "- 어떤 리스크가 사라졌는가: 도구 실행 결과 누락 및 추상 요약으로 인한 승인 불가능 리스크를 감소시킴.",
    ]
    return "\n".join(report_lines)


def _should_enforce_structured_report(steps: List[AgentStep]) -> bool:
    """
    일반 대화와 작업형 보고를 분리하기 위한 게이트.
    Observation이 실제로 수집된 경우에만 6단계 보고 형식을 강제한다.
    """
    return len(_collect_core_observations(steps)) > 0


def _extract_limitations(
    steps: List[AgentStep],
    finish_args: Optional[Dict[str, Any]] = None,
) -> List[str]:
    """
    ✅ Phase 5: 행동 후 한계 자동 추출.

    ReAct 루프의 steps를 분석하여 시스템이 명시해야 할 한계를 추출한다.
    finish 인자에 사용자가 직접 명시한 limitations도 포함.

    한계 감지 기준:
      1. 도구 실행 오류가 있었으나 해결되지 않은 경우
      2. 도구 없이 결론을 내린 경우 (검증 미비)
      3. 복구/재시도 후 성공했으나 원래 경로가 실패한 경우
      4. finish_args에 명시적 limitations가 있는 경우
    """
    limitations: List[str] = []

    # 1. finish_args에 명시적으로 포함된 한계
    if finish_args and isinstance(finish_args, dict):
        explicit = finish_args.get("limitations", "")
        if explicit and isinstance(explicit, str) and explicit.strip():
            limitations.append(explicit.strip())

    # 2. 도구 실행 오류 분석
    error_tools: List[str] = []
    successful_tools: List[str] = []
    for step in steps:
        if step.action and step.action.tool and step.action.tool not in ("finish", "self_correction"):
            if step.observation.startswith("[Error]"):
                error_tools.append(step.action.tool)
            elif step.observation and step.observation != "[종료]":
                successful_tools.append(step.action.tool)

    if error_tools:
        unresolved = [t for t in error_tools if t not in successful_tools]
        if unresolved:
            tools_str = ", ".join(set(unresolved))
            limitations.append(
                f"도구 실행 오류 미해결: [{tools_str}] — 해당 정보는 미검증"
            )

    # 3. 도구 근거 없이 결론 도출한 경우
    if not successful_tools and steps:
        limitations.append(
            "도구 근거 없음: 모든 결론이 LLM 추론만으로 도출됨 (검증 미비)"
        )

    # 4. 턴 수 기반 효율 경고
    if len(steps) > 10:
        limitations.append(
            f"높은 턴 수({len(steps)}턴): 작업 복잡도 대비 추론 비용이 높을 수 있음"
        )

    return limitations


def _extract_success_pattern(
    steps: List[AgentStep],
    user_input: str,
) -> Optional[str]:
    """
    Phase 6: 성공 패턴 추출 (Positive Reinforcement).

    finish_tool로 정상 종료된 경우, 사용된 도구 시퀀스에서 성공 패턴을 추출한다.
    실패만 기록하던 기존 방식에 "뭘 잘했는지"를 추가하여 에이전트가
    다음 실행에서 성공 패턴을 재사용하도록 유도한다.

    Returns:
        성공 패턴 문자열 (없으면 None)
    """
    if not steps:
        return None

    # 성공적으로 실행된 도구 시퀀스 추출
    tool_sequence: List[str] = []
    error_count = 0
    for step in steps:
        if step.action and step.action.tool and step.action.tool not in ("finish", "self_correction"):
            if step.observation.startswith("[Error]"):
                error_count += 1
            else:
                tool_sequence.append(step.action.tool)

    if not tool_sequence:
        return None

    # 도구 시퀀스를 간결한 패턴으로 변환 (연속 중복 제거)
    compressed: List[str] = []
    for t in tool_sequence:
        if not compressed or compressed[-1] != t:
            compressed.append(t)

    # 패턴 문자열 생성
    pattern_str = " → ".join(compressed) + " → finish"

    # 효율성 판단 (에러 0이고 턴 수가 적으면 고효율)
    efficiency = "고효율" if (error_count == 0 and len(steps) <= 5) else "표준"

    # 사용자 의도 요약 (첫 50자)
    intent_summary = user_input[:50].strip()
    if len(user_input) > 50:
        intent_summary += "..."

    return (
        f"[{efficiency}] \"{intent_summary}\" 작업에서 "
        f"{pattern_str} 패턴이 성공. "
        f"(도구 {len(tool_sequence)}회 사용, 오류 {error_count}회)"
    )


def _save_success_insight(pattern: str) -> None:
    """
    성공 패턴을 BehaviorInsight로 DB에 저장.
    실패해도 메인 플로우에 영향 없음 (fire-and-forget).
    """
    try:
        from mellow_link.infra.memory_database import get_memory_db, BehaviorInsight
        import uuid as _uuid

        db = get_memory_db()
        insight = BehaviorInsight(
            id=str(_uuid.uuid4()),
            pattern_type="success_pattern",
            finding=pattern,
            recommendation=f"이 패턴을 유사한 작업에서 재사용하세요: {pattern}",
            confidence=0.75,
            is_applied=0,
            created_at=datetime.now(),
        )
        db.save_insight(insight)
        logger.debug("[AgentBrain] Success pattern saved: %s", pattern[:100])
    except Exception as e:
        logger.debug("[AgentBrain] Failed to save success pattern: %s", e)


def validate_response_requires_action(
    llm_response: str,
    action: Optional[AgentAction],
) -> Optional[str]:
    """
    [Validator] Action 필드가 비어있는데 Thought만 길게 뱉은 경우 할루시네이션으로 간주하고
    재추론을 요청할 메시지를 반환. 그 외에는 None (정상).
    """
    if action is not None:
        return None
    text = (llm_response or "").strip()
    if len(text) < REASONING_ONLY_HALLUCINATION_THRESHOLD:
        return None
    return (
        "오류: 긴 설명만 출력하고 실제 도구 호출(Action) JSON을 출력하지 않았습니다. "
        "Thought에서 '분석했다/확인했다'라고 말하지 말고, 반드시 단 하나의 JSON만 출력하세요. "
        "예: {\"tool\":\"read_file\",\"args\":{\"path\":\"...\"}} "
        "도구 실행 후 [Observation] 결과를 받은 뒤에만 결론을 내리세요."
    )
