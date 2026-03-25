"""
Orchestrator - Core FSM Controller for Mellow-Link

This module implements the main orchestration logic using an async event loop
and finite state machine pattern. It coordinates GPU resource sharing between
LLM (Ollama) and Image Generation (ComfyUI) workloads.

Design Pattern:
    - Singleton pattern for single orchestrator instance
    - FSM for state management
    - Observer pattern for event distribution
    - Command pattern for task execution

Extracted from legacy:
    - state_machine.py: ChatStateMachine, StateContext
    - chat_api.py: mode routing, RAG integration, streaming
    - model_service.py: GPU lock pattern
"""

import asyncio
import logging
import os
from typing import Optional, Dict, Any, Callable, List, AsyncGenerator
from datetime import datetime, timedelta
from collections import defaultdict
import time

from .states import SystemState, TaskPriority, TransitionResult
from .events import Event, TaskEvent, StateChangeEvent, EventType, VRAMEvent
from .agent_brain import AgentBrain
from .orchestrator_schemas import ChatState, IntentResult, ChatContext, VALID_TRANSITIONS
from .orchestrator_persona import load_persona_from_file
from .orchestrator_chat import ChatPipelineProcessor

logger = logging.getLogger(__name__)


# =============================================================================
# Orchestrator Class
# =============================================================================

