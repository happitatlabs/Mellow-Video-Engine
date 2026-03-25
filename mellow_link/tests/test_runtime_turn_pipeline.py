"""
/runtime/turn 2단 파이프라인 테스트.

1. GM 결과 JSON 파싱 성공
2. needs_clarify=true일 때 pro 렌더 호출 안 함, clarify 반환
3. model_tier_requested=pro 일 때 character render가 pro(thinking) 선택
4. trace_id가 meta에 존재하고 에러 응답에도 존재
5. system_state enum 값(IDLE|TEXT|IMAGE|ERROR)으로 반환
"""

import asyncio
import json
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from mellow_link.runtime.schemas import TurnRequest, TurnRequestUser, TurnRequestInput, TurnRequestContext
from mellow_link.runtime.schemas import GMResult, GMClarify, GMSpeaker


# -----------------------------------------------------------------------------
# 1. GM 결과 JSON 파싱 성공
# -----------------------------------------------------------------------------
class TestGMParseSuccess(unittest.TestCase):
    def test_parse_gm_result_valid_json(self):
        from mellow_link.runtime.engine_backed_adapter import _parse_gm_result

        raw = json.dumps({
            "speaker": {"id": "aventurin", "name": "어벤츄린"},
            "intent": "SMALLTALK",
            "confidence": 0.92,
            "slots": {"topic": "인사"},
            "state_summary": "첫 인사",
            "needs_clarify": False,
        })
        result = _parse_gm_result(raw)
        self.assertIsInstance(result, GMResult)
        self.assertEqual(result.intent, "SMALLTALK")
        self.assertEqual(result.confidence, 0.92)
        self.assertFalse(result.needs_clarify)
        self.assertIsNone(result.clarify)

    def test_parse_gm_result_with_markdown_fence(self):
        from mellow_link.runtime.engine_backed_adapter import _parse_gm_result

        raw = '```json\n{"intent": "OPEN", "confidence": 1.0, "needs_clarify": false}\n```'
        result = _parse_gm_result(raw)
        self.assertEqual(result.intent, "OPEN")
        self.assertEqual(result.confidence, 1.0)


# -----------------------------------------------------------------------------
# 2. needs_clarify=true일 때 clarify 반환, Character Render 미호출
# -----------------------------------------------------------------------------
class TestNeedsClarifySkipsRender(unittest.TestCase):
    def test_turn_returns_clarify_without_calling_render_when_needs_clarify(self):
        from mellow_link.runtime.engine_backed_adapter import EngineBackedAdapter

        async def run():
            llm = MagicMock()
            orch = MagicMock()
            orch.get_service = MagicMock(return_value=llm)
            orch.get_state = MagicMock(return_value=MagicMock(name="IDLE"))
            adapter = EngineBackedAdapter(orchestrator=orch)

            # GM 단계만 호출되도록: GM이 needs_clarify=True + clarify 반환
            gm_json = json.dumps({
                "intent": "UNKNOWN",
                "confidence": 0.4,
                "needs_clarify": True,
                "clarify": {"question": "로맨스 톤? 친구 톤?", "options": ["로맨스", "친구"]},
            })
            llm.generate = AsyncMock(return_value=MagicMock(content=gm_json))

            req = TurnRequest(
                session_id="s1",
                user=TurnRequestUser(id="u1"),
                input=TurnRequestInput(text="말 걸어줘"),
            )
            resp = await adapter.turn(req, trace_id="trc_test_001")
            return resp, llm.generate

        resp, generate_mock = asyncio.run(run())
        self.assertIsNotNone(resp.turn.clarify)
        self.assertEqual(resp.turn.clarify.question, "로맨스 톤? 친구 톤?")
        # GM 1회만 호출, Character Render는 호출되지 않음 (generate 1회)
        self.assertEqual(generate_mock.call_count, 1)


