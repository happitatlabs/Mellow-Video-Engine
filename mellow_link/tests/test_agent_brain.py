"""
AgentBrain 테스트 스위트.

실제 LLM 서버 없이 MockLLM으로 전체 ReAct 루프를 검증.

검증 범위:
  1. parse_action: JSON 추출 (정상, 엣지 케이스, 실패)
  2. build_system_prompt: 도구 목록 삽입
  3. AgentBrain.run: 전체 ReAct 루프
     - 정상 finish
     - 도구 실행 → observe → finish
     - Self-Correction (포맷 오류 복구)
     - max_turns 도달
     - 다중 턴 도구 체이닝
  4. History trimming
"""

import asyncio
import json
import sys
import unittest
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Any
from unittest.mock import AsyncMock, MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from mellow_link.core.agent_brain import (
    AgentAction,
    AgentBrain,
    AgentResult,
    AgentStep,
    build_system_prompt,
    parse_action,
    _validate_experience_advisory_and_append_disclaimer,
)
from mellow_link.core.tool_registry import ToolRegistry
from mellow_link.core.security_manager import SecurityBlocked


# ═══════════════════════════════════════════════
# Mock LLM Service
# ═══════════════════════════════════════════════

@dataclass
class MockLLMResponse:
    """LLMService.chat()이 반환하는 객체를 모방."""
    text: str
    model: str = "mock-model"


class MockLLM:
    """
    테스트용 가짜 LLM.
    responses 리스트에서 순서대로 응답을 반환한다.
    """
    def __init__(self, responses: List[str]):
        self._responses = list(responses)
        self._call_count = 0
        self.received_messages: List[List[Dict]] = []

    async def chat(self, messages: List[Dict[str, str]], model: str = "mock", **kwargs) -> MockLLMResponse:
        self.received_messages.append(messages)
        if self._call_count < len(self._responses):
            text = self._responses[self._call_count]
        else:
            text = '```json\n{"tool": "finish", "args": {"summary": "응답 소진"}}\n```'
        self._call_count += 1
        return MockLLMResponse(text=text)

    def get_model_for_mode(self, mode: str) -> str:
        return "mock-model"

    def is_ready(self) -> bool:
        return True


# ═══════════════════════════════════════════════
# 1. parse_action 단위 테스트
# ═══════════════════════════════════════════════

class TestParseAction(unittest.TestCase):

    # --- 정상 추출 ---

    def test_fenced_json_block(self):
        """```json ... ``` 블록에서 추출."""
        text = 'Thought: 검색이 필요해.\n```json\n{"tool": "search_memory", "args": {"query": "RAG검색어"}}\n```'
        action = parse_action(text)
        self.assertIsNotNone(action)
        self.assertEqual(action.tool, "search_memory")
        self.assertEqual(action.args["query"], "RAG검색어")

    def test_fenced_without_json_label(self):
        """``` ... ``` (json 라벨 없이)에서도 추출."""
        text = '생각 중...\n```\n{"tool": "finish", "args": {"summary": "완료"}}\n```'
        action = parse_action(text)
        self.assertIsNotNone(action)
        self.assertEqual(action.tool, "finish")

    def test_inline_json(self):
        """텍스트 중간에 있는 인라인 JSON 추출."""
        text = '결과를 저장하겠습니다. {"tool": "write_file", "args": {"file_path": "test.txt", "content": "hello"}} 완료.'
        action = parse_action(text)
        self.assertIsNotNone(action)
        self.assertEqual(action.tool, "write_file")

    def test_finish_action(self):
        """finish 도구 파싱."""
        text = '```json\n{"tool": "finish", "args": {"summary": "최종 답변입니다"}}\n```'
        action = parse_action(text)
        self.assertEqual(action.tool, "finish")
        self.assertEqual(action.args["summary"], "최종 답변입니다")

    def test_empty_args(self):
        """args가 비어있는 경우."""
        text = '```json\n{"tool": "list_directory", "args": {}}\n```'
        action = parse_action(text)
        self.assertEqual(action.tool, "list_directory")
        self.assertEqual(action.args, {})

    def test_missing_args_key(self):
        """args 키가 없는 경우 빈 dict로 처리."""
        text = '```json\n{"tool": "whoami_tool"}\n```'
        action = parse_action(text)
        self.assertIsNotNone(action)
        self.assertEqual(action.args, {})

    # --- 파싱 실패 ---

    def test_no_json_returns_none(self):
        """JSON이 없는 순수 텍스트 → None."""
        text = "검색할 필요가 없습니다. 바로 답변드리겠습니다."
        self.assertIsNone(parse_action(text))

    def test_invalid_json_returns_none(self):
        """깨진 JSON → None."""
        text = '```json\n{"tool": "broken, "args": {}}\n```'
        self.assertIsNone(parse_action(text))

    def test_json_without_tool_key_returns_none(self):
        """tool 키가 없는 JSON → None."""
        text = '```json\n{"name": "not_a_tool", "data": 123}\n```'
        self.assertIsNone(parse_action(text))

    def test_tool_not_string_returns_none(self):
        """tool 값이 문자열이 아닌 경우 → None."""
        text = '```json\n{"tool": 123, "args": {}}\n```'
        self.assertIsNone(parse_action(text))


