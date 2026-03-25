"""
Legacy backend video tests.

These tests targeted the deleted `backend.video_engine.py` module and do not
describe the maintained runtime boundary anymore. The current product baseline
is the web/API flow in `web_ui.py` and `api_server.py`.
"""

import pytest

pytestmark = pytest.mark.skip(
    reason="stale legacy test: backend.video_engine.py is not part of the maintained runtime"
)


def test_legacy_backend_video_suite_is_intentionally_skipped():
    """Keep the skip explicit in pytest output."""
    assert True
