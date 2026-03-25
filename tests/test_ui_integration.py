"""
Legacy Textual TUI integration tests.

The Textual UI under `ui/` is no longer the official product entry path. These
tests are preserved only as an explicit skipped module so the repository state
is clear during pytest collection.
"""

import pytest

pytestmark = pytest.mark.skip(
    reason="stale legacy test: ui/ Textual flow is deprecated in favor of web_ui.py and api_server.py"
)


def test_legacy_tui_suite_is_intentionally_skipped():
    """Keep the skip explicit in pytest output."""
    assert True
