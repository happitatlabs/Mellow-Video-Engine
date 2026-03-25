from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from types import SimpleNamespace

from mellow_chat_runtime import app_state as runtime_app_state
from mellow_link.core.agent_experience import ExperienceHelper
from mellow_link.core.agent_schemas import AgentResult
from mellow_link.core.scheduler_service import SchedulerService
from mellow_link.infra.memory_database import ScheduledTask


class _FailingArchiver:
    async def archive(self, task_data):
        raise RuntimeError("archive backend unavailable")


class _SuccessArchiver:
    async def archive(self, task_data):
        return "exp_001"


class _FakeSchedulerDb:
    def __init__(self, existing_tasks=None):
        self.tasks = list(existing_tasks or [])
        self.added = []
        self.updated = []

    def get_all_scheduled_tasks(self, status=None):
        if status is None:
            return list(self.tasks)
        return [task for task in self.tasks if task.status == status]

    def add_scheduled_task(self, task):
        self.added.append(task)
        self.tasks.append(task)
        return True

    def get_pending_tasks(self):
        now = datetime.now()
        return [
            task for task in self.tasks
            if task.status == "ENABLED" and task.next_run_at <= now
        ]

    def update_task_result(self, task_id, **kwargs):
        self.updated.append((task_id, kwargs))
        for task in self.tasks:
            if task.id == task_id:
                for key, value in kwargs.items():
                    setattr(task, key, value)
        return True


class _FakeAgentBrain:
    async def run(self, **kwargs):
        return SimpleNamespace(finish_reason="finish_tool", total_turns=1)


def test_memory_archiving_failure_does_not_raise_or_flip_runtime_degraded(caplog):
    async def run():
        await runtime_app_state.reset_runtime_state_for_tests()
        helper = ExperienceHelper(
            archiver=_FailingArchiver(),
            enable_memory_archiving=True,
        )
        result = AgentResult(answer="ok", steps=[], total_turns=1, finish_reason="finish_tool")
        with caplog.at_level(logging.WARNING):
            await helper.archive_experience(
                user_input="hello",
                context_summary="ctx",
                result=result,
                start_time=datetime.now() - timedelta(milliseconds=5),
            )
        return await runtime_app_state.get_runtime_health_snapshot()

    snapshot = asyncio.run(run())
    assert snapshot["degraded"] is False
    assert snapshot["last_error"] is None
    assert "archive_experience_failed" in caplog.text


def test_memory_archiving_success_logs_experience_id(caplog):
    async def run():
        helper = ExperienceHelper(
            archiver=_SuccessArchiver(),
            enable_memory_archiving=True,
        )
        result = AgentResult(answer="ok", steps=[], total_turns=1, finish_reason="finish_tool")
        with caplog.at_level(logging.INFO):
            await helper.archive_experience(
                user_input="hello",
                context_summary="ctx",
                result=result,
                start_time=datetime.now() - timedelta(milliseconds=5),
            )

    asyncio.run(run())
    assert "experience_archived experience_id=exp_001" in caplog.text


def test_scheduler_start_guard_blocks_duplicate_service_start(caplog):
    async def run():
        svc = SchedulerService(db=_FakeSchedulerDb(), agent_brain=None, check_interval_seconds=60)

        async def fake_run_loop():
            while svc._is_running:
                await asyncio.sleep(0.01)

        svc._run_loop = fake_run_loop
        with caplog.at_level(logging.WARNING):
            await svc.start()
            await svc.start()
            await svc.stop()

    asyncio.run(run())
    assert "Already running" in caplog.text


def test_scheduler_registration_skips_duplicate_diagnosis_task():
    existing = ScheduledTask(
        id="task_existing",
        task_name="성능 자가 진단",
        task_type="diagnosis_task",
        schedule_expr="86400",
        args_json="{}",
        next_run_at=datetime.now() + timedelta(hours=1),
        status="ENABLED",
        created_at=datetime.now(),
    )
    db = _FakeSchedulerDb(existing_tasks=[existing])
    svc = SchedulerService(db=db, agent_brain=None)

    svc._register_diagnosis_task()

    assert db.added == []


def test_scheduler_execute_task_emits_tick_log(caplog):
    async def run():
        db = _FakeSchedulerDb()
        svc = SchedulerService(db=db, agent_brain=_FakeAgentBrain())
        task = ScheduledTask(
            id="task_tick",
            task_name="샘플 에이전트 태스크",
            task_type="agent_task",
            schedule_expr="60",
            args_json='{"user_input":"check"}',
            next_run_at=datetime.now() - timedelta(seconds=1),
            status="ENABLED",
            created_at=datetime.now(),
        )
        db.tasks.append(task)
        with caplog.at_level(logging.INFO):
            await svc._execute_task(task)

    asyncio.run(run())
    assert "Executing task: 샘플 에이전트 태스크 (task_tick)" in caplog.text
    assert "Task completed: 샘플 에이전트 태스크" in caplog.text
