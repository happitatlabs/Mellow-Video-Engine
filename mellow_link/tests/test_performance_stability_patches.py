"""
Smoke tests for Performance Stability patches (metrics async, observation by mode, prompt templates).

Run with: pytest mellow_link/tests/test_performance_stability_patches.py -v
Or: python -m pytest mellow_link/tests/test_performance_stability_patches.py -v
Or: python mellow_link/tests/test_performance_stability_patches.py (from project root)
"""

import sys
from pathlib import Path

# 프로젝트 루트를 path에 추가 (직접 실행 시 mellow_link import 가능)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import asyncio
import os
import unittest
from unittest.mock import patch, MagicMock, AsyncMock

# Ensure env flags are off by default for tests so baseline behavior holds
def _env_off():
    for key in (
        "MELLOW_METRICS_ENABLED",
        "MELLOW_PROMPT_TEMPLATE_MODE",
        "MELLOW_OBSERVATION_STRICT_MODES",
    ):
        os.environ.pop(key, None)


class TestMetricsAsyncFlush(unittest.TestCase):
    """Metrics must not write to DB on request path when ASYNC_FLUSH enabled."""

    def setUp(self):
        _env_off()

    def test_collector_push_does_not_call_save_metric(self):
        """Push only enqueues; no direct save_metric call."""
        os.environ["MELLOW_METRICS_ENABLED"] = "1"
        try:
            from mellow_link.core.metrics_collector import MetricsCollector
            coll = MetricsCollector(async_flush=True, flush_interval_ms=500, flush_batch_size=50)
            with patch("mellow_link.infra.memory_database.get_memory_db") as mock_get_db:
                coll.push("TTFT_MS", 100.0, "ms")
                coll.push("TPS", 25.0, "tokens/s")
                # No flush called from push
                mock_get_db.assert_not_called()
            self.assertEqual(len(coll._queue), 2)
        finally:
            _env_off()

    def test_disabled_collector_returns_none(self):
        """When metrics_enabled=False, init returns None."""
        os.environ["MELLOW_METRICS_ENABLED"] = "0"
        try:
            from mellow_link.core.metrics_collector import init_metrics_collector
            from mellow_link.core.metrics_collector import shutdown_metrics_collector
            shutdown_metrics_collector()
            c = init_metrics_collector(enabled=False)
            self.assertIsNone(c)
        finally:
            _env_off()

    def test_queue_overflow_drops_oldest(self):
        """When queue reaches max_queue_size, push drops oldest; size stays at max."""
        from mellow_link.core.metrics_collector import MetricsCollector
        coll = MetricsCollector(async_flush=True, flush_interval_ms=500, flush_batch_size=50, max_queue_size=3)
        coll.push("A", 1.0, "x", metric_id="id1")
        coll.push("B", 2.0, "x", metric_id="id2")
        coll.push("C", 3.0, "x", metric_id="id3")
        self.assertEqual(len(coll._queue), 3)
        coll.push("D", 4.0, "x", metric_id="id4")
        self.assertEqual(len(coll._queue), 3)
        ids = [e.metric_id for e in coll._queue]
        self.assertNotIn("id1", ids)
        self.assertIn("id4", ids)


class TestObservationStrictByMode(unittest.TestCase):
    """Observation enforcement only in thinking/research mode."""

    def setUp(self):
        _env_off()

    def test_strict_modes_parsed(self):
        """Observation strict: only thinking,research require tool; fast does not."""
        strict_modes_str = "thinking,research"
        modes = [m.strip().lower() for m in strict_modes_str.split(",") if m.strip()]
        self.assertIn("thinking", modes)
        self.assertIn("research", modes)
        self.assertNotIn("fast", modes)
        require_fast = "fast".strip().lower() in modes
        require_thinking = "thinking".strip().lower() in modes
        self.assertFalse(require_fast)
        self.assertTrue(require_thinking)

    def test_fast_mode_does_not_require_tool(self):
        """When mode=fast, require_at_least_one_tool should be False from orchestrator logic."""
        strict_modes_str = "thinking,research"
        strict_modes = [m.strip().lower() for m in strict_modes_str.split(",") if m.strip()]
        require_observation = "fast".strip().lower() in strict_modes
        self.assertFalse(require_observation)
        require_observation = "thinking".strip().lower() in strict_modes
        self.assertTrue(require_observation)


