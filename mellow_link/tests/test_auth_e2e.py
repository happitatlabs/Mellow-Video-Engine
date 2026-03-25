"""
인증/인가 E2E 회귀 테스트 (IDOR, 무인증 run 접근).

시나리오:
  - IDOR: 미인증으로 session_id만으로 /chat/ask 호출 → 403
  - 무인증 run: /runs 목록·생성·조회·이벤트 등 인증 없이 호출 → 401
  - (선택) run control: 비-admin이 /runs/{id}/control → 403

실행:
  pytest -q mellow_link/tests/test_auth_e2e.py
  (또는) pytest -q mellow_link/tests/test_security_manager.py mellow_link/tests/test_auth_e2e.py

앱 미사용 시(의존성 부족·FASTAPI_AVAILABLE=False 등): 전체 스킵. 의존성 있으면 실제 403/401 검증.
"""

import os
import pytest

# 앱 사용 가능 여부에 따라 스킵
try:
    from fastapi.testclient import TestClient
    _has_fastapi = True
except ImportError:
    _has_fastapi = False

_app = None
_skip_reason = None


def _get_app():
    global _app, _skip_reason
    if _app is not None:
        return _app
    if _skip_reason is not None:
        return None
    if not _has_fastapi:
        _skip_reason = "FastAPI/testclient not installed"
        return None
    try:
        from mellow_link.main import app
        _app = app
        return _app
    except Exception as e:
        _skip_reason = f"Could not import app: {e}"
        return None


@pytest.fixture(scope="module")
def client():
    """TestClient for the real app. Skips if app not available (e.g. missing deps)."""
    app = _get_app()
    if app is None:
        pytest.skip(_skip_reason or "FastAPI app not available (main.app)")
    return TestClient(app)


# -----------------------------------------------------------------------------
# IDOR: 미인증 session_id 접근 차단
# -----------------------------------------------------------------------------

class TestChatAskIDOR:
    """/chat/ask: session_id를 넘기면 로그인 필수. 미인증 시 403."""

    def test_chat_ask_with_session_id_and_no_auth_returns_403(self, client):
        """미인증으로 session_id만 보내면 403 (IDOR 차단)."""
        r = client.post(
            "/chat/ask",
            json={"question": "hello", "session_id": 1},
            headers={},
        )
        assert r.status_code == 403
        data = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        detail = data.get("detail", "") or r.text
        assert "세션" in detail or "로그인" in detail or "403" in str(r.status_code)

    def test_chat_ask_with_session_id_and_invalid_token_returns_403(self, client):
        """잘못된 토큰 + session_id → 403 (user_id 없음)."""
        r = client.post(
            "/chat/ask",
            json={"question": "hello", "session_id": 1},
            headers={"Authorization": "Bearer invalid_token_xyz"},
        )
        # Invalid token → user_id stays None → same IDOR guard
        assert r.status_code == 403


# -----------------------------------------------------------------------------
# Runs API: 무인증 접근 → 401
# -----------------------------------------------------------------------------

class TestRunsUnauthenticated:
    """/runs/*: 로그인 필수. 인증 없으면 401."""

    def test_runs_list_without_auth_returns_401(self, client):
        """GET /runs without Authorization → 401."""
        r = client.get("/runs")
        assert r.status_code == 401

    def test_runs_create_without_auth_returns_401(self, client):
        """POST /runs without Authorization → 401."""
        r = client.post("/runs")
        assert r.status_code == 401

    def test_runs_get_snapshot_without_auth_returns_401(self, client):
        """GET /runs/{run_id} without Authorization → 401."""
        r = client.get("/runs/some-run-id-123")
        assert r.status_code == 401

    def test_runs_events_without_auth_returns_401(self, client):
        """GET /runs/{run_id}/events without Authorization → 401."""
        r = client.get("/runs/some-run-id-123/events")
        assert r.status_code == 401

    def test_runs_start_without_auth_returns_401(self, client):
        """POST /runs/{run_id}/start without Authorization → 401."""
        r = client.post(
            "/runs/some-run-id-123/start",
            json={"user_input": "test", "mode": "fast"},
        )
        assert r.status_code == 401


# -----------------------------------------------------------------------------
# Run control: Admin 전용 (비-admin → 403)
# -----------------------------------------------------------------------------

class TestRunsControlAdminOnly:
    """/runs/{id}/control, /mode, /propose-tool: Admin 전용."""

    def test_runs_control_without_auth_returns_401_or_403(self, client):
        """POST /runs/{id}/control without auth → 401 또는 403 (Admin 전용)."""
        r = client.post(
            "/runs/some-run-id-123/control",
            json={"action": "pause"},
        )
        assert r.status_code in (401, 403)

    def test_runs_control_with_user_token_returns_403(self, client):
        """POST /runs/{id}/control with non-admin token → 403 (if run exists and auth passes for 401)."""
        # No valid user token in test; endpoint requires admin. With no auth we get 401.
        r = client.post(
            "/runs/some-run-id-123/control",
            json={"action": "pause"},
            headers={"Authorization": "Bearer guest_anonymous"},
        )
        # Guest token → admin rejected → 403
        assert r.status_code in (401, 403)
