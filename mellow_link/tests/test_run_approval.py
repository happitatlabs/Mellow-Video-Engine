"""
Operator 승인 플로우: PolicyGuardian NEED_AI_REVIEW 시 run 대기 → approve/reject 재개.
"""
import threading
import unittest
from unittest.mock import patch, MagicMock

from mellow_link.infra.run_events import (
    create_run,
    emit_event,
    get_run_events,
    get_run_snapshot,
)
from mellow_link.infra.run_approval import (
    set_pending_and_wait,
    resolve_approval,
    get_pending_approval,
    RUN_APPROVAL_STATE,
)
from mellow_link.infra.run_context import set_run_context, get_run_id, get_current_todo_id


class TestRunContext(unittest.TestCase):
    def setUp(self):
        set_run_context(None, None)

    def tearDown(self):
        set_run_context(None, None)

    def test_set_and_get_run_context(self):
        set_run_context("run-123", "T3")
        self.assertEqual(get_run_id(), "run-123")
        self.assertEqual(get_current_todo_id(), "T3")
        set_run_context("run-456", None)
        self.assertEqual(get_run_id(), "run-456")
        self.assertIsNone(get_current_todo_id())


class TestRunApprovalWaitResolve(unittest.TestCase):
    def tearDown(self):
        RUN_APPROVAL_STATE.clear()

    def test_resolve_approval_approved_wakes_waiter(self):
        result_holder = []

        def waiter():
            r = set_pending_and_wait(
                run_id="test-run-approve",
                todo_id="T3",
                audit_type="tool_code",
                file_path=None,
                critique="NEED_AI_REVIEW",
                risk_level=2,
                risk_score=50,
                db=None,
            )
            result_holder.append(r)

        t = threading.Thread(target=waiter)
        t.start()
        # 잠시 후 승인
        t.join(timeout=0.5)
        if not t.is_alive():
            self.fail("Waiter finished too early (no approval sent)")
        ok = resolve_approval("test-run-approve", approved=True)
        self.assertTrue(ok)
        t.join(timeout=2.0)
        self.assertFalse(t.is_alive())
        self.assertEqual(result_holder, ["approved"])

    def test_resolve_approval_rejected_wakes_waiter(self):
        result_holder = []

        def waiter():
            r = set_pending_and_wait(
                run_id="test-run-reject",
                todo_id="T3",
                audit_type="tool_code",
                file_path=None,
                critique="NEED_AI_REVIEW",
                risk_level=2,
                risk_score=50,
                db=None,
            )
            result_holder.append(r)

        t = threading.Thread(target=waiter)
        t.start()
        t.join(timeout=0.5)
        if not t.is_alive():
            self.fail("Waiter finished too early")
        ok = resolve_approval("test-run-reject", approved=False, reason="Operator 거부")
        self.assertTrue(ok)
        t.join(timeout=2.0)
        self.assertFalse(t.is_alive())
        self.assertEqual(result_holder, ["rejected"])

    def test_resolve_approval_no_pending_returns_false(self):
        ok = resolve_approval("nonexistent-run", approved=True)
        self.assertFalse(ok)


class TestSnapshotNeedsApproval(unittest.TestCase):
    """approval_required 이벤트가 있으면 스냅샷에 needs_approval, approval_required 포함."""

    def setUp(self):
        from mellow_link.infra.database import SessionLocal
        self.db = SessionLocal()

    def tearDown(self):
        self.db.close()

    def test_snapshot_has_needs_approval_and_approval_required(self):
        run_id = create_run(session_id=None, db=self.db)
        emit_event(run_id, "run_started", {"user_input": "test"}, db=self.db)
        emit_event(
            run_id,
            "approval_required",
            {
                "todo_id": "T3",
                "audit_type": "tool_code",
                "file_path": None,
                "critique": "PolicyGuardian: NEED_AI_REVIEW (level=2). Operator 또는 AIGuardian 승인 필요.",
                "risk_level": 2,
                "risk_score": 50,
            },
            db=self.db,
        )
        snapshot = get_run_snapshot(run_id, db=self.db)
        self.assertIsNotNone(snapshot)
        self.assertTrue(snapshot.get("needs_approval"), "needs_approval should be True")
        ar = snapshot.get("approval_required")
        self.assertIsNotNone(ar)
        self.assertEqual(ar.get("audit_type"), "tool_code")
        self.assertEqual(ar.get("todo_id"), "T3")
        self.assertEqual(ar.get("risk_level"), 2)
        self.assertEqual(ar.get("risk_score"), 50)
        self.assertIn("NEED_AI_REVIEW", (ar.get("critique") or ""))