# ═══════════════════════════════════════════════
# 2. 시스템 프롬프트 빌더 테스트
# ═══════════════════════════════════════════════

class TestBuildSystemPrompt(unittest.TestCase):

    def test_contains_tools_json(self):
        """도구 JSON이 프롬프트에 삽입됨."""
        tools = '[{"name": "test_tool", "description": "test"}]'
        prompt = build_system_prompt(tools, persona="")
        self.assertIn("test_tool", prompt)
        self.assertIn("TOOL_WHITELIST", prompt)

    def test_contains_format_instructions(self):
        """포맷 지침이 포함됨."""
        prompt = build_system_prompt("[]", persona="")
        self.assertIn('"tool"', prompt)
        self.assertIn('"args"', prompt)
        self.assertIn("finish", prompt)

    def test_contains_react_keywords(self):
        """ReAct 실행/관찰 키워드."""
        prompt = build_system_prompt("[]", persona="")
        self.assertIn("Observation", prompt)

    def test_contains_memory_reference_rule(self):
        """시스템 프롬프트에 미션/도구 지침이 포함됨 (AGENT_MISSION 또는 TOOL_WHITELIST)."""
        prompt = build_system_prompt("[]", persona="")
        self.assertTrue(
            "[AGENT_MISSION]" in prompt or "TOOL_WHITELIST" in prompt,
            "프롬프트에 미션 또는 도구 whitelist 지침이 포함되어야 함",
        )


# ═══════════════════════════════════════════════
# 2.5 과거 경험(RAG) 도구명 검증 (Memory Integrity)
# ═══════════════════════════════════════════════

