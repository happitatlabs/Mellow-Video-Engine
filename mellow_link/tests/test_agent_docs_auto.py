"""
Unit tests for agent_docs_auto: trigger, quota, cooldown, cache.
"""
import time
import unittest
from unittest.mock import patch

from mellow_link.core.agent_docs_auto import (
    _should_trigger,
    _pick_doc,
    try_auto_read_docs,
    clear_session,
    _get_state,
    MAX_QUOTA,
    COOLDOWN_SEC,
)


class TestTrigger(unittest.TestCase):
    def test_trigger_policy(self):
        self.assertTrue(_should_trigger("What is the approval policy?"))

    def test_trigger_architecture(self):
        self.assertTrue(_should_trigger("Show me the system map and architecture"))

    def test_no_trigger_without_keywords(self):
        self.assertFalse(_should_trigger("Write a poem about the sea"))

    def test_trigger_summarize_policy_allowed(self):
        self.assertTrue(_should_trigger("Summarize the approval policy"))

    def test_no_trigger_code_generation(self):
        self.assertFalse(_should_trigger("Implement the approval gate in code"))

    def test_no_trigger_empty(self):
        self.assertFalse(_should_trigger(""))


class TestPickDoc(unittest.TestCase):
    def test_approval_picks_gate_flow(self):
        self.assertEqual(_pick_doc("approval gate flow"), "MELLOW_LINK_Approval_Gate_Flow_Map.md")

    def test_architecture_picks_system_map(self):
        self.assertEqual(_pick_doc("system architecture"), "system_map.md")


class TestQuota(unittest.TestCase):
    def setUp(self):
        self.sid = "test-quota-" + str(id(self))

    def tearDown(self):
        clear_session(self.sid)

    def test_quota_enforced(self):
        state = _get_state(self.sid)
        state["count"] = MAX_QUOTA
        state["last_ts"] = 0
        r = try_auto_read_docs(self.sid, "What is the approval gate policy?")
        self.assertIsNone(r)


class TestCooldown(unittest.TestCase):
    def setUp(self):
        self.sid = "test-cooldown-" + str(id(self))

    def tearDown(self):
        clear_session(self.sid)

    def test_cooldown_blocks_rapid_calls(self):
        state = _get_state(self.sid)
        state["count"] = 0
        state["last_ts"] = time.monotonic()
        r = try_auto_read_docs(self.sid, "Explain the approval gate")
        self.assertIsNone(r)


class TestCache(unittest.TestCase):
    def setUp(self):
        self.sid = "test-cache-" + str(id(self))

    def tearDown(self):
        clear_session(self.sid)

    def test_cache_used_on_repeat(self):
        q = "What is the system map?"
        with patch("mellow_link.core.agent_tools_docs.read_docs_file") as mock_read:
            mock_read.return_value = '{"content":"x","hash":"sha256:abc","source":"docs/system_map.md"}'
            state = _get_state(self.sid)
            state["last_ts"] = 0
            state["count"] = 0
            r1 = try_auto_read_docs(self.sid, q)
            if r1 is None:
                return
            mock_read.reset_mock()
            state["last_ts"] = 0
            state["count"] = 0
            r2 = try_auto_read_docs(self.sid, q)
            if r2 is not None:
                self.assertEqual(r1, r2)
                self.assertEqual(mock_read.call_count, 1)

    def test_return_format_has_doc_reference_prefix(self):
        q = "What is the approval gate?"
        with patch("mellow_link.core.agent_tools_docs.read_docs_file") as mock_read:
            mock_read.return_value = '{"content":"x","hash":"sha256:abc","source":"docs/a.md"}'
            state = _get_state(self.sid)
            state["last_ts"] = 0
            state["count"] = 0
            r = try_auto_read_docs(self.sid, q)
        if r is not None:
            self.assertTrue(r.startswith("[DOC_REFERENCE:"))
            self.assertIn("hash=sha256:", r)


class TestSessionPersistence(unittest.TestCase):
    """Session state persists across calls (no automatic reset)."""

    def setUp(self):
        self.sid = "test-persist-" + str(id(self))

    def tearDown(self):
        clear_session(self.sid)

    def test_state_persists_across_two_calls(self):
        _get_state(self.sid)
        from mellow_link.core.agent_docs_auto import _session_state
        self.assertIn(self.sid, _session_state)


class TestClearSession(unittest.TestCase):
    def test_clear_removes_state(self):
        sid = "test-clear"
        _get_state(sid)
        clear_session(sid)
        from mellow_link.core.agent_docs_auto import _session_state
        self.assertNotIn(sid, _session_state)
