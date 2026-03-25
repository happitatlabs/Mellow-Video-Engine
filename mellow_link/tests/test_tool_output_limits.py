"""
Unit tests for tool output truncation (p95 latency patch).

Verifies:
- truncate_list returns correct slice and meta (returned_count, total_count, truncated, next_offset)
- format_truncation_footer produces [TRUNCATED] message
- list_directory with > limit items returns capped output and meta footer
"""

import unittest
from pathlib import Path

# Project root
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
import sys
sys.path.insert(0, str(PROJECT_ROOT))

from mellow_link.core.agent_tools_base import truncate_list, format_truncation_footer


class TestTruncateList(unittest.TestCase):
    """truncate_list helper."""

    def test_under_limit(self):
        items = list(range(30))
        sliced, total, truncated, next_offset = truncate_list(items, limit=50, offset=0)
        self.assertEqual(len(sliced), 30)
        self.assertEqual(total, 30)
        self.assertFalse(truncated)
        self.assertIsNone(next_offset)

    def test_exactly_limit(self):
        items = list(range(50))
        sliced, total, truncated, next_offset = truncate_list(items, limit=50, offset=0)
        self.assertEqual(len(sliced), 50)
        self.assertEqual(total, 50)
        self.assertFalse(truncated)
        self.assertIsNone(next_offset)

    def test_over_limit_120_items_cap_50(self):
        """Fake directory listing of 120 items -> returned 50, total 120, truncated, next_offset 50."""
        items = [f"item_{i}" for i in range(120)]
        sliced, total, truncated, next_offset = truncate_list(items, limit=50, offset=0)
        self.assertEqual(len(sliced), 50, "returned items length must be 50")
        self.assertEqual(total, 120, "meta total_count must be 120")
        self.assertTrue(truncated, "meta truncated must be True")
        self.assertEqual(next_offset, 50, "next_offset must be 50")
        self.assertEqual(sliced[0], "item_0")
        self.assertEqual(sliced[-1], "item_49")

    def test_offset_pagination(self):
        items = list(range(100))
        sliced, total, truncated, next_offset = truncate_list(items, limit=50, offset=50)
        self.assertEqual(len(sliced), 50)
        self.assertEqual(sliced[0], 50)
        self.assertEqual(sliced[-1], 99)
        self.assertFalse(truncated)
        self.assertIsNone(next_offset)


class TestFormatTruncationFooter(unittest.TestCase):
    """format_truncation_footer."""

    def test_footer_contains_truncated_and_next_offset(self):
        s = format_truncation_footer(120, 50, 50)
        self.assertIn("[TRUNCATED]", s)
        self.assertIn("returned 50/120", s)
        self.assertIn("next_offset=50", s)

    def test_footer_no_next_offset(self):
        s = format_truncation_footer(30, 30, None)
        self.assertIn("[TRUNCATED]", s)
        self.assertIn("30/30", s)


class TestListDirectoryTruncationLogic(unittest.TestCase):
    """Simulate list_directory output build: 120 items + limit 50 -> 50 lines + footer."""

    def test_simulated_list_directory_output_has_meta(self):
        """Apply same logic as list_directory: truncate_list then format; assert meta in output."""
        items = [f"entry_{i}" for i in range(120)]
        limit = 50
        sliced, total_count, truncated, next_offset = truncate_list(items, limit=limit, offset=0)
        lines = [f"      {x}" for x in sliced]
        result = "\n".join(lines)
        if truncated:
            result += "\n\n" + format_truncation_footer(total_count, len(sliced), next_offset)
        self.assertEqual(len(sliced), 50)
        self.assertEqual(total_count, 120)
        self.assertTrue(truncated)
        self.assertEqual(next_offset, 50)
        self.assertIn("[TRUNCATED]", result)
        self.assertIn("returned 50/120", result)
        self.assertIn("next_offset=50", result)


if __name__ == "__main__":
    unittest.main()
