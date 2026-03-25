"""
FSM 안정성 테스트: IDLE 복귀 보장, 실패 시 이벤트, ChatState 직렬화.

- 쿨다운 구간에서도 finally → force IDLE 복귀되는지
- 강제 전이 실패 시 ERROR 이벤트 (kind=fsm_idle_recovery_failed) 발생 여부
- ChatState가 응답/이벤트에서 .value로 "idle" 등 문자열 유지되는지
"""
import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch


# -----------------------------------------------------------------------------
# 1. 쿨다운 구간에서도 IDLE 복귀되는지
# -----------------------------------------------------------------------------
class TestCooldownIdleRecovery(unittest.TestCase):
    """전이 직후(쿨다운 걸리는 타이밍) 종료해도 finally 이후 current_state == IDLE."""

    def test_idle_recovery_in_cooldown_after_task_completion(self):
        from mellow_link.core.orchestrator import Orchestrator
        from mellow_link.core.events import TaskEvent, EventType
        from mellow_link.core.states import SystemState, TaskPriority

        async def run():
            orch = Orchestrator()
            orch._task_queue = None
            orch._gpu_lock = None
            orch._initialized = False
            await orch.initialize()

            mock_llm = MagicMock()
            mock_llm.execute = AsyncMock(return_value="ok")
            orch.register_service("llm", mock_llm)

            task = TaskEvent(
                event_type=EventType.TASK_SUBMIT,
                task_type="llm",
                priority=TaskPriority.NORMAL,
                request_data={},
            )
            # 1) IDLE -> TEXT 전환 → _last_transition_time 갱신 (쿨다운 시작)
            # 2) _execute_task 즉시 완료
            # 3) finally에서 force_state_change(IDLE) → 쿨다운 무시하고 IDLE 복귀
            await orch._process_task(task)
            return orch.get_state()

        state = asyncio.run(run())
        self.assertEqual(
            state,
            SystemState.IDLE,
            "쿨다운 구간에서도 finally 이후 current_state는 IDLE이어야 함",
        )


# -----------------------------------------------------------------------------
# 2. 강제 전이가 실패했을 때 ERROR 이벤트가 남는지
# -----------------------------------------------------------------------------
class TestForceStateChangeFailureEmitsErrorEvent(unittest.TestCase):
    """일부러 불가능한 전이(또는 request_state_change 실패) 시 payload.kind == fsm_idle_recovery_failed."""

    def test_fsm_idle_recovery_failed_event_on_force_failure(self):
        from mellow_link.core.orchestrator import Orchestrator
        from mellow_link.core.events import Event, EventType
        from mellow_link.core.states import SystemState, TransitionResult

        collected = []

        async def run():
            orch = Orchestrator()
            orch._task_queue = None
            orch._gpu_lock = None
            orch._initialized = False
            await orch.initialize()

            async def capture_error(event: Event):
                if event.event_type == EventType.ERROR and isinstance(
                    getattr(event, "payload", None), dict
                ):
                    collected.append(event.payload)

            orch.register_handler(EventType.ERROR, capture_error)
            try:
                with patch.object(
                    orch,
                    "request_state_change",
                    new_callable=AsyncMock,
                    return_value=TransitionResult.INVALID_TRANSITION,
                ):
                    result = await orch.force_state_change(
                        SystemState.IDLE,
                        reason="test recovery",
                        run_id="test-run-123",
                    )
                    return result
            finally:
                orch.unregister_handler(EventType.ERROR, capture_error)

        result = asyncio.run(run())
        self.assertFalse(result.is_success())
        self.assertEqual(len(collected), 1)
        self.assertEqual(
            collected[0].get("kind"),
            "fsm_idle_recovery_failed",
            "payload.kind == fsm_idle_recovery_failed",
        )
        self.assertEqual(collected[0].get("failure_reason"), "INVALID_TRANSITION")
        self.assertEqual(collected[0].get("run_id"), "test-run-123")


# -----------------------------------------------------------------------------
# 3. ChatState 직렬화가 깨지지 않는지
# -----------------------------------------------------------------------------
class TestChatStateSerialization(unittest.TestCase):
    """응답/이벤트에서 .value로 나가서 'idle' 같은 문자열이 유지되는지."""

    def test_chat_event_to_dict_uses_value_string(self):
        """ChatEvent.to_dict() 시 chat_state는 .value 문자열."""
        from mellow_link.core.events import ChatEvent, EventType
        from mellow_link.core.states import ChatState

        ev = ChatEvent(
            event_type=EventType.CHAT_STREAM,
            chat_state=ChatState.GENERATING,
            content="chunk",
        )
        d = ev.to_dict()
        self.assertEqual(d["chat_state"], "generating")
        self.assertIsInstance(d["chat_state"], str)

    def test_chat_event_idle_value(self):
        """ChatEvent 기본 chat_state=IDLE → 'idle'."""
        from mellow_link.core.events import ChatEvent, EventType
        from mellow_link.core.states import ChatState

        ev = ChatEvent(event_type=EventType.CHAT_START, chat_state=ChatState.IDLE)
        self.assertEqual(ev.to_dict()["chat_state"], "idle")

    def test_chat_context_current_state_enum_value_consistent(self):
        """ChatContext.current_state는 Enum이며 .value로 직렬화 시 문자열 유지."""
        from mellow_link.core.orchestrator_schemas import ChatContext
        from mellow_link.core.states import ChatState

        ctx = ChatContext(user_query="q", current_state=ChatState.COMPLETED)
        self.assertIs(ctx.current_state, ChatState.COMPLETED)
        self.assertEqual(ctx.current_state.value, "completed")
        # API/응답에서 쓰일 때 .value 사용 시 문자열
        self.assertEqual(ctx.current_state.value, "completed")