class Orchestrator:
    """
    Central orchestrator managing the AI task pipeline.

    Responsibilities:
        1. Maintain FSM state (IDLE, TEXT, IMAGE, ERROR)
        2. Manage task queue with priority scheduling
        3. Coordinate GPU resource allocation
        4. Handle state transitions with cooldown periods
        5. Dispatch events to registered handlers

    Attributes:
        current_state: Current FSM state
        task_queue: Priority queue for pending tasks
        event_handlers: Registered event callbacks
        services: Dictionary of registered service instances

    Usage:
        orchestrator = Orchestrator()
        await orchestrator.initialize()
        await orchestrator.submit_task(task_event)
        await orchestrator.run()  # Start main event loop
    """

    # Class-level constants
    DEFAULT_COOLDOWN_SECONDS: float = 2.0  # GPU cooldown between state transitions
    MAX_QUEUE_SIZE: int = 100              # Maximum pending tasks
    QUEUE_TIMEOUT: float = 1.0             # Timeout for queue.get()

    # Singleton instance
    _instance: Optional['Orchestrator'] = None

    def __new__(cls):
        """Singleton pattern - ensure only one orchestrator exists."""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    @classmethod
    def get_instance(cls) -> Optional['Orchestrator']:
        """
        Singleton accessor (legacy/extension compatibility).

        Note:
            일부 확장(예: agent_tools.create_image)에서 기대하는 API.
        """
        return cls._instance or cls()

    def __init__(self):
        """
        Initialize the Orchestrator.

        Sets up:
            - Initial state as IDLE
            - Empty task queue
            - Event handler registry
            - Service container
        """
        # Prevent re-initialization on singleton access
        if hasattr(self, '_initialized') and self._initialized:
            return

        self.current_state: SystemState = SystemState.IDLE
        self._task_queue: Optional[asyncio.PriorityQueue] = None
        self._event_handlers: Dict[EventType, List[Callable]] = defaultdict(list)
        self._services: Dict[str, Any] = {}
        self._is_running: bool = False
        self._last_transition_time: Optional[datetime] = None
        self._shutdown_event: Optional[asyncio.Event] = None

        # Metrics tracking
        self._metrics = {
            "tasks_processed": 0,
            "tasks_failed": 0,
            "total_processing_time": 0.0,
            "state_transitions": defaultdict(int),
            "queue_high_water_mark": 0,
            "start_time": None,
            "last_error": None,
        }

        # GPU lock for exclusive access
        self._gpu_lock: Optional[asyncio.Lock] = None

        # Task tracking
        self._active_tasks: Dict[str, TaskEvent] = {}
        self._task_results: Dict[str, Any] = {}

        # LLM + AgentBrain (ReAct loop)
        # - llm_service는 기존 구조상 self._services["llm"]로 등록되므로 별도 참조를 둔다.
        # - agent는 LLM이 등록/연결된 직후 초기화된다.
        self.llm_service: Optional[Any] = None
        self.agent: Optional[AgentBrain] = None

        self._chat_pipeline = ChatPipelineProcessor(self)

        # In-memory session runtime (fast_fallback_used etc.). Lazy TTL cleanup on access.
        self._session_runtime: Dict[str, Dict[str, Any]] = {}
        self._SESSION_RUNTIME_TTL_SECONDS: float = 30 * 60  # 30 minutes

        self._initialized = True
        logger.info("[Orchestrator] Instance created (singleton)")

    def _get_session_runtime_state(self, session_id: str) -> Dict[str, Any]:
        """Get or create session state; lazy TTL cleanup (drop if last_seen older than TTL)."""
        now = time.time()
        entry = self._session_runtime.get(session_id)
        if entry is not None:
            if now - entry.get("last_seen", 0) > self._SESSION_RUNTIME_TTL_SECONDS:
                logger.info("Session runtime expired: %s", session_id)
                try:
                    from mellow_link.core.metrics_collector import get_metrics_collector
                    coll = get_metrics_collector()
                    if coll:
                        coll.push("SESSION_RUNTIME_EXPIRED", 1.0, "count", metric_id=session_id)
                except Exception:
                    pass
                del self._session_runtime[session_id]
                entry = None
        if entry is None:
            entry = {"fast_fallback_used": False, "last_seen": now}
            self._session_runtime[session_id] = entry
        else:
            entry["last_seen"] = now
        return entry

    def _init_agent_if_possible(self) -> None:
        """
        LLM 서비스가 준비되면 AgentBrain을 1회 초기화한다.

        - FSM/락 구조는 그대로 두고, 텍스트 생성의 LLM 호출만 에이전트 루프로 위임하기 위함.
        """
        if self.agent is not None:
            return

        llm = self.llm_service or self._services.get("llm")
        if llm is None:
            return

        self.llm_service = llm
        self.agent = AgentBrain(self.llm_service)
        logger.info("[Orchestrator] AgentBrain initialized")

    async def run_agent(
        self,
        user_input: str,
        history: List[Dict[str, str]],
        is_admin: bool = False,
        mode: str = "fast",
        session_id: Optional[str] = None,
        prompt_category: Optional[str] = None,
        session_state: Optional[Dict[str, Any]] = None,
    ):
        """
        AgentBrain 실행을 Orchestrator 락/수명주기와 함께 제공하는 공개 메서드.

        - mode: "fast" | "thinking" | "research" | "auto". "auto"면 쿼리 기반으로 fast/thinking 선택 (research는 명시 시에만).
        - prompt_category: optional "tool" | "fast" | "thinking" | "research". If "tool" and mode=="auto", selected_mode is never "fast".
        - session_id: 있으면 세션 스코프 런타임(예: fast_fallback_used) 유지.
        - session_state: Optional session state dict (예: {"run_id": "..."}). 제공되면 사용, 없으면 session_id로부터 생성.
        """
        if session_id is not None:
            session_id = str(session_id)
        self._init_agent_if_possible()
        if self.agent is None:
            raise RuntimeError("AgentBrain is not initialized (LLM service missing)")
        if self._gpu_lock is None:
            raise RuntimeError("GPU lock is not initialized (call initialize() first)")

        effective_mode = (mode or "fast").strip().lower()
        if effective_mode == "auto":
            # Use caller-provided prompt_category if given; else detect from query
            if prompt_category is None:
                tool_keywords = [
                    '파일', '폴더', '경로', '읽어', '써', '저장', '삭제', '업로드', '문서', '인덱싱', '검색', 'rag',
                    'file', 'folder', 'path', 'read', 'write', 'save', 'delete', 'upload', 'document', 'index', 'search', 'rag',
                    '도구', 'tool', '실행', 'execute', '호출', 'call'
                ]
                user_input_lower = user_input.lower()
                if any(keyword in user_input_lower for keyword in tool_keywords):
                    prompt_category = "tool"
            effective_mode = self._chat_pipeline._select_mode_for_query(user_input, prompt_category=prompt_category)
            logger.debug(f"AUTO_MODE_DECISION category={prompt_category} selected={effective_mode}")
        else:
            logger.debug(f"AUTO_MODE_DECISION category={prompt_category} selected={effective_mode}")

        # Session-scoped state for fallback etc. (in-memory; lazy TTL 30min)
        # session_state가 제공되면 사용, 없으면 session_id로부터 생성
        if session_state is None:
            session_state = None
            if session_id:
                session_state = self._get_session_runtime_state(session_id)
                session_state["session_id"] = session_id
        elif session_id and "session_id" not in session_state:
            # session_state가 제공되었지만 session_id가 없으면 추가
            session_state["session_id"] = session_id

        # 1. 모드에 따라 파일명 결정
        filename = "aventurine_persona_v1.txt" if is_admin else "default_system_prompt.txt"

        # 2. 파일에서 텍스트 로딩
        target_persona = load_persona_from_file(filename)

        # 3. Observation strict: only in thinking/research (env MELLOW_OBSERVATION_STRICT_MODES)
        require_observation = False
        try:
            from mellow_link.config import get_settings
            s = get_settings()
            strict_modes_str = getattr(s, "observation_strict_modes", "thinking,research") or "thinking,research"
            strict_modes = [m.strip().lower() for m in strict_modes_str.split(",") if m.strip()]
            require_observation = effective_mode in strict_modes
        except Exception:
            strict_modes = ["thinking", "research"]
            require_observation = effective_mode in strict_modes

        async with self._gpu_lock:
            return await self.agent.run(
                user_input,
                context=history,
                persona=target_persona,
                require_at_least_one_tool=require_observation,
                mode=effective_mode,
                session_id=session_id,
                session_state=session_state,
                is_admin=is_admin,
            )

    async def initialize(self) -> None:
        """
        Async initialization of orchestrator components.

        Steps:
            1. Initialize asyncio queues
            2. Connect to services (LLM, Image, Document)
            3. Start VRAM watchdog
            4. Verify all dependencies are available

        Raises:
            RuntimeError: If critical services fail to initialize
        """
        logger.info("[Orchestrator] Initializing...")

        # Initialize asyncio primitives
        self._task_queue = asyncio.PriorityQueue(maxsize=self.MAX_QUEUE_SIZE)
        self._shutdown_event = asyncio.Event()
        self._gpu_lock = asyncio.Lock()

        # Record start time
        self._metrics["start_time"] = datetime.now()

        # Try to connect to services
        await self._connect_services()

        # LLM 서비스가 연결된 직후 에이전트 초기화
        self._init_agent_if_possible()

        # Emit initialization event
        await self.emit_event(Event(
            event_type=EventType.STATE_CHANGE,
            payload={"action": "initialize", "state": self.current_state.name},
            source="orchestrator"
        ))

        logger.info("[Orchestrator] Initialization complete")

    async def _connect_services(self) -> None:
        """Attempt to connect to registered services."""
        for name, service in self._services.items():
            try:
                if hasattr(service, 'connect'):
                    await service.connect()
                    logger.info(f"[Orchestrator] Service '{name}' connected")
                elif hasattr(service, 'initialize'):
                    await service.initialize()
                    logger.info(f"[Orchestrator] Service '{name}' initialized")
            except Exception as e:
                logger.warning(f"[Orchestrator] Service '{name}' connection failed: {e}")

    async def shutdown(self) -> None:
        """
        Graceful shutdown of the orchestrator.

        Steps:
            1. Stop accepting new tasks
            2. Wait for current task to complete (with timeout)
            3. Flush remaining queue (optional: save to disk)
            4. Disconnect all services
            5. Stop VRAM watchdog
        """
        logger.info("[Orchestrator] Initiating shutdown...")

        # Signal shutdown
        self._is_running = False
        if self._shutdown_event:
            self._shutdown_event.set()

        # Wait for current task with timeout
        shutdown_timeout = 30.0
        try:
            await asyncio.wait_for(self._perform_shutdown(), timeout=shutdown_timeout)
        except asyncio.TimeoutError:
            logger.error("[Orchestrator] Shutdown timed out!")
            
        # Disconnect services
        await self._disconnect_services()

    async def _perform_shutdown(self) -> None:
        """
        Perform the actual shutdown operations.

        - Wait for active tasks to complete
        - Drain the task queue
        """
        # Wait for active tasks to complete
        if self._active_tasks:
            logger.info(f"[Orchestrator] Waiting for {len(self._active_tasks)} active tasks to complete...")
            max_wait = 10.0  # seconds
            wait_interval = 0.5
            elapsed = 0.0
            while self._active_tasks and elapsed < max_wait:
                await asyncio.sleep(wait_interval)
                elapsed += wait_interval

            if self._active_tasks:
                logger.warning(f"[Orchestrator] {len(self._active_tasks)} tasks still active after timeout")

        # Drain remaining tasks from queue
        if self._task_queue:
            remaining = self._task_queue.qsize()
            if remaining > 0:
                logger.info(f"[Orchestrator] Draining {remaining} tasks from queue...")
                while not self._task_queue.empty():
                    try:
                        self._task_queue.get_nowait()
                        self._task_queue.task_done()
                    except asyncio.QueueEmpty:
                        break

        logger.info("[Orchestrator] Shutdown operations completed")

    async def _disconnect_services(self) -> None:
        """Disconnect all registered services."""
        for name, service in self._services.items():
            try:
                if hasattr(service, 'disconnect'):
                    await service.disconnect()
                elif hasattr(service, 'shutdown'):
                    await service.shutdown()
                logger.info(f"[Orchestrator] Service '{name}' disconnected")
            except Exception as e:
                logger.error(f"[Orchestrator] Error disconnecting service '{name}': {e}")

        # Emit shutdown event
        await self.emit_event(Event(
            event_type=EventType.SHUTDOWN,
            payload={"reason": "graceful_shutdown"},
            source="orchestrator"
        ))

        # Transition to IDLE
        self.current_state = SystemState.IDLE

        logger.info("[Orchestrator] Shutdown complete")

    # ==================== State Management ====================

    def get_state(self) -> SystemState:
        """
        Get the current FSM state.

        Returns:
            Current SystemState enum value
        """
        return self.current_state

    async def request_state_change(
        self,
        target_state: SystemState,
        reason: str = "",
        force: bool = False
    ) -> TransitionResult:
        """
        Request a state transition.

        Args:
            target_state: Desired new state
            reason: Human-readable reason for transition
            force: If True, skip cooldown check (use carefully)

        Returns:
            TransitionResult indicating success or failure reason

        State Transition Rules:
            - IDLE can transition to TEXT or IMAGE
            - TEXT can transition to IDLE or IMAGE (with cooldown)
            - IMAGE can transition to IDLE or TEXT (with cooldown)
            - Any state can transition to ERROR
            - ERROR can only transition to IDLE
        """
        previous_state = self.current_state

        # Validate transition
        if not self._is_valid_transition(previous_state, target_state):
            logger.warning(
                f"[Orchestrator] Invalid transition: {previous_state.name} -> {target_state.name}"
            )
            return TransitionResult.INVALID_TRANSITION

        # Check cooldown (unless forced or transitioning to/from ERROR)
        if not force and target_state != SystemState.ERROR and previous_state != SystemState.ERROR:
            cooldown_ok = await self._check_cooldown()
            if not cooldown_ok:
                logger.debug(
                    f"[Orchestrator] Cooldown active for {previous_state.name} -> {target_state.name}"
                )
                return TransitionResult.COOLDOWN_ACTIVE

        # Perform transition
        try:
            # ── VRAM_MANAGEMENT: 상태 전환 시 이전 상태의 모델 언로드 ──
            # 벤치마크 모드 확인 (한 번만 확인)
            should_unload = True
            try:
                from mellow_link.config import get_settings
                settings = get_settings()
                should_unload = getattr(settings, "enable_model_unload_on_idle", True)
            except Exception:
                # 설정 로드 실패 시 환경 변수 직접 확인
                import os
                env_value = os.getenv("ENABLE_MODEL_UNLOAD_ON_IDLE", "").strip().lower()
                if env_value in {"0", "false", "no", "off", "disabled"}:
                    should_unload = False
            
            # IMAGE -> TEXT/IDLE: 이미지 모델 언로드 (벤치마크 모드에서는 비활성화 가능)
            if previous_state == SystemState.IMAGE and target_state != SystemState.IMAGE:
                if should_unload:
                    image_service = self._services.get("image")
                    if image_service and hasattr(image_service, "unload_model"):
                        try:
                            logger.info("[Orchestrator] IMAGE -> 다른 상태 전환: 이미지 모델 언로드 시작")
                            unload_success = await image_service.unload_model()
                            if unload_success:
                                logger.info("[Orchestrator] 이미지 모델 언로드 완료 (VRAM 해제)")
                            else:
                                logger.warning("[Orchestrator] 이미지 모델 언로드 실패 (VRAM이 계속 사용 중일 수 있음)")
                        except Exception as unload_error:
                            logger.error(f"[Orchestrator] 이미지 모델 언로드 중 오류: {unload_error}")
                else:
                    logger.debug("[Orchestrator] 벤치마크 모드: IDLE 전환 시 이미지 모델 언로드 건너뜀 (모델 유지)")
            
            # TEXT -> IMAGE/IDLE: LLM 모델 언로드 (벤치마크 모드에서는 비활성화 가능)
            if previous_state == SystemState.TEXT and target_state != SystemState.TEXT:
                if should_unload:
                    llm_service = self._services.get("llm")
                    if llm_service and hasattr(llm_service, "unload_model"):
                        try:
                            logger.info("[Orchestrator] TEXT -> 다른 상태 전환: LLM 모델 언로드 시작")
                            unload_success = await llm_service.unload_model()
                            if unload_success:
                                logger.info("[Orchestrator] LLM 모델 언로드 완료 (VRAM 해제)")
                            else:
                                logger.warning("[Orchestrator] LLM 모델 언로드 실패 (VRAM이 계속 사용 중일 수 있음)")
                        except Exception as unload_error:
                            logger.error(f"[Orchestrator] LLM 모델 언로드 중 오류: {unload_error}")
                else:
                    logger.debug("[Orchestrator] 벤치마크 모드: IDLE 전환 시 LLM 모델 언로드 건너뜀 (모델 유지)")
            
            # 가비지 컬렉션 강제 실행 (모델 언로드 후)
            if previous_state != target_state and previous_state != SystemState.IDLE:
                try:
                    import gc
                    collected = gc.collect()
                    logger.debug(f"[Orchestrator] 상태 전환 후 GC 실행: {collected}개 객체 해제")
                except Exception as gc_error:
                    logger.debug(f"[Orchestrator] GC 실행 실패 (무시): {gc_error}")
            
            self.current_state = target_state
            self._last_transition_time = datetime.now()

            # Track metrics
            transition_key = f"{previous_state.name}_to_{target_state.name}"
            self._metrics["state_transitions"][transition_key] += 1

            # Emit state change event
            await self.emit_event(StateChangeEvent(
                event_type=EventType.STATE_CHANGE,
                previous_state=previous_state,
                new_state=target_state,
                transition_reason=reason,
                source="orchestrator"
            ))

            logger.info(
                f"[Orchestrator] State transition: {previous_state.name} -> {target_state.name} "
                f"(reason: {reason or 'none'})"
            )
            return TransitionResult.SUCCESS

        except Exception as e:
            logger.error(f"[Orchestrator] State transition error: {e}")
            self._metrics["last_error"] = str(e)
            return TransitionResult.ERROR

    async def force_state_change(
        self,
        target_state: SystemState,
        reason: str = "",
        run_id: Optional[str] = None,
    ) -> TransitionResult:
        """
        Request state transition ignoring cooldown (FINALLY → FORCE IDLE policy).

        Used in finally blocks to guarantee IDLE recovery regardless of cooldown.
        On failure, logs and emits an event for diagnostics.

        Args:
            target_state: Desired state (typically SystemState.IDLE)
            reason: Human-readable reason
            run_id: Optional task_id/run_id for logging

        Returns:
            TransitionResult (SUCCESS or failure reason)
        """
        previous_state = self.current_state
        result = await self.request_state_change(
            target_state, reason=reason, force=True
        )
        if not result.is_success():
            logger.error(
                "FSM IDLE recovery failed: previous_state=%s current_state=%s "
                "failure_reason=%s run_id=%s",
                previous_state.name,
                self.current_state.name,
                result.name,
                run_id or "n/a",
            )
            await self.emit_event(
                Event(
                    event_type=EventType.ERROR,
                    payload={
                        "kind": "fsm_idle_recovery_failed",
                        "previous_state": previous_state.name,
                        "current_state": self.current_state.name,
                        "failure_reason": result.name,
                        "run_id": run_id,
                        "reason": reason,
                    },
                    source="orchestrator",
                )
            )
        return result

    def _is_valid_transition(
        self,
        from_state: SystemState,
        to_state: SystemState
    ) -> bool:
        """
        Validate if a state transition is allowed.

        Args:
            from_state: Current state
            to_state: Target state

        Returns:
            True if transition is valid according to FSM rules
        """
        if from_state == to_state:
            return True  # No-op transitions are valid

        valid_targets = VALID_TRANSITIONS.get(from_state, set())
        return to_state in valid_targets

    async def _check_cooldown(self) -> bool:
        """
        Check if GPU cooldown period has elapsed.

        Returns:
            True if cooldown has passed, False if still waiting

        Note:
            Cooldown prevents rapid GPU context switching which
            can cause instability and memory fragmentation.
        """
        if self._last_transition_time is None:
            return True

        elapsed = datetime.now() - self._last_transition_time
        cooldown_delta = timedelta(seconds=self.DEFAULT_COOLDOWN_SECONDS)

        return elapsed >= cooldown_delta

    # ==================== Task Management ====================

    async def submit_task(self, task: TaskEvent) -> str:
        """
        Submit a new task to the processing queue.

        Args:
            task: TaskEvent containing task details

        Returns:
            Task ID for tracking

        Raises:
            QueueFullError: If queue is at maximum capacity
            InvalidTaskError: If task validation fails
        """
        if not self._task_queue:
            raise RuntimeError("Orchestrator not initialized")

        if self._task_queue.qsize() >= self.MAX_QUEUE_SIZE:
            raise RuntimeError(f"Task queue full (max: {self.MAX_QUEUE_SIZE})")

        # Set event type if not set
        if task.event_type is None:
            task.event_type = EventType.TASK_SUBMIT

        # Add to queue with priority (lower number = higher priority)
        priority_value = task.priority.value if task.priority else TaskPriority.NORMAL.value
        await self._task_queue.put((priority_value, task.timestamp, task))

        # Track active task
        self._active_tasks[task.task_id] = task

        # Update high water mark
        current_size = self._task_queue.qsize()
        if current_size > self._metrics["queue_high_water_mark"]:
            self._metrics["queue_high_water_mark"] = current_size

        # Emit submit event
        await self.emit_event(Event(
            event_type=EventType.TASK_SUBMIT,
            payload={"task_id": task.task_id, "task_type": task.task_type},
            source="orchestrator"
        ))

        logger.info(
            f"[Orchestrator] Task submitted: {task.task_id} "
            f"(type: {task.task_type}, priority: {task.priority.name})"
        )

        return task.task_id

    async def cancel_task(self, task_id: str) -> bool:
        """
        Cancel a pending or running task.

        Args:
            task_id: ID of task to cancel

        Returns:
            True if task was found and cancelled

        Note:
            Running tasks may not be immediately cancellable
            depending on the service implementation.
        """
        if task_id in self._active_tasks:
            task = self._active_tasks.pop(task_id)

            # Emit cancel event
            await self.emit_event(Event(
                event_type=EventType.TASK_CANCEL,
                payload={"task_id": task_id},
                source="orchestrator"
            ))

            logger.info(f"[Orchestrator] Task cancelled: {task_id}")
            return True

        return False

    async def get_task_status(self, task_id: str) -> Optional[Dict[str, Any]]:
        """
        Get the current status of a task.

        Args:
            task_id: ID of task to query

        Returns:
            Dict with task status, or None if not found
        """
        if task_id in self._active_tasks:
            task = self._active_tasks[task_id]
            return {
                "task_id": task_id,
                "status": "pending" if task_id not in self._task_results else "completed",
                "task_type": task.task_type,
                "priority": task.priority.name,
                "submitted_at": task.timestamp.isoformat(),
            }

        if task_id in self._task_results:
            return {
                "task_id": task_id,
                "status": "completed",
                "result": self._task_results[task_id],
            }

        return None

    async def _process_task(self, task: TaskEvent) -> None:
        """
        Process a single task through the appropriate service.

        Args:
            task: TaskEvent to process

        Flow:
            1. Determine required state for task type
            2. Request state transition
            3. Dispatch to appropriate service
            4. Wait for completion
            5. Emit completion event
            6. Transition back to IDLE
        """
        start_time = time.time()
        task_id = task.task_id

        try:
            # Emit start event
            await self.emit_event(Event(
                event_type=EventType.TASK_START,
                payload={"task_id": task_id, "task_type": task.task_type},
                source="orchestrator"
            ))

            # Determine target state based on task type
            target_state = self._get_state_for_task(task.task_type)

            # Request state transition
            if target_state != self.current_state:
                result = await self.request_state_change(
                    target_state,
                    reason=f"Processing task {task_id}"
                )
                if result != TransitionResult.SUCCESS:
                    raise RuntimeError(f"State transition failed: {result.name}")

            # Acquire GPU lock for GPU tasks
            if task.is_gpu_task():
                async with self._gpu_lock:
                    await self._execute_task(task)
            else:
                await self._execute_task(task)

            # Calculate processing time
            elapsed = time.time() - start_time
            self._metrics["tasks_processed"] += 1
            self._metrics["total_processing_time"] += elapsed

            # Store result
            self._task_results[task_id] = task.result_data

            # Emit completion event
            await self.emit_event(Event(
                event_type=EventType.TASK_COMPLETE,
                payload={
                    "task_id": task_id,
                    "processing_time": elapsed,
                    "result_preview": str(task.result_data)[:100] if task.result_data else None
                },
                source="orchestrator"
            ))

            logger.info(f"[Orchestrator] Task completed: {task_id} ({elapsed:.2f}s)")

        except Exception as e:
            self._metrics["tasks_failed"] += 1
            self._metrics["last_error"] = str(e)
            task.error_message = str(e)

            # Emit failure event
            await self.emit_event(Event(
                event_type=EventType.TASK_FAILED,
                payload={"task_id": task_id, "error": str(e)},
                source="orchestrator"
            ))

            logger.error(f"[Orchestrator] Task failed: {task_id} - {e}")

        finally:
            # Remove from active tasks
            self._active_tasks.pop(task_id, None)

            # FINALLY → FORCE IDLE: cooldown 무시하고 항상 IDLE 복귀 시도
            if self.current_state != SystemState.IDLE:
                await self.force_state_change(
                    SystemState.IDLE,
                    reason=f"Task {task_id} completed",
                    run_id=task_id,
                )

    def _get_state_for_task(self, task_type: str) -> SystemState:
        """Map task type to required system state."""
        mapping = {
            "llm": SystemState.TEXT,
            "chat": SystemState.TEXT,
            "text": SystemState.TEXT,
            "image": SystemState.IMAGE,
            "comfyui": SystemState.IMAGE,
            "document": SystemState.IDLE,  # CPU-based, no state change needed
        }
        return mapping.get(task_type.lower(), SystemState.IDLE)

    async def _execute_task(self, task: TaskEvent) -> None:
        """
        Execute task through the appropriate service.

        Args:
            task: TaskEvent to execute
        """
        service = self._services.get(task.task_type)

        if service is None:
            # Try generic service lookup
            service = self._services.get("default")

        if service is None:
            raise RuntimeError(f"No service registered for task type: {task.task_type}")

        # Execute through service
        if hasattr(service, 'execute'):
            task.result_data = await service.execute(task.request_data)
        elif hasattr(service, 'process'):
            task.result_data = await service.process(task.request_data)
        elif hasattr(service, 'generate'):
            task.result_data = await service.generate(**task.request_data)
        else:
            raise RuntimeError(f"Service '{task.task_type}' has no execute method")

    # ==================== Chat Processing Pipeline ====================

    async def process_chat(
        self,
        context: ChatContext,
        rag_search_fn: Optional[Callable] = None,
        llm_generate_fn: Optional[Callable] = None
    ) -> ChatContext:
        """
        Process a chat request through the full pipeline.

        ANALYZING -> RETRIEVING (optional) -> GENERATING -> GENERATING_RESPONSE -> COMPLETED

        Args:
            context: ChatContext with request data
            rag_search_fn: Function to search RAG documents
            llm_generate_fn: Function to generate LLM response

        Returns:
            Updated ChatContext with response
        """
        return await self._chat_pipeline.process_chat(context, rag_search_fn, llm_generate_fn)

    async def process_chat_stream(
        self,
        context: ChatContext,
        rag_search_fn: Optional[Callable] = None,
        llm_stream_fn: Optional[Callable] = None
    ) -> AsyncGenerator[str, None]:
        """
        Process chat with streaming response.

        Args:
            context: ChatContext with request data
            rag_search_fn: Function to search RAG documents
            llm_stream_fn: Async generator function for streaming LLM response

        Yields:
            Text chunks from LLM response
        """
        async for chunk in self._chat_pipeline.process_chat_stream(
            context, rag_search_fn, llm_stream_fn
        ):
            yield chunk

    # ==================== Event System ====================

    def register_handler(
        self,
        event_type: EventType,
        handler: Callable[[Event], Any]
    ) -> None:
        """
        Register an event handler callback.

        Args:
            event_type: Type of events to handle
            handler: Async callable to invoke on event

        Note:
            Multiple handlers can be registered for same event type.
            Handlers are called in registration order.
        """
        self._event_handlers[event_type].append(handler)
        logger.debug(f"[Orchestrator] Handler registered for {event_type.name}")

    def unregister_handler(
        self,
        event_type: EventType,
        handler: Callable[[Event], Any]
    ) -> bool:
        """
        Remove a previously registered event handler.

        Args:
            event_type: Type of events
            handler: Handler to remove

        Returns:
            True if handler was found and removed
        """
        handlers = self._event_handlers.get(event_type, [])
        if handler in handlers:
            handlers.remove(handler)
            logger.debug(f"[Orchestrator] Handler unregistered for {event_type.name}")
            return True
        return False

    async def emit_event(self, event: Event) -> None:
        """
        Emit an event to all registered handlers.

        Args:
            event: Event to broadcast

        Note:
            Events are processed asynchronously.
            Handler exceptions are logged but don't stop propagation.
        """
        handlers = self._event_handlers.get(event.event_type, [])

        for handler in handlers:
            try:
                if asyncio.iscoroutinefunction(handler):
                    await handler(event)
                else:
                    handler(event)
            except Exception as e:
                logger.error(
                    f"[Orchestrator] Event handler error for {event.event_type.name}: {e}"
                )

    # ==================== Service Management ====================

    def register_service(self, name: str, service: Any) -> None:
        """
        Register a service instance with the orchestrator.

        Args:
            name: Service identifier (e.g., 'llm', 'image', 'document')
            service: Service instance implementing required interface
        """
        self._services[name] = service
        if name == "llm":
            self.llm_service = service
            self._init_agent_if_possible()
        logger.info(f"[Orchestrator] Service registered: {name}")

    def get_service(self, name: str) -> Optional[Any]:
        """
        Retrieve a registered service by name.

        Args:
            name: Service identifier

        Returns:
            Service instance or None if not found
        """
        return self._services.get(name)

    # ==================== Main Loop ====================

    async def run(self) -> None:
        """
        Main event loop for the orchestrator.

        This is the primary async loop that:
            1. Monitors the task queue
            2. Processes tasks according to priority
            3. Handles state transitions
            4. Responds to system events

        Should be run as the main coroutine:
            asyncio.run(orchestrator.run())
        """
        if not self._task_queue:
            await self.initialize()

        self._is_running = True
        logger.info("[Orchestrator] Main loop started")

        try:
            await self._main_loop()
        except asyncio.CancelledError:
            logger.info("[Orchestrator] Main loop cancelled")
        except Exception as e:
            logger.error(f"[Orchestrator] Main loop error: {e}", exc_info=True)
            await self.request_state_change(SystemState.ERROR, reason=str(e))
        finally:
            await self.shutdown()

    async def _main_loop(self) -> None:
        """
        Internal main loop implementation.

        Separated from run() to allow for setup/teardown.

        Loop Steps:
            1. Check for shutdown signal
            2. Get next task from queue (with timeout)
            3. Process task if available
            4. Yield control to other coroutines
        """
        while self._is_running:
            # Check shutdown signal
            if self._shutdown_event and self._shutdown_event.is_set():
                break

            try:
                # Get next task with timeout
                priority, timestamp, task = await asyncio.wait_for(
                    self._task_queue.get(),
                    timeout=self.QUEUE_TIMEOUT
                )

                # Process the task
                await self._process_task(task)

                # Mark task as done
                self._task_queue.task_done()

            except asyncio.TimeoutError:
                # No task available, yield control
                await asyncio.sleep(0.01)
            except Exception as e:
                logger.error(f"[Orchestrator] Loop iteration error: {e}")
                await asyncio.sleep(0.1)

    # ==================== Health & Monitoring ====================

    async def health_check(self) -> Dict[str, Any]:
        """
        Perform system health check.

        Returns:
            Dict containing:
                - state: Current FSM state
                - queue_size: Number of pending tasks
                - services_status: Health of each service
                - uptime: Time since initialization
                - last_error: Most recent error (if any)
        """
        # Calculate uptime
        uptime_seconds = 0
        if self._metrics["start_time"]:
            uptime_seconds = (datetime.now() - self._metrics["start_time"]).total_seconds()

        # Check services
        services_status = {}
        for name, service in self._services.items():
            try:
                if hasattr(service, 'health_check'):
                    services_status[name] = await service.health_check()
                elif hasattr(service, 'is_available'):
                    services_status[name] = {"available": service.is_available()}
                else:
                    services_status[name] = {"status": "unknown"}
            except Exception as e:
                services_status[name] = {"status": "error", "error": str(e)}

        return {
            "state": self.current_state.name,
            "is_running": self._is_running,
            "queue_size": self._task_queue.qsize() if self._task_queue else 0,
            "active_tasks": len(self._active_tasks),
            "services_status": services_status,
            "uptime_seconds": uptime_seconds,
            "last_error": self._metrics["last_error"],
            "gpu_lock_held": self._gpu_lock.locked() if self._gpu_lock else False,
        }

    def get_metrics(self) -> Dict[str, Any]:
        """
        Get orchestrator performance metrics.

        Returns:
            Dict containing:
                - tasks_processed: Total tasks completed
                - tasks_failed: Total tasks failed
                - avg_processing_time: Average task duration
                - state_transitions: Count by transition type
                - queue_high_water_mark: Max queue size reached
        """
        total = self._metrics["tasks_processed"]
        avg_time = (
            self._metrics["total_processing_time"] / total
            if total > 0 else 0.0
        )

        return {
            "tasks_processed": total,
            "tasks_failed": self._metrics["tasks_failed"],
            "avg_processing_time": round(avg_time, 3),
            "state_transitions": dict(self._metrics["state_transitions"]),
            "queue_high_water_mark": self._metrics["queue_high_water_mark"],
        }