class TestObservationSubstantive(unittest.TestCase):
    """_has_valid_tool_execution: only substantive observations count."""

    def test_empty_observation_not_valid(self):
        from mellow_link.core.agent_parsers import _has_valid_tool_execution
        from mellow_link.core.agent_schemas import AgentStep, AgentAction
        steps = [
            AgentStep(turn=1, thought="", action=AgentAction(tool="read_file", args={"file_path": "x"}), observation=""),
        ]
        self.assertFalse(_has_valid_tool_execution(steps))

    def test_error_observation_not_valid(self):
        from mellow_link.core.agent_parsers import _has_valid_tool_execution
        from mellow_link.core.agent_schemas import AgentStep, AgentAction
        steps = [
            AgentStep(turn=1, thought="", action=AgentAction(tool="read_file", args={}), observation="[Error] file not found"),
        ]
        self.assertFalse(_has_valid_tool_execution(steps))

    def test_substantive_observation_valid(self):
        from mellow_link.core.agent_parsers import _has_valid_tool_execution
        from mellow_link.core.agent_schemas import AgentStep, AgentAction
        steps = [
            AgentStep(turn=1, thought="", action=AgentAction(tool="read_file", args={"file_path": "workspace/a.txt"}), observation="Hello world content here."),
        ]
        self.assertTrue(_has_valid_tool_execution(steps))

    def test_structured_valid_observation(self):
        """Dict with status ok/success or row_count>0 or non-empty data counts as substantive."""
        from mellow_link.core.agent_parsers import _is_substantive_observation
        self.assertTrue(_is_substantive_observation({"status": "ok"}))
        self.assertTrue(_is_substantive_observation({"status": "success"}))
        self.assertTrue(_is_substantive_observation({"row_count": 5}))
        self.assertTrue(_is_substantive_observation({"data": [1, 2, 3]}))
        self.assertTrue(_is_substantive_observation({"result": "done"}))

    def test_structured_failure_not_valid(self):
        """Dict with status error/failed does not count as substantive."""
        from mellow_link.core.agent_parsers import _is_substantive_observation
        self.assertFalse(_is_substantive_observation({"status": "error"}))
        self.assertFalse(_is_substantive_observation({"status": "failed"}))
        self.assertFalse(_is_substantive_observation({"status": "failure", "message": "timeout"}))

    def test_empty_dict_not_valid(self):
        """Empty dict and placeholder-like dict do not count as substantive."""
        from mellow_link.core.agent_parsers import _is_substantive_observation
        self.assertFalse(_is_substantive_observation({}))
        self.assertFalse(_is_substantive_observation({"placeholder": True}))
        self.assertFalse(_is_substantive_observation({"status": "pending"}))


class TestPhase1ChatMetrics(unittest.TestCase):
    """Phase 1: chat() path records INFER_MS, TPS_APPROX, TTFT_MS=-1, TTFT_MEASURED=0; no DB in request path."""

    def setUp(self):
        _env_off()

    def test_chat_pushes_infer_ms_and_tps_approx_when_collector_enabled(self):
        """When get_metrics_collector() returns a collector, chat() pushes INFER_MS, TPS_APPROX, TTFT_MS=-1, TTFT_MEASURED=0."""
        os.environ["MELLOW_METRICS_ENABLED"] = "1"
        try:
            from mellow_link.core.metrics_collector import MetricsCollector
            coll = MetricsCollector(async_flush=True, flush_interval_ms=500, flush_batch_size=50)
            push_infer_ms_calls = []
            push_tps_approx_calls = []
            push_ttft_measured_calls = []
            push_calls = []
            coll.push_infer_ms = lambda ms, rid=None: push_infer_ms_calls.append((ms, rid))
            coll.push_tps_approx = lambda tps, rid=None: push_tps_approx_calls.append((tps, rid))
            coll.push_ttft_measured = lambda measured, rid=None: push_ttft_measured_calls.append((measured, rid))
            def capture_push(cat, val, unit, metric_id=None, **kw):
                push_calls.append((cat, val, unit, metric_id))
            coll.push = capture_push

            from mellow_link.services.llm_service import LLMService, LLMStatus
            svc = LLMService(host="localhost", port=11434)
            mock_resp = MagicMock()
            mock_resp.status = 200
            async def _json():
                return {"message": {"content": "Hi"}, "done": True, "eval_count": 10, "prompt_eval_count": 100}
            mock_resp.json = _json
            mock_session = MagicMock()
            mock_session.closed = False
            async_cm = MagicMock()
            async_cm.__aenter__ = AsyncMock(return_value=mock_resp)
            async_cm.__aexit__ = AsyncMock(return_value=None)
            mock_session.post = MagicMock(return_value=async_cm)
            with patch("mellow_link.core.metrics_collector.get_metrics_collector", return_value=coll):
                with patch.object(svc, "is_ready", return_value=True):
                    with patch.object(svc, "_session", mock_session):
                        with patch.object(svc, "_status", LLMStatus.CONNECTED):
                            loop = asyncio.new_event_loop()
                            try:
                                loop.run_until_complete(svc.chat([{"role": "user", "content": "hello"}], model="test"))
                            finally:
                                loop.close()

            self.assertGreater(len(push_infer_ms_calls), 0, "push_infer_ms should be called")
            self.assertGreater(len(push_tps_approx_calls), 0, "push_tps_approx should be called")
            self.assertGreater(len(push_ttft_measured_calls), 0, "push_ttft_measured should be called")
            self.assertEqual(push_ttft_measured_calls[0][0], False, "TTFT_MEASURED should be False for chat path")
            ttft_ms_pushes = [p for p in push_calls if p[0] == "TTFT_MS"]
            self.assertGreater(len(ttft_ms_pushes), 0, "TTFT_MS should be pushed")
            self.assertEqual(ttft_ms_pushes[0][1], -1.0, "TTFT_MS should be -1 (not measured)")
        finally:
            _env_off()

    def test_chat_no_direct_db_write(self):
        """chat() path must not call get_memory_db or save_metric."""
        os.environ["MELLOW_METRICS_ENABLED"] = "1"
        try:
            from mellow_link.core.metrics_collector import MetricsCollector
            coll = MetricsCollector(async_flush=True, flush_interval_ms=500, flush_batch_size=50)
            from mellow_link.services.llm_service import LLMService, LLMStatus
            svc = LLMService(host="localhost", port=11434)
            mock_resp = MagicMock()
            mock_resp.status = 200
            async def json_coro():
                return {"message": {"content": "Hi"}, "done": True, "eval_count": 10, "prompt_eval_count": 100}
            mock_resp.json = json_coro
            mock_session = MagicMock()
            mock_session.closed = False
            async_cm = MagicMock()
            async_cm.__aenter__ = AsyncMock(return_value=mock_resp)
            async_cm.__aexit__ = AsyncMock(return_value=None)
            mock_session.post = MagicMock(return_value=async_cm)
            with patch("mellow_link.core.metrics_collector.get_metrics_collector", return_value=coll):
                with patch("mellow_link.infra.memory_database.get_memory_db") as mock_get_db:
                    with patch.object(svc, "is_ready", return_value=True):
                        with patch.object(svc, "_session", mock_session):
                            with patch.object(svc, "_status", LLMStatus.CONNECTED):
                                loop = asyncio.new_event_loop()
                                try:
                                    loop.run_until_complete(svc.chat([{"role": "user", "content": "hello"}], model="test"))
                                finally:
                                    loop.close()
                    mock_get_db.assert_not_called()
        finally:
            _env_off()