# -----------------------------------------------------------------------------
# 3. model_tier_requested=pro 일 때 character render가 pro(thinking) 선택
# -----------------------------------------------------------------------------
class TestModelTierProUsesThinking(unittest.TestCase):
    def test_character_render_receives_thinking_mode_when_pro(self):
        from mellow_link.runtime.engine_backed_adapter import EngineBackedAdapter

        async def run():
            llm = MagicMock()
            orch = MagicMock()
            orch.get_service = MagicMock(return_value=llm)
            orch.get_state = MagicMock(return_value=MagicMock(name="IDLE"))
            adapter = EngineBackedAdapter(orchestrator=orch)

            gm_resp = json.dumps({"intent": "SMALLTALK", "confidence": 0.9, "needs_clarify": False})
            render_resp = json.dumps({"speech": "안녕.", "passage": None, "ooc": None})
            llm.generate = AsyncMock(side_effect=[
                MagicMock(content=gm_resp),
                MagicMock(content=render_resp),
            ])

            req = TurnRequest(
                session_id="s1",
                user=TurnRequestUser(id="u1"),
                input=TurnRequestInput(text="안녕"),
                context=TurnRequestContext(model_tier_requested="pro"),
            )
            await adapter.turn(req, trace_id="trc_pro")
            calls = llm.generate.call_args_list
            self.assertGreaterEqual(len(calls), 2)
            # 두 번째 호출(Character Render)에서 mode="thinking"
            second_kw = calls[1].kwargs
            self.assertEqual(second_kw.get("mode"), "thinking")

        asyncio.run(run())


# -----------------------------------------------------------------------------
# 4. trace_id가 meta에 존재하고 에러 응답에도 존재
# -----------------------------------------------------------------------------
class TestTraceIdPropagation(unittest.TestCase):
    def test_turn_response_meta_has_trace_id(self):
        from mellow_link.runtime.engine_backed_adapter import EngineBackedAdapter

        async def run():
            llm = MagicMock()
            orch = MagicMock()
            orch.get_service = MagicMock(return_value=llm)
            orch.get_state = MagicMock(return_value=MagicMock(name="IDLE"))
            llm.generate = AsyncMock(side_effect=[
                MagicMock(content=json.dumps({"intent": "OPEN", "confidence": 1.0, "needs_clarify": False})),
                MagicMock(content=json.dumps({"speech": "hi", "passage": None, "ooc": None})),
            ])
            adapter = EngineBackedAdapter(orchestrator=orch)
            req = TurnRequest(session_id="s1", user=TurnRequestUser(id="u1"), input=TurnRequestInput(text="hi"))
            resp = await adapter.turn(req, trace_id="trc_explicit_123")
            return resp

        resp = asyncio.run(run())
        self.assertEqual(resp.meta.trace_id, "trc_explicit_123")


# -----------------------------------------------------------------------------
# 5. system_state enum 값으로 반환
# -----------------------------------------------------------------------------
class TestSystemStateEnum(unittest.TestCase):
    def test_status_returns_system_state_enum_value(self):
        from mellow_link.runtime.engine_backed_adapter import EngineBackedAdapter

        async def run():
            orch = MagicMock()
            orch.get_state = MagicMock(return_value=MagicMock(name="IDLE"))
            orch._metrics = {}
            adapter = EngineBackedAdapter(orchestrator=orch)
            return await adapter.status()

        resp = asyncio.run(run())
        self.assertIn(resp.health.system_state, ("IDLE", "TEXT", "IMAGE", "ERROR"))


# -----------------------------------------------------------------------------
# 6. passage 항상 존재
# -----------------------------------------------------------------------------
class TestPassageAlwaysPresent(unittest.TestCase):
    def test_passage_always_present(self):
        from mellow_link.runtime.engine_backed_adapter import EngineBackedAdapter

        async def run():
            llm = MagicMock()
            orch = MagicMock()
            orch.get_service = MagicMock(return_value=llm)
            orch.get_state = MagicMock(return_value=MagicMock(name="IDLE"))
            llm.generate = AsyncMock(side_effect=[
                MagicMock(content=json.dumps({"intent": "OPEN", "confidence": 1.0, "needs_clarify": False})),
                MagicMock(content=json.dumps({
                    "speech": "안녕.",
                    "passage": "*캐릭터가 고개를 끄덕였다.*",
                    "ooc": None,
                })),
            ])
            adapter = EngineBackedAdapter(orchestrator=orch)
            req = TurnRequest(session_id="s1", user=TurnRequestUser(id="u1"), input=TurnRequestInput(text="안녕"))
            return await adapter.turn(req, trace_id="trc_passage")

        resp = asyncio.run(run())
        self.assertIsNotNone(resp.turn.passage)
        self.assertGreater(len((resp.turn.passage or "").strip()), 0)