# =============================================================================
# Factory Function (compatibility with legacy create_state_machine)
# =============================================================================

def create_chat_context(
    user_query: str,
    system_prompt: str = "",
    use_rag: bool = False,
    rag_collection_name: Optional[str] = None,
    user_memories: Optional[List[str]] = None,
    session_history: Optional[List[Dict[str, str]]] = None,
    mode: str = "thinking"
) -> ChatContext:
    """
    Factory function to create ChatContext.

    Provides compatibility with legacy create_state_machine() API.

    Args:
        user_query: User's question
        system_prompt: System prompt for the folder/agent
        use_rag: Whether RAG is enabled
        rag_collection_name: Collection name for RAG search
        user_memories: List of user memory strings
        session_history: List of previous messages
        mode: Processing mode (fast, thinking, research, auto)

    Returns:
        ChatContext instance ready for processing
    """
    return ChatContext(
        user_query=user_query,
        system_prompt=system_prompt,
        use_rag=use_rag,
        rag_collection_name=rag_collection_name,
        user_memories=user_memories or [],
        session_history=session_history or [],
        mode=mode
    )


def get_orchestrator() -> Orchestrator:
    """
    Get the singleton Orchestrator instance.

    Returns:
        The global Orchestrator instance
    """
    return Orchestrator()