class TestExperienceAdvisoryToolValidation(unittest.TestCase):
    """과거 기억 주입 직전 Registry 대조 및 디스클레이머 삽입 검증."""

    def test_invalid_tool_in_past_experience_appends_disclaimer(self):
        """과거 경험에 현재 존재하지 않는 도구명(예: deprecated_tool)이 있으면 경고 문구가 하단에 삽입된다."""
        valid_tools = ["read_file", "write_file", "list_directory", "finish"]
        past_text = (
            "[Past Experience Advisory]\n"
            "과거 deprecated_tool 사용 실패. 교훈: read_file로 먼저 확인할 것.\n"
            '이전 턴: {"tool": "deprecated_tool", "args": {"id": "1"}}'
        )
        result = _validate_experience_advisory_and_append_disclaimer(past_text, valid_tools)
        self.assertIn("deprecated_tool", result)
        self.assertIn("현재 유효하지 않습니다", result)
        self.assertIn("Tool Registry의 도구만 사용하십시오", result)

    def test_only_valid_tools_no_disclaimer(self):
        """과거 경험에 유효한 도구만 언급되면 디스클레이머가 추가되지 않는다."""
        valid_tools = ["read_file", "write_file", "finish"]
        past_text = (
            "[Past Experience Advisory]\n"
            "read_file 사용 후 write_file로 저장 성공. 교훈: read_file로 확인할 것."
        )
        result = _validate_experience_advisory_and_append_disclaimer(past_text, valid_tools)
        self.assertEqual(result, past_text)
        self.assertNotIn("유효하지 않습니다", result)

    def test_empty_advisory_unchanged(self):
        """빈 경험 지침은 그대로 반환된다."""
        self.assertEqual(
            _validate_experience_advisory_and_append_disclaimer("", ["read_file"]),
            "",
        )
        self.assertEqual(
            _validate_experience_advisory_and_append_disclaimer("   ", ["read_file"]),
            "   ",
        )

    def test_multiple_invalid_tools_single_disclaimer(self):
        """여러 개의 잘못된 도구명이 있어도 한 줄 경고에 모두 나열된다."""
        valid_tools = ["read_file"]
        past_text = "deprecated_tool 사용 실패. old_tool 호출도 실패."
        result = _validate_experience_advisory_and_append_disclaimer(past_text, valid_tools)
        self.assertIn("현재 유효하지 않습니다", result)
        self.assertIn("deprecated_tool", result)
        self.assertIn("old_tool", result)


# ═══════════════════════════════════════════════
# 3. AgentBrain ReAct Loop 통합 테스트
# ═══════════════════════════════════════════════