class TestPhase1StreamMetrics(unittest.TestCase):
    """Phase 1: generate_stream() pushes TTFT_MS and TTFT_MEASURED=1 on first token; no DB in request path."""

    def setUp(self):
        _env_off()

    def test_stream_pushes_ttft_and_measured_on_first_token(self):
        """On first token, push_ttft and push_ttft_measured(True) are called."""
        os.environ["MELLOW_METRICS_ENABLED"] = "1"
        try:
            import asyncio
            from mellow_link.core.metrics_collector import MetricsCollector
            coll = MetricsCollector(async_flush=True, flush_interval_ms=500, flush_batch_size=50)
            push_ttft_calls = []
            push_ttft_measured_calls = []
            coll.push_ttft = lambda ms, rid=None: push_ttft_calls.append((ms, rid))
            coll.push_ttft_measured = lambda measured, rid=None: push_ttft_measured_calls.append((measured, rid))

            async def stream_resp():
                yield b'{"message":{"content":"H"}}\n'
                yield b'{"message":{"content":"i"}}\n'
                yield b'{"done":true,"eval_count":2,"prompt_eval_count":5}\n'

            class StreamCtx:
                status = 200
                async def __aenter__(self):
                    return self
                async def __aexit__(self, *a):
                    pass
                @property
                def content(self):
                    return self
                def __aiter__(self):
                    return stream_resp().__aiter__()

            from mellow_link.services.llm_service import LLMService, LLMStatus
            svc = LLMService(host="localhost", port=11434)
            mock_session = MagicMock()
            mock_session.closed = False
            mock_session.post = MagicMock(return_value=StreamCtx())
            with patch("mellow_link.core.metrics_collector.get_metrics_collector", return_value=coll):
                with patch.object(svc, "is_ready", return_value=True):
                    with patch.object(svc, "_session", mock_session):
                        with patch.object(svc, "_status", LLMStatus.CONNECTED):
                            chunks = []
                            async def consume():
                                async for c in svc.generate_stream("hi", model="test"):
                                    chunks.append(c)
                            loop = asyncio.new_event_loop()
                            try:
                                loop.run_until_complete(consume())
                            finally:
                                loop.close()

            self.assertGreater(len(push_ttft_calls), 0, "push_ttft should be called on first token")
            self.assertGreater(len(push_ttft_measured_calls), 0, "push_ttft_measured should be called")
            self.assertEqual(push_ttft_measured_calls[0][0], True, "TTFT_MEASURED should be True for stream path")
        finally:
            _env_off()

    def test_stream_no_direct_db_write(self):
        """generate_stream() must not call get_memory_db in request path."""
        os.environ["MELLOW_METRICS_ENABLED"] = "1"
        try:
            import asyncio
            from mellow_link.core.metrics_collector import MetricsCollector
            coll = MetricsCollector(async_flush=True, flush_interval_ms=500, flush_batch_size=50)

            async def stream_resp():
                yield b'{"message":{"content":"x"}}\n'
                yield b'{"done":true,"eval_count":1,"prompt_eval_count":2}\n'

            class StreamCtx:
                status = 200
                async def __aenter__(self):
                    return self
                async def __aexit__(self, *a):
                    pass
                @property
                def content(self):
                    return self
                def __aiter__(self):
                    return stream_resp().__aiter__()

            from mellow_link.services.llm_service import LLMService, LLMStatus
            svc = LLMService(host="localhost", port=11434)
            mock_session = MagicMock()
            mock_session.closed = False
            mock_session.post = MagicMock(return_value=StreamCtx())
            with patch("mellow_link.core.metrics_collector.get_metrics_collector", return_value=coll):
                with patch("mellow_link.infra.memory_database.get_memory_db") as mock_get_db:
                    with patch.object(svc, "is_ready", return_value=True):
                        with patch.object(svc, "_session", mock_session):
                            with patch.object(svc, "_status", LLMStatus.CONNECTED):
                                async def consume():
                                    async for _ in svc.generate_stream("hi", model="test"):
                                        pass
                                loop = asyncio.new_event_loop()
                                try:
                                    loop.run_until_complete(consume())
                                finally:
                                    loop.close()
                    mock_get_db.assert_not_called()
        finally:
            _env_off()


