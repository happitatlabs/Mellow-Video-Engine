import uuid

import pytest

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
    app = _get_app()
    if app is None:
        pytest.skip(_skip_reason or "FastAPI app not available")
    return TestClient(app)


def _register(client, username_prefix="phase1"):
    from mellow_link.infra.database import SessionLocal
    from mellow_link.infra import User, UserRole, create_default_folders_for_user, create_access_token

    username = f"{username_prefix}_{uuid.uuid4().hex[:8]}"
    with SessionLocal() as db:
        user = User(
            username=username,
            hashed_password="test-hash",
            role=UserRole.USER.value,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        create_default_folders_for_user(db, user.id, role=UserRole.USER.value)
        token = create_access_token(data={"sub": username}, role=user.role)
    return {
        "username": username,
        "token": token,
        "headers": {"Authorization": f"Bearer {token}", "Accept": "application/json"},
    }


def _emit_finished(run_id: str, success: bool = True, summary: str = "done"):
    from mellow_link.infra.run_events import emit_event, EVENT_TYPE_RUN_STARTED, EVENT_TYPE_RUN_FINISHED

    emit_event(run_id, EVENT_TYPE_RUN_STARTED, {"user_input": "phase1 test", "mode": "fast", "session_id": None})
    emit_event(run_id, EVENT_TYPE_RUN_FINISHED, {"success": success, "summary": summary})



def test_root_redirects_to_runtime_console(client):
    res = client.get("/", follow_redirects=False)
    assert res.status_code in (302, 307)
    assert res.headers["location"] == "/runtime-console"


def test_legacy_ui_is_marked_deprecated(client):
    res = client.get("/index.html")
    assert res.status_code == 200
    assert "Deprecated" in res.text
    assert "/runtime-console" in res.text


def test_home_prioritizes_runtime_entry(client):
    res = client.get("/ui")
    assert res.status_code == 200
    text = res.text
    assert "Default Entry: Runtime Chat" in text
    assert "Runtime Chat" in text


def test_legacy_ui_disables_new_chat_flow(client):
    res = client.get("/index.html")
    assert res.status_code == 200
    text = res.text
    assert "Legacy UI (Deprecated)" in text
    assert "Runtime Console 사용을 권장합니다." in text
    assert 'id="messageInput"' in text and 'disabled' in text
    assert 'id="sendBtn"' in text and 'Legacy chat disabled' in text

def test_ui_exposes_module_hub(client):
    res = client.get("/ui")
    assert res.status_code == 200
    text = res.text
    assert "Executable AI Platform" in text
    assert "/api/modules" in text
    assert "Modules" in text



def test_runtime_console_pages_are_exposed(client):
    chat_res = client.get("/runtime-console")
    assert chat_res.status_code == 200
    assert "/runtime/turn" in chat_res.text
    assert "/runtime/status" in chat_res.text

    ops_res = client.get("/runtime-operator")
    assert ops_res.status_code == 200
    assert "/runtime/status" in ops_res.text

def test_runs_creation_assigns_default_session_and_list_shows_new_run(client):
    user = _register(client, "phase1_owner")

    create_res = client.post("/runs", headers=user["headers"])
    assert create_res.status_code == 200, create_res.text
    created = create_res.json()
    run_id = created["run_id"]
    assert created["session_id"]

    list_res = client.get("/runs", headers=user["headers"])
    assert list_res.status_code == 200, list_res.text
    runs = list_res.json()["runs"]
    matched = next((r for r in runs if r["run_id"] == run_id), None)
    assert matched is not None
    assert matched["session_id"] == created["session_id"]


def test_user_console_reentry_snapshot_survives_refresh(client):
    user = _register(client, "phase1_reentry")
    create_res = client.post("/runs", headers=user["headers"])
    run_id = create_res.json()["run_id"]
    session_id = create_res.json()["session_id"]

    start_res = client.post(
        f"/runs/{run_id}/start",
        headers={**user["headers"], "Content-Type": "application/json"},
        json={"user_input": "hello", "mode": "fast", "session_id": session_id},
    )
    assert start_res.status_code == 200, start_res.text

    page_res = client.get(f"/user-console?run_id={run_id}")
    assert page_res.status_code == 200
    assert "실행 진행 뷰" in page_res.text

    snap_res = client.get(f"/runs/{run_id}", headers=user["headers"])
    assert snap_res.status_code == 200, snap_res.text
    snap = snap_res.json()
    assert snap["run_id"] == run_id
    assert snap["session_id"] == session_id


def test_sse_allows_only_authenticated_owner(client):
    owner = _register(client, "phase1_sse_owner")
    other = _register(client, "phase1_sse_other")

    create_res = client.post("/runs", headers=owner["headers"])
    run_id = create_res.json()["run_id"]
    _emit_finished(run_id, success=True, summary="finished for sse")

    unauth = client.get(f"/runs/{run_id}/events")
    assert unauth.status_code == 401

    forbidden = client.get(
        f"/runs/{run_id}/events",
        params={"access_token": other["token"]},
        headers={"Accept": "text/event-stream"},
    )
    assert forbidden.status_code == 403

    allowed = client.get(
        f"/runs/{run_id}/events",
        params={"access_token": owner["token"]},
        headers={"Accept": "text/event-stream"},
    )
    assert allowed.status_code == 200
    assert "text/event-stream" in allowed.headers.get("content-type", "")


def test_owned_runs_only_and_orphan_runs_hidden(client):
    user = _register(client, "phase1_visible")

    owned = client.post("/runs", headers=user["headers"]).json()
    _emit_finished(owned["run_id"], success=True, summary="owned run")

    from mellow_link.infra.database import SessionLocal, AgentRun
    from datetime import datetime

    orphan_id = f"run_orphan_{uuid.uuid4().hex[:8]}"
    with SessionLocal() as db:
        db.add(AgentRun(run_id=orphan_id, session_id=None, status="completed", created_at=datetime.utcnow(), updated_at=datetime.utcnow(), summary="orphan"))
        db.commit()

    list_res = client.get("/runs", headers=user["headers"])
    assert list_res.status_code == 200
    ids = {item["run_id"] for item in list_res.json()["runs"]}
    assert owned["run_id"] in ids
    assert orphan_id not in ids