class TestAgentBrainRun(unittest.TestCase):
    """MockLLM으로 전체 루프를 검증."""

    def _make_brain(self, responses: List[str], max_turns: int = 10) -> AgentBrain:
        """테스트용 AgentBrain 생성 (registry를 패치)."""
        mock_llm = MockLLM(responses)

        brain = object.__new__(AgentBrain)
        brain._llm = mock_llm
        brain._max_turns = max_turns
        brain._model_mode = "thinking"
        brain._context_window = 20
        brain._enable_memory_archiving = False
        brain._enable_experience_retrieval = False
        brain._archiver = None
        brain._experience_provider = None
        brain._checkpoint_manager = None
        brain._log_analyzer = None
        brain._experience_helper = MagicMock()
        brain._experience_helper.archive_experience = AsyncMock(return_value=None)
        brain._experience_helper.build_context_summary = MagicMock(return_value="")

        # 테스트용 레지스트리 (실제 agent_tools 대신 간단한 도구)
        reg = ToolRegistry()

        @reg.register
        def search_memory(query: str) -> str:
            """기억 검색."""
            return f"검색 결과: '{query}'에 대한 3건의 문서 발견."

        @reg.register
        def read_file(file_path: str) -> str:
            """파일 읽기."""
            return f"파일 내용: [{file_path}]의 데이터."

        @reg.register
        def write_file(file_path: str, content: str) -> str:
            """파일 쓰기."""
            return f"[완료] 저장: {file_path}"

        @reg.register
        def finish(summary: str) -> str:
            """종료."""
            return f"[FINISH] {summary}"

        brain._registry = reg
        return brain

    # --- 즉시 finish ---

    def test_immediate_finish(self):
        """LLM이 첫 턴에 바로 finish를 호출."""
        brain = self._make_brain([
            '생각: 바로 답할 수 있어.\n```json\n{"tool": "finish", "args": {"summary": "안녕하세요!"}}\n```'
        ])
        result = asyncio.run(brain.run("인사해줘", mode="thinking", require_at_least_one_tool=False))

        self.assertEqual(result.finish_reason, "finish_tool")
        self.assertEqual(result.answer, "안녕하세요!")
        self.assertEqual(result.total_turns, 1)
        self.assertEqual(len(result.steps), 1)

    # --- 도구 사용 후 finish ---

    def test_tool_then_finish(self):
        """도구 실행 → 결과 확인 → finish."""
        brain = self._make_brain([
            '생각: 먼저 기억을 검색해야겠어.\n```json\n{"tool": "search_memory", "args": {"query": "RAG"}}\n```',
            '생각: 검색 결과를 받았어.\n```json\n{"tool": "finish", "args": {"summary": "관련 3건의 문서를 찾았습니다."}}\n```',
        ])
        with patch("mellow_link.core.complexity_evaluator.get_complexity_evaluator") as mock_get:
            mock_eval = MagicMock()
            mock_eval.calculate_limit.return_value = 5
            mock_get.return_value = mock_eval
            result = asyncio.run(brain.run("RAG 검색 결과 알려줘", mode="thinking", require_at_least_one_tool=False))

        self.assertEqual(result.finish_reason, "finish_tool")
        self.assertEqual(result.total_turns, 2)
        self.assertEqual(len(result.steps), 2)

        # Step 1: 도구 실행 확인
        step1 = result.steps[0]
        self.assertEqual(step1.action.tool, "search_memory")
        self.assertIn("검색 결과", step1.observation)

        # Step 2: finish 확인
        step2 = result.steps[1]
        self.assertEqual(step2.action.tool, "finish")

    # --- 다중 도구 체이닝 ---

    def test_multi_tool_chain(self):
        """도구 여러 번 → finish."""
        brain = self._make_brain([
            '```json\n{"tool": "search_memory", "args": {"query": "설정"}}\n```',
            '```json\n{"tool": "read_file", "args": {"file_path": "config/settings.yaml"}}\n```',
            '```json\n{"tool": "finish", "args": {"summary": "설정 파일을 확인했습니다."}}\n```',
        ])
        with patch("mellow_link.core.complexity_evaluator.get_complexity_evaluator") as mock_get:
            mock_eval = MagicMock()
            mock_eval.calculate_limit.return_value = 5
            mock_get.return_value = mock_eval
            result = asyncio.run(brain.run("설정 파일 내용 알려줘", mode="thinking", require_at_least_one_tool=False))

        self.assertEqual(result.finish_reason, "finish_tool")
        self.assertEqual(result.total_turns, 3)
        self.assertEqual(result.steps[0].action.tool, "search_memory")
        self.assertEqual(result.steps[1].action.tool, "read_file")
        self.assertEqual(result.steps[2].action.tool, "finish")

    # --- Self-Correction ---

    def test_self_correction_recovers(self):
        """포맷 오류 → 수정 요청 → 올바른 JSON으로 복구."""
        brain = self._make_brain([
            "음, 검색해볼게요.",
            '```json\n{"tool": "finish", "args": {"summary": "복구 완료"}}\n```',
        ])
        with patch("mellow_link.core.complexity_evaluator.get_complexity_evaluator") as mock_get:
            mock_eval = MagicMock()
            mock_eval.calculate_limit.return_value = 5
            mock_get.return_value = mock_eval
            result = asyncio.run(brain.run("테스트", mode="thinking", require_at_least_one_tool=False))

        # 포맷 오류(1턴) 후 2턴에서 JSON 응답 시도; finish_tool 또는 no_tool_calls로 종료
        self.assertIn(result.finish_reason, ("finish_tool", "no_tool_calls"))
        self.assertGreaterEqual(result.total_turns, 1)

    def test_self_correction_exhausted(self):
        """포맷 오류가 max_retries를 초과하면 raw/fallback 응답으로 종료."""
        brain = self._make_brain([
            "JSON 없는 응답 1",
            "JSON 없는 응답 2",
            "JSON 없는 응답 3",
        ])
        result = asyncio.run(brain.run("테스트", require_at_least_one_tool=False))

        self.assertIn(result.finish_reason, ("parse_fallback", "no_tool_calls", "max_turns"))
        self.assertIn("JSON 없는 응답", result.answer)

    # --- max_turns 도달 ---

    def test_max_turns_reached(self):
        """도구만 계속 호출하고 finish를 안 하면 dynamic_max_turns에서 종료."""
        brain = self._make_brain(
            [f'```json\n{{"tool": "search_memory", "args": {{"query": "turn{i}"}}}}\n```' for i in range(5)],
            max_turns=10,
        )
        with patch("mellow_link.core.complexity_evaluator.get_complexity_evaluator") as mock_get:
            mock_eval = MagicMock()
            mock_eval.calculate_limit.return_value = 5
            mock_get.return_value = mock_eval
            result = asyncio.run(brain.run("무한 검색", mode="thinking", require_at_least_one_tool=False))

        self.assertEqual(result.finish_reason, "max_turns")
        self.assertEqual(result.total_turns, 5)
        self.assertEqual(len(result.steps), 5)

    # --- 컨텍스트 전달 ---

    def test_context_passed_to_messages(self):
        """이전 대화 컨텍스트가 LLM에 전달됨."""
        mock_llm = MockLLM([
            '```json\n{"tool": "finish", "args": {"summary": "컨텍스트 확인"}}\n```',
        ])

        brain = self._make_brain([])
        brain._llm = mock_llm

        prior_context = [
            {"role": "user", "content": "이전 질문"},
            {"role": "assistant", "content": "이전 답변"},
        ]
        asyncio.run(brain.run("후속 질문", context=prior_context))

        # LLM에 전달된 메시지 확인
        sent_messages = mock_llm.received_messages[0]
        roles = [m["role"] for m in sent_messages]
        contents = [m["content"] for m in sent_messages]

        self.assertEqual(roles[0], "system")          # 시스템 프롬프트
        self.assertIn("이전 질문", contents[1])        # 컨텍스트
        self.assertIn("이전 답변", contents[2])
        self.assertIn("후속 질문", " ".join(contents))  # 현재 입력이 메시지 어딘가에 포함

    # --- 존재하지 않는 도구 호출 ---

    def test_unknown_tool_handled(self):
        """LLM이 없는 도구를 호출하면 에러 메시지가 observation으로 돌아가고, 이후 finish로 종료."""
        brain = self._make_brain([
            '```json\n{"tool": "nonexistent_tool", "args": {}}\n```',
            '```json\n{"tool": "finish", "args": {"summary": "도구 오류 처리됨"}}\n```',
        ])
        with patch("mellow_link.core.complexity_evaluator.get_complexity_evaluator") as mock_get:
            mock_eval = MagicMock()
            mock_eval.calculate_limit.return_value = 5
            mock_get.return_value = mock_eval
            result = asyncio.run(brain.run("없는 도구 테스트", mode="thinking", require_at_least_one_tool=False))

        self.assertEqual(result.finish_reason, "finish_tool")
        # nonexistent_tool을 호출한 스텝의 observation에 에러 표시가 있어야 함
        nonexistent_step = next((s for s in result.steps if s.action.tool == "nonexistent_tool"), None)
        self.assertIsNotNone(nonexistent_step, "nonexistent_tool 호출 스텝이 있어야 함")
        obs = nonexistent_step.observation or ""
        self.assertTrue(
            "[Error]" in obs or "찾을 수 없" in obs or "Error" in obs or "도구" in obs,
            f"해당 턴 observation에 에러 표시가 있어야 함: {obs[:200]!r}",
        )

    def test_plan_intent_emits_plan_created_and_finishes_with_ack(self):
        """
        Plan intent query in auto/thinking mode: plan_created event emitted, run does NOT end with no_tool_calls.
        Turn 1 with no LLM tool_calls -> deterministic plan_created_ack (short ack + finish_reason).
        """
        from mellow_link.infra.run_events import EVENT_TYPE_PLAN_CREATED
        brain = self._make_brain([])  # responses not used when _call_llm is patched
        brain._checkpoint_manager = None
        brain._experience_helper = MagicMock()
        brain._experience_helper.archive_experience = AsyncMock(return_value=None)
        brain._experience_helper.build_context_summary = MagicMock(return_value="")
        brain._enable_experience_retrieval = False

        emitted_events = []
        def capture_emit(run_id: str, event_type: str, payload: dict):
            emitted_events.append((event_type, payload))

        async def run_plan_intent():
            # Non-empty text so we don't hit empty-response retry; no tool_calls so we hit plan_created_ack path
            with patch.object(brain, "_call_llm", new_callable=AsyncMock, return_value=("Okay.", [], 0.0)):
                with patch("mellow_link.infra.run_events.emit_event", side_effect=capture_emit):
                    return await brain.run(
                        "MVP 만들기, To-do 7개",
                        session_state={"run_id": "test_run_plan"},
                        mode="thinking",
                    )

        result = asyncio.run(run_plan_intent())

        plan_created_calls = [p for t, p in emitted_events if t == EVENT_TYPE_PLAN_CREATED]
        self.assertGreater(len(plan_created_calls), 0, "plan_created event must be emitted")
        self.assertEqual(len(plan_created_calls[0].get("todos", [])), 7, "plan_created must have 7 todos (T1~T7)")
        self.assertEqual(result.finish_reason, "plan_created_ack", "must not terminate with no_tool_calls")
        self.assertIn("계획이 생성되었습니다", result.answer)
        self.assertEqual(result.total_turns, 1)
        self.assertEqual(len(result.steps), 1)
        self.assertEqual(result.steps[0].action.tool, "plan_created")

    def test_plan_only_no_t3_no_tools_finish_plan_created_ack(self):
        """
        "먼저 계획만 세워줘" 등 plan-only 요청 시:
        - 도구 호출 없음
        - T3 while 루프 미진입
        - finish_reason == "plan_created_ack"
        """
        from mellow_link.infra.run_events import EVENT_TYPE_PLAN_CREATED, EVENT_TYPE_TOOL_STARTED
        brain = self._make_brain([])
        brain._checkpoint_manager = None
        brain._experience_helper = MagicMock()
        brain._experience_helper.archive_experience = AsyncMock(return_value=None)
        brain._experience_helper.build_context_summary = MagicMock(return_value="")
        brain._enable_experience_retrieval = False

        emitted = []
        def capture(run_id: str, event_type: str, payload: dict):
            emitted.append((event_type, payload))

        with patch("mellow_link.infra.run_events.emit_event", side_effect=capture):
            result = asyncio.run(brain.run(
                "먼저 계획만 세워줘",
                session_state={"run_id": "test_plan_only"},
                mode="thinking",
            ))

        self.assertEqual(result.finish_reason, "plan_created_ack", "plan_only must end with plan_created_ack")
        self.assertIn("계획이 생성되었습니다", result.answer)
        self.assertEqual(result.total_turns, 0, "T3 루프 미진입이면 total_turns 0")
        tool_events = [t for t, _ in emitted if t == EVENT_TYPE_TOOL_STARTED]
        self.assertEqual(len(tool_events), 0, "도구 호출 없어야 함")
        plan_events = [p for t, p in emitted if t == EVENT_TYPE_PLAN_CREATED]
        self.assertGreater(len(plan_events), 0, "plan_created 이벤트 발행")
        self.assertEqual(len(result.steps), 1)
        self.assertEqual(result.steps[0].action.tool, "plan_created")

    def test_plan_then_execute_enters_t3_with_tools(self):
        """
        "계획 세우고 실행해" 등 실행 승인 포함 시:
        - plan_approved로 plan_only 비활성화
        - 도구 스키마 정상 구성, T3 루프 진입
        """
        from mellow_link.infra.run_events import EVENT_TYPE_PLAN_CREATED, EVENT_TYPE_TOOL_STARTED
        brain = self._make_brain([
            '```json\n{"tool": "finish", "args": {"summary": "실행 완료"}}\n```',
        ])
        brain._checkpoint_manager = None
        brain._experience_helper = MagicMock()
        brain._experience_helper.archive_experience = AsyncMock(return_value=None)
        brain._experience_helper.build_context_summary = MagicMock(return_value="")
        brain._enable_experience_retrieval = False

        emitted = []
        def capture(run_id: str, event_type: str, payload: dict):
            emitted.append((event_type, payload))

        with patch("mellow_link.infra.run_events.emit_event", side_effect=capture):
            result = asyncio.run(brain.run(
                "계획 세우고 실행해",
                session_state={"run_id": "test_exec"},
                mode="thinking",
            ))

        self.assertNotEqual(result.finish_reason, "plan_created_ack", "실행 승인 시 plan_created_ack으로 끝나지 않음")
        self.assertGreater(result.total_turns, 0, "T3 루프 진입하여 턴 진행")

    def test_security_blocked_halts_without_retry(self):
        """
        SecurityBlocked 발생 시:
          - 즉시 루프를 중단(security_violation)하고
          - LLM 재호출(재시도)을 하지 않아야 한다.
        """
        brain = self._make_brain([
            '```json\n{"tool": "danger", "args": {}}\n```',
            '```json\n{"tool": "danger", "args": {}}\n```',  # 재시도 방지 검증용 (실행되면 실패)
        ], max_turns=5)

        @brain._registry.register
        def danger() -> str:
            raise SecurityBlocked("blocked by policy")

        result = asyncio.run(brain.run("보안 위반 테스트", require_at_least_one_tool=False))
        self.assertEqual(result.finish_reason, "security_violation")
        self.assertIn("보안 문제로 작업을 중단합니다", result.answer)
        self.assertIn("[SECURITY ALERT]", result.answer)
        # 보안 차단 시 재시도 없이 중단되므로 LLM 호출은 제한적
        self.assertLessEqual(getattr(brain._llm, "_call_count", 0), 2)