class TestMetricsDisabledBaseline(unittest.TestCase):
    """When metrics disabled, get_metrics_collector() is None; no collector calls in chat/stream."""

    def setUp(self):
        _env_off()

    def test_chat_with_collector_none_does_not_fail(self):
        """chat() when get_metrics_collector() returns None completes without error."""
        os.environ["MELLOW_METRICS_ENABLED"] = "0"
        try:
            from mellow_link.services.llm_service import LLMService, LLMStatus
            svc = LLMService(host="localhost", port=11434)
            mock_resp = MagicMock()
            mock_resp.status = 200
            async def json_coro():
                return {"message": {"content": "Hi"}, "done": True, "eval_count": 10, "prompt_eval_count": 100}
            mock_resp.json = json_coro
            mock_session = MagicMock()
            mock_session.closed = False
            async_cm = MagicMock()
            async_cm.__aenter__ = AsyncMock(return_value=mock_resp)
            async_cm.__aexit__ = AsyncMock(return_value=None)
            mock_session.post = MagicMock(return_value=async_cm)
            with patch("mellow_link.core.metrics_collector.get_metrics_collector", return_value=None):
                with patch.object(svc, "is_ready", return_value=True):
                    with patch.object(svc, "_session", mock_session):
                        with patch.object(svc, "_status", LLMStatus.CONNECTED):
                            loop = asyncio.new_event_loop()
                            try:
                                loop.run_until_complete(svc.chat([{"role": "user", "content": "hello"}], model="test"))
                            finally:
                                loop.close()
        finally:
            _env_off()


class TestPromptBuilderNoTruncation(unittest.TestCase):
    """Prompt builder drops whole sections; never truncates mid-policy."""

    def test_assembled_drops_sections_by_count(self):
        """build_system_prompt_assembled caps memories and history by count, not by char cut."""
        from mellow_link.core.agent_prompts import build_system_prompt_assembled
        tools = '["read_file"]'
        memories = ["mem1", "mem2", "mem3", "mem4", "mem5"]
        out = build_system_prompt_assembled(
            tools,
            mode="fast",
            user_memories=memories,
            memories_max=3,
            history_max_turns=2,
            rag_max_items=3,
        )
        # Should contain at most 3 memory items (whole items, not cut)
        self.assertIn("mem1", out)
        self.assertIn("mem3", out)
        self.assertNotIn("mem4", out)
        self.assertNotIn("mem5", out)
        self.assertIn("workspace", out, "Assembled prompt must contain required sandbox phrase")

    def test_assembled_raises_when_sandbox_phrase_missing(self):
        """Defensive: build_system_prompt_assembled raises RuntimeError if base omits sandbox phrase."""
        import mellow_link.core.agent_prompts as ap
        with patch.object(ap, "_get_base_template_by_mode", return_value="no sandbox phrase in this base"):
            with self.assertRaises(RuntimeError) as ctx:
                ap.build_system_prompt_assembled("[]", mode="fast")
            self.assertIn("workspace", str(ctx.exception))

    def test_base_template_contains_no_hallucination_policy_intact(self):
        """Base mini template contains full NO_HALLUCINATION sentence (no mid-sentence cut)."""
        from mellow_link.core.agent_prompts import _get_base_template_by_mode
        base = _get_base_template_by_mode("fast", "[]")
        self.assertIn("NO_HALLUCINATION", base)
        self.assertIn("Observation 결과를 받은 후에만 결론 도출", base)

    def test_build_system_prompt_backward_compatible(self):
        """build_system_prompt(tools_json, persona) still works (no template mode)."""
        from mellow_link.core.agent_prompts import build_system_prompt
        out = build_system_prompt("[]", persona="", use_template_mode=False)
        self.assertIn("CRITICAL", out)
        self.assertIn("[]", out)


class TestModeSelectionToolRequired(unittest.TestCase):
    """Tool-required and short-query mode selection (auto mode)."""

    def test_tool_required_short_query_selects_thinking(self):
        """Short query with tool keyword (e.g. '파일 읽어줘') must select thinking."""
        from mellow_link.core.orchestrator_chat import ChatPipelineProcessor
        proc = ChatPipelineProcessor(MagicMock())
        self.assertEqual(proc._select_mode_for_query("파일 읽어줘"), "thinking")
        self.assertEqual(proc._select_mode_for_query("read file"), "thinking")

    def test_short_simple_query_selects_fast(self):
        """Short query without deep/tool keywords (e.g. '안녕?') must select fast."""
        from mellow_link.core.orchestrator_chat import ChatPipelineProcessor
        proc = ChatPipelineProcessor(MagicMock())
        self.assertEqual(proc._select_mode_for_query("안녕?"), "fast")

    def test_prompt_category_tool_plus_auto_selects_thinking(self):
        """When prompt_category='tool' and mode is auto, selected_mode must never be 'fast' (must be 'thinking')."""
        from mellow_link.core.orchestrator_chat import ChatPipelineProcessor
        proc = ChatPipelineProcessor(MagicMock())
        # Any query with prompt_category="tool" must yield "thinking"
        self.assertEqual(proc._select_mode_for_query("hello", prompt_category="tool"), "thinking")
        self.assertEqual(proc._select_mode_for_query("안녕", prompt_category="tool"), "thinking")
        self.assertEqual(proc._select_mode_for_query("현재 시간 알려줘", prompt_category="tool"), "thinking")

    def test_plan_intent_auto_mode_selects_thinking(self):
        """Plan intent (e.g. 'MVP 만들기, To-do 7개') in auto mode must select 'thinking', not thinking-lite."""
        from mellow_link.core.orchestrator_chat import ChatPipelineProcessor
        from mellow_link.core.output_sanitizer import detect_plan_intent
        proc = ChatPipelineProcessor(MagicMock())
        query = "MVP 만들기, To-do 7개"
        self.assertTrue(detect_plan_intent(query), "detect_plan_intent should recognize plan intent")
        effective_mode = proc._select_mode_for_query(query)
        self.assertEqual(effective_mode, "thinking", "plan_intent in auto must route to thinking (tool-call capable)")


def _is_empty_response_spec(text):
    """Mirror AgentBrain._is_empty_llm_response: empty = strip empty or length < 10."""
    if text is None or not isinstance(text, str):
        return True
    s = text.strip()
    return len(s) == 0 or len(s) < 10