# -----------------------------------------------------------------------------
# 7. user_action 전달 및 passage 반영
# -----------------------------------------------------------------------------
class TestUserActionPropagation(unittest.TestCase):
    def test_user_action_propagation(self):
        from mellow_link.runtime.engine_backed_adapter import EngineBackedAdapter

        async def run():
            llm = MagicMock()
            orch = MagicMock()
            orch.get_service = MagicMock(return_value=llm)
            orch.get_state = MagicMock(return_value=MagicMock(name="IDLE"))
            gm_with_action = json.dumps({
                "speaker": {"id": "aventurin", "name": "어벤츄린"},
                "intent": "OPEN",
                "confidence": 0.82,
                "slots": {"target_label": "문"},
                "user_action": "*나는 손으로 문을 가리켰다.*",
                "needs_clarify": False,
            })
            render_with_reflection = json.dumps({
                "speech": "좋아, 한번 열어보지.",
                "passage": "*사용자가 문을 가리키자 어벤츄린은 느긋하게 손잡이에 손을 올렸다.*",
                "ooc": None,
            })
            llm.generate = AsyncMock(side_effect=[
                MagicMock(content=gm_with_action),
                MagicMock(content=render_with_reflection),
            ])
            adapter = EngineBackedAdapter(orchestrator=orch)
            req = TurnRequest(
                session_id="s1",
                user=TurnRequestUser(id="u1"),
                input=TurnRequestInput(text="문을 열어볼래?\n*나는 손으로 문을 가리켰다.*"),
            )
            resp = await adapter.turn(req, trace_id="trc_action")
            return resp, llm.generate.call_args_list

        resp, calls = asyncio.run(run())
        self.assertIsNotNone(resp.turn.passage)
        self.assertIn("가리키", resp.turn.passage or "")
        self.assertGreaterEqual(len(calls), 2)
        # 두 번째 호출(Character Render)의 prompt: kwargs 또는 첫 번째 positional
        c1 = calls[1]
        second_prompt = (c1.kwargs or {}).get("prompt") or (c1.args[0] if c1.args else "")
        self.assertIn("user_action", second_prompt)
        self.assertIn("손으로 문을 가리켰다", second_prompt)


# -----------------------------------------------------------------------------
# 8. passage fallback (렌더가 null/빈 문자열 반환 시)
# -----------------------------------------------------------------------------
class TestPassageFallbackWhenNull(unittest.TestCase):
    def test_passage_fallback_when_null(self):
        from mellow_link.runtime.engine_backed_adapter import EngineBackedAdapter, _passage_fallback

        async def run():
            llm = MagicMock()
            orch = MagicMock()
            orch.get_service = MagicMock(return_value=llm)
            orch.get_state = MagicMock(return_value=MagicMock(name="IDLE"))
            llm.generate = AsyncMock(side_effect=[
                MagicMock(content=json.dumps({"intent": "SMALLTALK", "confidence": 0.9, "needs_clarify": False})),
                MagicMock(content=json.dumps({"speech": "응.", "passage": None, "ooc": None})),
            ])
            adapter = EngineBackedAdapter(orchestrator=orch)
            req = TurnRequest(session_id="s1", user=TurnRequestUser(id="u1"), input=TurnRequestInput(text="안녕"))
            return await adapter.turn(req, trace_id="trc_fallback")

        resp = asyncio.run(run())
        self.assertIsNotNone(resp.turn.passage)
        expected_sub = "상황을 살피며"
        self.assertIn(expected_sub, resp.turn.passage)