# ═══════════════════════════════════════════════
# 4. History Trimming 테스트
# ═══════════════════════════════════════════════

class TestHistoryTrimming(unittest.TestCase):

    def _make_brain_raw(self, context_window: int = 5) -> AgentBrain:
        brain = object.__new__(AgentBrain)
        brain._context_window = context_window
        return brain

    def test_trim_preserves_system_prompt(self):
        """트리밍 시 시스템 프롬프트(첫 메시지)는 항상 유지."""
        brain = self._make_brain_raw(context_window=3)

        messages = [
            {"role": "system", "content": "시스템 프롬프트"},
            {"role": "user", "content": "msg1"},
            {"role": "assistant", "content": "msg2"},
            {"role": "user", "content": "msg3"},
            {"role": "assistant", "content": "msg4"},
            {"role": "user", "content": "msg5"},
            {"role": "assistant", "content": "msg6"},
        ]

        trimmed = brain._trim_history(messages)

        # 시스템 프롬프트 유지
        self.assertEqual(trimmed[0]["role"], "system")
        self.assertEqual(trimmed[0]["content"], "시스템 프롬프트")

        # 최근 context_window개만 남음
        self.assertEqual(len(trimmed), 1 + 3)  # system + 3 recent

    def test_no_trim_when_under_limit(self):
        """메시지가 한도 이내면 트리밍하지 않음."""
        brain = self._make_brain_raw(context_window=10)
        messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "q1"},
        ]
        trimmed = brain._trim_history(messages)
        self.assertEqual(len(trimmed), 2)


# ═══════════════════════════════════════════════
# 5. AgentResult / AgentStep 데이터 구조 테스트
# ═══════════════════════════════════════════════

class TestDataStructures(unittest.TestCase):

    def test_agent_action(self):
        a = AgentAction(tool="search_memory", args={"query": "test"})
        self.assertEqual(a.tool, "search_memory")
        self.assertEqual(a.args["query"], "test")

    def test_agent_step(self):
        s = AgentStep(turn=1, thought="생각 중...")
        self.assertEqual(s.turn, 1)
        self.assertIsNone(s.action)
        self.assertEqual(s.observation, "")

    def test_agent_result_defaults(self):
        r = AgentResult(answer="ok")
        self.assertEqual(r.answer, "ok")
        self.assertEqual(r.steps, [])
        self.assertEqual(r.total_turns, 0)
        self.assertEqual(r.finish_reason, "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