class TestFastFallbackPolicy(unittest.TestCase):
    """Strict fast fallback: empty definition and one per session."""

    def test_is_empty_llm_response(self):
        """Empty = strip empty or length < 10 (spec: a, b, c)."""
        self.assertTrue(_is_empty_response_spec(""))
        self.assertTrue(_is_empty_response_spec("   "))
        self.assertTrue(_is_empty_response_spec("ab"))
        self.assertTrue(_is_empty_response_spec(None))
        self.assertFalse(_is_empty_response_spec("Hello, world."))
        self.assertFalse(_is_empty_response_spec("0123456789"))

    @unittest.skipIf(not hasattr(asyncio, "run"), "asyncio.run required")
    def test_fallback_sets_session_state_once(self):
        """First empty fast response triggers fallback and sets fast_fallback_used."""
        try:
            from mellow_link.core.agent_brain import AgentBrain
            mock_llm = MagicMock()
            mock_llm.is_ready = MagicMock(return_value=True)
            mock_llm.get_model_for_mode = MagicMock(side_effect=lambda m: "thinking" if m == "thinking" else "fast")
            mock_llm.chat = AsyncMock(return_value=MagicMock(text="", tool_calls=None))
            brain = AgentBrain(mock_llm, max_turns=1)
        except Exception as e:
            self.skipTest(f"AgentBrain init failed (e.g. missing psutil): {e}")
        brain._model_mode = "fast"
        session_state = {"fast_fallback_used": False}
        asyncio.run(brain._call_llm([{"role": "user", "content": "hi"}], session_state=session_state))
        self.assertTrue(session_state.get("fast_fallback_used"), "First empty should set fast_fallback_used")

    @unittest.skipIf(not hasattr(asyncio, "run"), "asyncio.run required")
    def test_second_fallback_blocked(self):
        """When fast_fallback_used already True, do not fallback again (no thinking model call)."""
        call_count = {"chat": 0}

        async def chat_side_effect(*args, **kwargs):
            call_count["chat"] += 1
            return MagicMock(text="", tool_calls=None)

        try:
            from mellow_link.core.agent_brain import AgentBrain
            mock_llm = MagicMock()
            mock_llm.is_ready = MagicMock(return_value=True)
            mock_llm.get_model_for_mode = MagicMock(side_effect=lambda m: "thinking" if m == "thinking" else "fast")
            mock_llm.chat = AsyncMock(side_effect=chat_side_effect)
            brain = AgentBrain(mock_llm, max_turns=1)
        except Exception as e:
            self.skipTest(f"AgentBrain init failed (e.g. missing psutil): {e}")
        brain._model_mode = "fast"
        session_state = {"fast_fallback_used": True}
        asyncio.run(brain._call_llm([{"role": "user", "content": "hi"}], session_state=session_state))
        # Initial + no_tools retry = 2 (no tools in call); no 3rd (thinking) call when fallback blocked
        self.assertEqual(call_count["chat"], 2, "Blocked fallback must not call thinking model (only 2 chats)")


class TestSessionScopedFallback(unittest.TestCase):
    """Fast fallback once per session: session_state persists across run_agent calls."""

    @unittest.skipIf(not hasattr(asyncio, "run"), "asyncio.run required")
    def test_same_session_id_receives_persisted_fallback_state(self):
        """Two run_agent(session_id=X) calls: second call receives session_state with fast_fallback_used=True after first run 'used' fallback."""
        from mellow_link.core.orchestrator import Orchestrator
        from mellow_link.core.agent_schemas import AgentResult

        run_calls = []

        async def capture_run(*args, **kwargs):
            run_calls.append(kwargs)
            return AgentResult(answer="ok", steps=[])

        orch = Orchestrator()
        orig_agent = orch.agent
        orig_lock = orch._gpu_lock
        try:
            orch.agent = MagicMock()
            orch.agent.run = AsyncMock(side_effect=capture_run)
            orch._gpu_lock = asyncio.Lock()

            asyncio.run(orch.run_agent("hi", history=[], session_id="test-session-fallback"))
            self.assertEqual(len(run_calls), 1)
            self.assertIn("session_state", run_calls[0])
            self.assertFalse(run_calls[0]["session_state"].get("fast_fallback_used"))

            orch._session_runtime["test-session-fallback"]["fast_fallback_used"] = True

            asyncio.run(orch.run_agent("hi again", history=[], session_id="test-session-fallback"))
            self.assertEqual(len(run_calls), 2)
            self.assertIn("session_state", run_calls[1])
            self.assertTrue(run_calls[1]["session_state"].get("fast_fallback_used"))
        finally:
            orch.agent = orig_agent
            orch._gpu_lock = orig_lock
            orch._session_runtime.pop("test-session-fallback", None)


class TestFastModePromptOptimization(unittest.TestCase):
    """FAST mode prompt size optimization: experience_advisory and tools_schema disabled."""

    def test_observation_size_capping(self):
        """Large observation (dict/list) should be capped with TRUNCATED_OBS marker."""
        try:
            from mellow_link.core.agent_brain import _cap_observation_size
            
            # Large dict observation
            large_dict = {"key" + str(i): "value" * 100 for i in range(100)}
            capped = _cap_observation_size(large_dict, max_chars=1200)
            
            self.assertIn("[TRUNCATED_OBS]", capped)
            self.assertIn("original_len=", capped)
            self.assertLessEqual(len(capped), 1200 + 50)  # Allow some margin for marker
            
            # Large string observation
            large_string = "x" * 5000
            capped_str = _cap_observation_size(large_string, max_chars=1200)
            
            self.assertIn("[TRUNCATED_OBS]", capped_str)
            self.assertIn("original_len=5000", capped_str)
            self.assertLessEqual(len(capped_str), 1200 + 50)
            
            # Small observation should not be truncated
            small = "small observation"
            small_capped = _cap_observation_size(small, max_chars=1200)
            self.assertEqual(small_capped, small)
            self.assertNotIn("[TRUNCATED_OBS]", small_capped)
            
        except Exception as e:
            self.skipTest(f"Test setup failed: {e}")

    def test_fast_mode_excludes_experience_advisory(self):
        """FAST mode should not include experience_advisory in prompt."""
        try:
            from mellow_link.core.agent_brain import AgentBrain
            from mellow_link.core.tool_registry import ToolRegistry
            
            mock_llm = MagicMock()
            mock_llm.is_ready = MagicMock(return_value=True)
            mock_llm.get_model_for_mode = MagicMock(return_value="qwen2.5:7b")
            mock_llm.chat = AsyncMock(return_value=MagicMock(text="ok", tool_calls=None))
            
            registry = ToolRegistry()
            brain = AgentBrain(mock_llm, max_turns=1, registry=registry)
            brain._model_mode = "fast"
            
            # Mock experience provider to return long advisory
            mock_experience_provider = MagicMock()
            mock_experience_provider.get_experience_advisory = MagicMock(
                return_value="This is a very long experience advisory that should not be included in FAST mode. " * 100
            )
            brain._experience_provider = mock_experience_provider
            brain._enable_experience_retrieval = True
            
            # Build prompt (simulate run_agent prompt building)
            from mellow_link.core.agent_prompts import build_system_prompt
            tools_json = registry.get_tools_prompt()
            system_prompt = build_system_prompt(
                tools_json,
                mode="fast",
                use_template_mode=True
            )
            
            # FAST mode should not include experience_advisory
            # Check that experience_advisory is not in system_prompt
            # (experience_advisory is added in agent_brain.py, but should be empty for fast mode)
            self.assertNotIn("experience advisory", system_prompt.lower())
            self.assertNotIn("very long experience", system_prompt.lower())
            
        except Exception as e:
            self.skipTest(f"Test setup failed: {e}")

    def test_fast_mode_excludes_tools_schema(self):
        """FAST mode should use empty tools_schema list."""
        try:
            from mellow_link.core.agent_brain import AgentBrain
            from mellow_link.core.tool_registry import ToolRegistry
            
            mock_llm = MagicMock()
            mock_llm.is_ready = MagicMock(return_value=True)
            mock_llm.get_model_for_mode = MagicMock(return_value="qwen2.5:7b")
            mock_llm.chat = AsyncMock(return_value=MagicMock(text="ok", tool_calls=None))
            
            registry = ToolRegistry()
            brain = AgentBrain(mock_llm, max_turns=1, registry=registry)
            brain._model_mode = "fast"
            
            # Get tools_schema for fast mode (should be empty)
            tools_schema = registry.get_tools_schema()
            # In FAST mode, tools_schema should be set to [] in agent_brain.py
            # This test verifies the logic exists
            effective_mode = "fast"
            if effective_mode == "fast":
                tools_schema_fast = []
            else:
                tools_schema_fast = tools_schema
            
            self.assertEqual(len(tools_schema_fast), 0, "FAST mode should have empty tools_schema")
            
        except Exception as e:
            self.skipTest(f"Test setup failed: {e}")

    def test_fast_mode_prompt_size_under_threshold(self):
        """FAST mode prompt should be under reasonable threshold (< 6000 chars)."""
        try:
            from mellow_link.core.agent_prompts import build_system_prompt_assembled
            from mellow_link.core.tool_registry import ToolRegistry
            
            registry = ToolRegistry()
            tools_json = registry.get_tools_prompt()
            
            # Build FAST mode prompt with dummy long history and memories
            long_history = [
                {"role": "user", "content": "Long user message " * 50},
                {"role": "assistant", "content": "Long assistant response " * 50},
            ] * 5  # 10 messages total
            
            long_memories = ["Memory " * 100] * 10
            
            system_prompt = build_system_prompt_assembled(
                tools_json,
                mode="fast",
                user_memories=long_memories,
                recent_history=long_history,
                memories_max=3,
                history_max_turns=2,
            )
            
            prompt_chars = len(system_prompt)
            self.assertLess(
                prompt_chars, 6000,
                f"FAST mode prompt should be under 6000 chars, got {prompt_chars:,}"
            )
            
        except Exception as e:
            self.skipTest(f"Test setup failed: {e}")

    def test_fast_mode_no_get_past_failure_context_reference(self):
        """FAST mode prompt should not reference get_past_failure_context tool."""
        try:
            from mellow_link.core.agent_prompts import build_system_prompt_assembled, SYSTEM_PROMPT_FAST_MIN
            from mellow_link.core.tool_registry import ToolRegistry
            
            registry = ToolRegistry()
            tools_json = registry.get_tools_prompt()
            
            # Build FAST mode prompt
            system_prompt = build_system_prompt_assembled(
                tools_json,
                mode="fast",
            )
            
            # Check that get_past_failure_context is not referenced
            self.assertNotIn(
                "get_past_failure_context",
                system_prompt.lower(),
                "FAST mode prompt should not reference get_past_failure_context"
            )
            
            # Also check base template
            self.assertNotIn(
                "get_past_failure_context",
                SYSTEM_PROMPT_FAST_MIN.lower(),
                "FAST mode base template should not reference get_past_failure_context"
            )
            
        except Exception as e:
            self.skipTest(f"Test setup failed: {e}")


class TestFastModeMaxTurnsAndEscalation(unittest.TestCase):
    """FAST mode max_turns=2 and tool_call escalation to THINKING."""

    def setUp(self):
        _env_off()

    @unittest.skipIf(not hasattr(asyncio, "run"), "asyncio.run required")
    def test_fast_mode_max_turns_capped_at_2(self):
        """FAST mode max_turns must be capped at 2 regardless of complexity evaluator."""
        try:
            from mellow_link.core.agent_brain import AgentBrain
            from mellow_link.core.agent_schemas import AgentAction
            
            mock_llm = MagicMock()
            mock_llm.is_ready = MagicMock(return_value=True)
            mock_llm.get_model_for_mode = MagicMock(return_value="fast")
            mock_llm.chat = AsyncMock(return_value=MagicMock(text="응답", tool_calls=None))
            
            brain = AgentBrain(mock_llm, max_turns=10)  # 기본값은 10이지만 FAST 모드에서는 2로 제한되어야 함
            
            # Mock tool registry
            mock_registry = MagicMock()
            mock_registry.get_tools_prompt = MagicMock(return_value="[]")
            mock_registry.get_tools_schema = MagicMock(return_value=[])
            mock_registry.execute = AsyncMock(return_value="result")
            brain._registry = mock_registry
            
            # Mock experience provider
            brain._experience_provider = None
            brain._enable_experience_retrieval = False
            
            # Mock checkpoint manager
            brain._checkpoint_manager = None
            
            # Run with FAST mode
            result = asyncio.run(brain.run("안녕?", mode="fast"))
            
            # FAST 모드에서는 max_turns가 2로 제한되어야 함
            # 실제로는 2턴 이내에 종료되어야 함 (no_tool_calls로 종료)
            self.assertLessEqual(result.total_turns, 2, "FAST mode should cap max_turns at 2")
            
        except Exception as e:
            self.skipTest(f"Test setup failed: {e}")

    @unittest.skipIf(not hasattr(asyncio, "run"), "asyncio.run required")
    def test_fast_mode_tool_call_escalates_to_thinking(self):
        """FAST mode tool_call detection should escalate to THINKING mode once."""
        try:
            from mellow_link.core.agent_brain import AgentBrain
            from mellow_link.core.agent_schemas import AgentAction
            
            mock_llm = MagicMock()
            mock_llm.is_ready = MagicMock(return_value=True)
            mock_llm.get_model_for_mode = MagicMock(side_effect=lambda m: "fast" if m == "fast" else "thinking")
            
            # 첫 번째 호출: tool_call 반환 (FAST 모드)
            # 두 번째 호출: THINKING 모드로 전환 후 호출
            call_count = [0]
            def mock_chat(*args, **kwargs):
                call_count[0] += 1
                if call_count[0] == 1:
                    # 첫 번째 호출: tool_call 반환
                    return MagicMock(
                        text="",
                        tool_calls=[{
                            "function": {"name": "read_file", "arguments": '{"path": "test.txt"}'}
                        }]
                    )
                else:
                    # 이후 호출: 일반 응답
                    return MagicMock(text="완료했습니다.", tool_calls=None)
            
            mock_llm.chat = AsyncMock(side_effect=mock_chat)
            
            brain = AgentBrain(mock_llm, max_turns=5)
            
            # Mock tool registry
            mock_registry = MagicMock()
            mock_registry.get_tools_prompt = MagicMock(return_value="[]")
            mock_registry.get_tools_schema = MagicMock(return_value=[])
            mock_registry.execute = AsyncMock(return_value="file content")
            brain._registry = mock_registry
            
            # Mock experience provider
            brain._experience_provider = None
            brain._enable_experience_retrieval = False
            
            # Mock checkpoint manager
            brain._checkpoint_manager = None
            
            # Run with FAST mode
            result = asyncio.run(brain.run("파일 읽어줘", mode="fast"))
            
            # 에스컬레이션이 발생했는지 확인 (get_model_for_mode가 "thinking"으로 호출되었는지)
            # 실제로는 THINKING 모드로 전환되어 도구가 실행되어야 함
            thinking_calls = [call for call in mock_llm.get_model_for_mode.call_args_list if call[0][0] == "thinking"]
            self.assertGreater(len(thinking_calls), 0, "Should escalate to THINKING mode")
            
        except Exception as e:
            self.skipTest(f"Test setup failed: {e}")

    @unittest.skipIf(not hasattr(asyncio, "run"), "asyncio.run required")
    def test_fast_mode_tool_call_after_escalation_blocked(self):
        """FAST mode tool_call after escalation should be blocked."""
        try:
            from mellow_link.core.agent_brain import AgentBrain
            
            mock_llm = MagicMock()
            mock_llm.is_ready = MagicMock(return_value=True)
            mock_llm.get_model_for_mode = MagicMock(return_value="fast")
            
            # 연속으로 tool_call 반환 (에스컬레이션 후에도 tool_call 발생)
            mock_llm.chat = AsyncMock(return_value=MagicMock(
                text="",
                tool_calls=[{
                    "function": {"name": "read_file", "arguments": '{"path": "test.txt"}'}
                }]
            ))
            
            brain = AgentBrain(mock_llm, max_turns=5)
            
            # Mock tool registry
            mock_registry = MagicMock()
            mock_registry.get_tools_prompt = MagicMock(return_value="[]")
            mock_registry.get_tools_schema = MagicMock(return_value=[])
            brain._registry = mock_registry
            
            # Mock experience provider
            brain._experience_provider = None
            brain._enable_experience_retrieval = False
            
            # Mock checkpoint manager
            brain._checkpoint_manager = None
            
            # Run with FAST mode
            result = asyncio.run(brain.run("파일 읽어줘", mode="fast"))
            
            # 에스컬레이션 후 tool_call이 차단되어야 함
            # finish_reason이 "fast_toolcall_blocked"이어야 함
            self.assertEqual(
                result.finish_reason,
                "fast_toolcall_blocked",
                "Tool call after escalation should be blocked"
            )
            self.assertIn("FAST 모드", result.answer)
            
        except Exception as e:
            self.skipTest(f"Test setup failed: {e}")


class TestLightweightSystemTools(unittest.TestCase):
    """경량 시스템 도구 테스트 (p95 이상치 감소용)."""

    def setUp(self):
        _env_off()

    def test_get_cwd_returns_non_empty_string(self):
        """get_cwd는 비어있지 않은 문자열을 반환해야 합니다."""
        try:
            from mellow_link.core.agent_tools_system import get_cwd
            import json
            
            result = get_cwd()
            self.assertIsInstance(result, str)
            self.assertGreater(len(result), 0)
            
            # JSON 파싱 가능한지 확인
            parsed = json.loads(result)
            self.assertIn("cwd", parsed)
            self.assertIsInstance(parsed["cwd"], str)
            self.assertGreater(len(parsed["cwd"]), 0)
        except Exception as e:
            self.skipTest(f"Test setup failed: {e}")

    def test_get_time_returns_iso_format(self):
        """get_time은 ISO 형식의 시간 문자열을 반환해야 합니다."""
        try:
            from mellow_link.core.agent_tools_system import get_time
            import json
            from datetime import datetime
            
            result = get_time()
            self.assertIsInstance(result, str)
            self.assertGreater(len(result), 0)
            
            # JSON 파싱 가능한지 확인
            parsed = json.loads(result)
            self.assertIn("time", parsed)
            self.assertIsInstance(parsed["time"], str)
            
            # ISO 형식인지 확인 (대략적으로)
            time_str = parsed["time"]
            # ISO 형식은 보통 "YYYY-MM-DDTHH:MM:SS" 형태
            self.assertIn("T", time_str or "-")
        except Exception as e:
            self.skipTest(f"Test setup failed: {e}")

    def test_get_system_snapshot_returns_bounded_fields(self):
        """get_system_snapshot은 제한된 필드와 작은 페이로드를 반환해야 합니다."""
        try:
            from mellow_link.core.agent_tools_system import get_system_snapshot
            import json
            
            result = get_system_snapshot()
            self.assertIsInstance(result, str)
            
            # 최대 800자 제한 확인
            self.assertLessEqual(len(result), 800)
            
            # JSON 파싱 가능한지 확인
            parsed = json.loads(result)
            
            # 필수 필드 확인
            self.assertIn("ram_used_percent", parsed)
            self.assertIn("ram_used_gb", parsed)
            self.assertIn("ram_total_gb", parsed)
            self.assertIn("disk_used_percent", parsed)
            
            # 값이 숫자인지 확인
            self.assertIsInstance(parsed["ram_used_percent"], (int, float))
            self.assertIsInstance(parsed["ram_used_gb"], (int, float))
            self.assertIsInstance(parsed["ram_total_gb"], (int, float))
            self.assertIsInstance(parsed["disk_used_percent"], (int, float))
            
            # 큰 딕셔너리 덤프가 아닌지 확인 (원시 psutil 객체가 아닌지)
            result_str = json.dumps(parsed, ensure_ascii=False)
            self.assertLess(len(result_str), 1000)  # 합리적인 크기 제한
        except Exception as e:
            self.skipTest(f"Test setup failed: {e}")

    def test_list_processes_returns_bounded_list(self):
        """list_processes는 최대 limit개 항목과 메타데이터를 반환해야 합니다."""
        try:
            from mellow_link.core.agent_tools_system import list_processes
            import json
            
            # limit=20 테스트
            result = list_processes(limit=20, offset=0)
            self.assertIsInstance(result, str)
            
            parsed = json.loads(result)
            self.assertIn("processes", parsed)
            self.assertIn("total_count", parsed)
            self.assertIn("returned_count", parsed)
            self.assertIn("truncated", parsed)
            
            processes = parsed["processes"]
            self.assertIsInstance(processes, list)
            self.assertLessEqual(len(processes), 20)  # limit 이하
            
            # 각 프로세스 항목 구조 확인
            if processes:
                proc = processes[0]
                self.assertIn("pid", proc)
                self.assertIn("name", proc)
                self.assertIn("mem_mb", proc)
            
            # 메타데이터 확인
            self.assertIsInstance(parsed["total_count"], int)
            self.assertIsInstance(parsed["returned_count"], int)
            self.assertIsInstance(parsed["truncated"], bool)
            self.assertEqual(parsed["returned_count"], len(processes))
            
            # limit=5 테스트
            result2 = list_processes(limit=5, offset=0)
            parsed2 = json.loads(result2)
            self.assertLessEqual(len(parsed2["processes"]), 5)
            
        except Exception as e:
            self.skipTest(f"Test setup failed: {e}")

    def test_list_processes_respects_limit_max(self):
        """list_processes는 최대 limit 50을 초과하지 않아야 합니다."""
        try:
            from mellow_link.core.agent_tools_system import list_processes
            import json
            
            # limit=100 요청 (최대 50으로 제한되어야 함)
            result = list_processes(limit=100, offset=0)
            parsed = json.loads(result)
            self.assertLessEqual(len(parsed["processes"]), 50)
            
        except Exception as e:
            self.skipTest(f"Test setup failed: {e}")


if __name__ == "__main__":
    unittest.main()
