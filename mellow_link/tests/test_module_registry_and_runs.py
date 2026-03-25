import uuid
from types import SimpleNamespace

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


def _user_headers():
    from mellow_link.infra.database import SessionLocal
    from mellow_link.infra import User, UserRole, create_default_folders_for_user, create_access_token

    username = f"mod_{uuid.uuid4().hex[:8]}"
    with SessionLocal() as db:
        user = User(username=username, hashed_password="test-hash", role=UserRole.USER.value)
        db.add(user)
        db.commit()
        db.refresh(user)
        create_default_folders_for_user(db, user.id, role=UserRole.USER.value)
        token = create_access_token(data={"sub": username}, role=user.role)
    return {"Authorization": f"Bearer {token}", "Accept": "application/json"}


def test_modules_api_lists_registered_modules(client):
    res = client.get("/api/modules")
    assert res.status_code == 200
    modules = res.json()["modules"]
    ids = {m["module_id"] for m in modules}
    assert {"sql_analytics", "research_assistant", "rebuild_assistant"}.issubset(ids)
    assert "ai_workflow_console" not in ids


def test_sql_analytics_run_has_module_metadata(client):
    headers = _user_headers()
    res = client.post(
        "/modules/sql_analytics/runs",
        headers={**headers, "Content-Type": "application/json"},
        json={"question": "지난 7일 환불률을 알려줘"},
    )
    assert res.status_code == 200, res.text
    data = res.json()
    run_id = data["run_id"]

    snap = client.get(f"/runs/{run_id}", headers=headers)
    assert snap.status_code == 200, snap.text
    body = snap.json()
    assert body["module_id"] == "sql_analytics"
    assert body["run_kind"] == "sql_analysis"


def test_research_assistant_reuses_temp_upload_flow(client, monkeypatch):
    from mellow_link.modules.research_assistant import api as research_api

    monkeypatch.setattr(
        research_api,
        "start_research_run",
        lambda *args, **kwargs: None,
    )

    headers = _user_headers()
    temp_session_id = f"research-temp-{uuid.uuid4().hex[:8]}"

    upload = client.post(
        "/chat/upload-temp",
        data={"session_id": temp_session_id},
        files={"file": ("brief.txt", b"Quarterly revenue increased by 18 percent. Refunds decreased by 4 percent.")},
    )
    assert upload.status_code == 200, upload.text

    res = client.post(
        "/modules/research_assistant/runs",
        headers={**headers, "Content-Type": "application/json"},
        json={
            "question": "업로드한 문서를 기준으로 핵심 변화를 요약해줘",
            "context_note": "간단한 요약으로 정리",
            "temp_session_id": temp_session_id,
        },
    )
    assert res.status_code == 200, res.text
    data = res.json()
    run_id = data["run_id"]

    snap = client.get(f"/runs/{run_id}", headers=headers)
    assert snap.status_code == 200, snap.text
    body = snap.json()
    assert body["module_id"] == "research_assistant"
    assert body["run_kind"] == "research_run"


def test_rebuild_assistant_run_has_module_metadata(client, monkeypatch):
    from mellow_link.modules.rebuild_assistant import api as rebuild_api

    monkeypatch.setattr(
        rebuild_api,
        "start_rebuild_assistant_run",
        lambda *args, **kwargs: None,
    )

    headers = _user_headers()
    res = client.post(
        "/modules/rebuild_assistant/runs",
        headers={**headers, "Content-Type": "application/json"},
        json={
            "goal": "이 JSP 주문 화면을 React 구조로 바꿔줘",
            "assets": {"source_code": "<% String id = request.getParameter(\"id\"); %>"},
        },
    )
    assert res.status_code == 200, res.text
    data = res.json()
    run_id = data["run_id"]

    snap = client.get(f"/runs/{run_id}", headers=headers)
    assert snap.status_code == 200, snap.text
    body = snap.json()
    assert body["module_id"] == "rebuild_assistant"
    assert body["run_kind"] == "rebuild_plan"


def test_rebuild_assistant_validates_goal_length(client):
    headers = _user_headers()
    res = client.post(
        "/modules/rebuild_assistant/runs",
        headers={**headers, "Content-Type": "application/json"},
        json={
            "goal": "짧다",
            "assets": {"source_code": "legacy"},
        },
    )
    assert res.status_code == 422


def test_rebuild_assistant_requires_assets_or_temp_session(client):
    headers = _user_headers()
    res = client.post(
        "/modules/rebuild_assistant/runs",
        headers={**headers, "Content-Type": "application/json"},
        json={
            "goal": "이 기능을 단일 페이지 기준으로 재구성해줘",
            "assets": {},
        },
    )
    assert res.status_code == 422


def test_research_assistant_formats_user_facing_summary():
    from mellow_link.modules.research_assistant.service import ResearchAssistantService

    svc = ResearchAssistantService()
    formatted = svc.format_user_summary(
        "매출은 전분기 대비 18% 증가했습니다. 다만 특정 제품군의 수익성은 낮습니다. "
        "환불률은 4% 감소했습니다. 다음 분기에는 저수익 제품군 정리가 필요합니다.",
        question="업로드한 문서를 기준으로 핵심 변화를 요약해줘",
        has_document_context=True,
    )

    assert "한 줄 결론" in formatted
    assert "핵심 요약" in formatted
    assert "주요 쟁점" in formatted
    assert "다음 액션" in formatted
    assert "18% 증가" in formatted
    assert "환불률은 4% 감소" in formatted


def test_research_assistant_detects_bootstrap_payload_as_weak_summary():
    from mellow_link.modules.research_assistant.service import ResearchAssistantService

    svc = ResearchAssistantService()
    weak = svc.is_weak_summary(
        '{"status":"initialized","message":"시스템 지침 인식 완료","workspace_directory":"mellow_link/workspace/","available_tools":["read_file"]}'
    )

    assert weak is True


def test_research_assistant_fallback_summary_mentions_incomplete_generation():
    from mellow_link.modules.research_assistant.service import ResearchAssistantService

    svc = ResearchAssistantService()
    formatted = svc.format_user_summary(
        "",
        question="문서를 읽고 SQL 유스케이스 적합성을 평가해줘",
        has_document_context=True,
    )

    assert "충분한 문서 기반 응답을 생성하지 못했습니다" in formatted
    assert "질문 범위를 더 좁혀 재실행하세요" in formatted


def test_research_assistant_summary_sanitizes_markdown_and_redacted_path():
    from mellow_link.modules.research_assistant.service import ResearchAssistantService

    svc = ResearchAssistantService()
    formatted = svc.format_user_summary(
        """
한 줄 결론
- **명확한 MVP 구조**이나 [REDACTED_PATH] 데이터 파이프라인이 비어 있습니다.

핵심 요약
- **잘 설계된 점**: SQL 계층과 규칙 계층이 분리되어 있습니다.
- **빠진 점**: UI[REDACTED_PATH] 모듈과 피드백 루프 정의가 없습니다.
        """,
        question="문서를 평가해줘",
        has_document_context=True,
    )

    assert "**" not in formatted
    assert "[REDACTED_PATH]" not in formatted
    assert "UI 모듈" in formatted


def test_research_assistant_splits_compound_heading_items():
    from mellow_link.modules.research_assistant.service import ResearchAssistantService

    svc = ResearchAssistantService()
    formatted = svc.format_user_summary(
        """
한 줄 결론
- 구조는 명확합니다.

핵심 요약
- 잘 설계된 점: 규칙 엔진이 분리되어 있습니다. - 빠진 점: 데이터 파이프라인이 없습니다. - 추천 구현 순서: SQL -> 규칙 -> AI
        """,
        question="문서를 평가해줘",
        has_document_context=True,
    )

    assert "- 잘 설계된 점: 규칙 엔진이 분리되어 있습니다." in formatted
    assert "- 빠진 점: 데이터 파이프라인이 없습니다." in formatted
    assert "- 추천 구현 순서: SQL -> 규칙 -> AI" in formatted


def test_sql_analytics_formats_user_facing_summary():
    from mellow_link.modules.sql_analytics.service import SQLAnalyticsService

    svc = SQLAnalyticsService()
    formatted = svc.format_user_summary(
        result={
            "decision": "high_risk",
            "normalized_request": {"filters": {"segment": "all"}},
            "sql_results": {
                "rows": [
                    {
                        "refund_rate": 0.081,
                        "inquiry_growth": 0.17,
                        "churn_rate": 0.05,
                    }
                ]
            },
            "rule_results": [
                {"matched": True, "message": "환불률이 기준치를 초과했습니다."},
                {"matched": True, "message": "문의량 증가가 감지되었습니다."},
            ],
        },
        question="현재 데이터에 어떤 이상 징후가 있는지 알려줘",
    )

    assert "한 줄 결론" in formatted
    assert "핵심 요약" in formatted
    assert "주요 쟁점" in formatted
    assert "다음 액션" in formatted
    assert "환불률 8.1%" in formatted
    assert "문의 증가율 17.0%" in formatted
    assert "고위험 상태" in formatted
    assert "환불률이 기준치를 초과했습니다." in formatted


def test_rebuild_assistant_structured_result_contract_uses_fixed_list_types():
    from mellow_link.modules.rebuild_assistant.schemas import RebuildAssetsPayload
    from mellow_link.modules.rebuild_assistant.service import RebuildAssistantService

    svc = RebuildAssistantService()
    prepared = svc.prepare_input(
        goal="이 JSP 주문 조회 화면을 React + REST API로 재구성해줘",
        assets=RebuildAssetsPayload(
            source_code="""
<%@ page language="java" %>
<%
String userId = request.getParameter("userId");
String sql = "SELECT * FROM orders WHERE user_id = ?";
%>
            """,
            database_schema="CREATE TABLE orders (id bigint, user_id varchar(50), status varchar(20));",
            sql_queries="SELECT o.id, o.status FROM orders o JOIN users u ON u.id = o.user_id",
            framework_info="JSP, Servlet, JDBC",
        ),
    )
    result = svc.build_result(prepared)

    dumped = result.model_dump()
    assert set(dumped.keys()) == {
        "one_line_conclusion",
        "analysis_summary",
        "rebuild_strategy",
        "layer_reconstruction",
        "recomposition_draft",
        "risks",
        "extracted_rules",
        "confidence",
        "missing_context",
    }
    assert isinstance(dumped["one_line_conclusion"], str)
    assert isinstance(dumped["analysis_summary"], list)
    assert isinstance(dumped["rebuild_strategy"], list)
    assert isinstance(dumped["risks"], list)
    assert isinstance(dumped["missing_context"], list)
    assert isinstance(dumped["confidence"], float)
    assert 0.0 <= dumped["confidence"] <= 1.0
    assert set(dumped["layer_reconstruction"].keys()) == {"database", "backend", "frontend"}
    assert set(dumped["recomposition_draft"].keys()) == {"database", "backend", "frontend"}
    assert set(dumped["extracted_rules"].keys()) == {"status_permissions", "search_filters", "save_validation"}
    assert all(isinstance(dumped["layer_reconstruction"][key], list) for key in ("database", "backend", "frontend"))
    assert all(isinstance(dumped["recomposition_draft"][key], list) for key in ("database", "backend", "frontend"))


def test_rebuild_assistant_scope_limiting_and_missing_context():
    from mellow_link.modules.rebuild_assistant.schemas import RebuildAssetsPayload
    from mellow_link.modules.rebuild_assistant.service import RebuildAssistantService

    svc = RebuildAssistantService()
    prepared = svc.prepare_input(
        goal="whole-system migration으로 전체 사이트를 배포 가능한 코드까지 다시 만들어줘",
        assets=RebuildAssetsPayload(source_code="<%-- minimal jsp --%>"),
    )
    result = svc.build_result(prepared)

    assert prepared.scope_limited is True
    assert result.rebuild_strategy
    assert result.missing_context
    assert result.confidence < 1.0


def test_rebuild_assistant_status_permissions_branching():
    from mellow_link.modules.rebuild_assistant.schemas import RebuildAssetsPayload
    from mellow_link.modules.rebuild_assistant.service import RebuildAssistantService

    svc = RebuildAssistantService()
    prepared = svc.prepare_input(
        goal="결재 상태와 권한에 따라 액션이 바뀌는 JSP 화면을 재구성해줘",
        assets=RebuildAssetsPayload(
            source_code="""
if ("APPROVED".equals(status) || userRole.equals("ADMIN")) { showApproveButton = true; }
if ("PENDING".equals(status)) { showRejectButton = true; }
            """,
            ui_template="""
<c:if test="${sessionScope.role eq 'ADMIN'}"><button>Approve</button></c:if>
<c:if test="${item.status eq 'PENDING'}"><button>Reject</button></c:if>
            """,
        ),
    )
    result = svc.build_result(prepared)

    assert prepared.signals.primary_feature_mode == "status_permissions"
    assert prepared.signals.status_permissions
    assert any("status_permissions" in item for item in result.analysis_summary)
    assert any("role/status/action visibility" in item for item in result.rebuild_strategy)
    assert any("policy" in item.lower() for item in result.recomposition_draft.backend)
    rules = result.extracted_rules.status_permissions.model_dump()
    assert set(rules.keys()) == {
        "entities",
        "roles",
        "statuses",
        "actions",
        "role_action_matrix",
        "status_action_matrix",
        "transition_rules",
        "ui_visibility_rules",
        "policy_hints",
    }
    assert rules["entities"]
    assert "ADMIN".lower() in [item.lower() for item in rules["roles"]]
    assert rules["actions"]
    assert rules["transition_rules"]
    assert rules["ui_visibility_rules"] or rules["policy_hints"]


def test_rebuild_assistant_search_filters_branching():
    from mellow_link.modules.rebuild_assistant.schemas import RebuildAssetsPayload
    from mellow_link.modules.rebuild_assistant.service import RebuildAssistantService

    svc = RebuildAssistantService()
    prepared = svc.prepare_input(
        goal="검색 조건이 많은 주문 조회 화면을 재구성해줘",
        assets=RebuildAssetsPayload(
            source_code="""
String keyword = request.getParameter("keyword");
String statusFilter = request.getParameter("status");
String page = request.getParameter("page");
            """,
            sql_queries="""
SELECT * FROM orders
WHERE user_name LIKE ?
AND status = ?
ORDER BY created_at DESC
LIMIT ? OFFSET ?
            """,
        ),
    )
    result = svc.build_result(prepared)

    assert prepared.signals.primary_feature_mode == "search_filters"
    assert prepared.signals.search_filters
    assert any("search_filters" in item for item in result.analysis_summary)
    assert any("query parameter" in item.lower() or "sql parameterization" in item.lower() for item in result.rebuild_strategy)
    assert any("OrderSearchQueryDTO" in item or "query state" in item for item in result.recomposition_draft.backend + result.recomposition_draft.frontend)
    assert any("OrderSearchPage" in item for item in result.recomposition_draft.frontend)
    assert any("OrderSearchFilterBar" in item for item in result.recomposition_draft.frontend)
    assert any("OrderSearchResultsTable" in item for item in result.recomposition_draft.frontend)
    assert any("order_query_mapper" in item for item in result.recomposition_draft.backend)
    assert "검색 필터 상태" in result.one_line_conclusion
    rules = result.extracted_rules.search_filters.model_dump()
    assert set(rules.keys()) == {
        "entities",
        "filter_fields",
        "query_params",
        "sort_rules",
        "paging_rules",
        "query_binding_rules",
        "default_filters",
        "result_shape_hints",
    }
    assert rules["entities"]
    assert rules["filter_fields"]
    assert rules["query_params"]
    assert rules["query_binding_rules"]
    assert rules["paging_rules"] or rules["sort_rules"] or rules["default_filters"] or rules["result_shape_hints"]


def test_rebuild_assistant_save_validation_branching():
    from mellow_link.modules.rebuild_assistant.schemas import RebuildAssetsPayload
    from mellow_link.modules.rebuild_assistant.service import RebuildAssistantService

    svc = RebuildAssistantService()
    prepared = svc.prepare_input(
        goal="저장 검증과 중복 체크가 많은 등록 기능을 재구성해줘",
        assets=RebuildAssetsPayload(
            source_code="""
if (name == null || name.isBlank()) throw new IllegalArgumentException("required");
if (repository.existsByCode(code)) throw new IllegalStateException("duplicate");
repository.save(entity);
            """,
            sql_queries="SELECT count(1) FROM products WHERE code = ?; INSERT INTO products(code, name) VALUES (?, ?);",
        ),
    )
    result = svc.build_result(prepared)

    assert prepared.signals.primary_feature_mode == "save_validation"
    assert prepared.signals.save_validation
    assert any("save_validation" in item for item in result.analysis_summary)
    assert any("save guard" in item.lower() or "duplicate check" in item.lower() for item in result.rebuild_strategy)
    assert any("CommandDTO" in item or "validator" in item.lower() for item in result.recomposition_draft.backend)
    assert "저장 검증과 중복 체크" in result.one_line_conclusion
    rules = result.extracted_rules.save_validation.model_dump()
    assert set(rules.keys()) == {
        "entities",
        "required_fields",
        "field_validation_rules",
        "duplicate_check_rules",
        "save_guard_rules",
        "exception_rules",
        "command_boundary_hints",
    }
    assert rules["entities"]
    assert rules["required_fields"]
    assert rules["field_validation_rules"]
    assert rules["duplicate_check_rules"]
    assert rules["save_guard_rules"]


def test_rebuild_assistant_confidence_varies_with_signal_coverage():
    from mellow_link.modules.rebuild_assistant.schemas import RebuildAssetsPayload
    from mellow_link.modules.rebuild_assistant.service import RebuildAssistantService

    svc = RebuildAssistantService()
    sparse = svc.prepare_input(
        goal="이 기능을 재구성해줘",
        assets=RebuildAssetsPayload(source_code="<% legacy %>"),
    )
    rich = svc.prepare_input(
        goal="주문 검색/저장/권한 기능을 React + REST API로 재구성해줘",
        assets=RebuildAssetsPayload(
            source_code="""
String keyword = request.getParameter("keyword");
if (userRole.equals("ADMIN")) { canApprove = true; }
if (repository.existsByCode(code)) throw new RuntimeException("duplicate");
            """,
            ui_template="<c:if test=\"${item.status eq 'PENDING'}\"><button>Approve</button></c:if>",
            database_schema="CREATE TABLE orders (id bigint, status varchar(20), code varchar(20));",
            sql_queries="SELECT * FROM orders WHERE status = ? AND name LIKE ? ORDER BY created_at DESC; INSERT INTO orders(code) VALUES (?);",
            framework_info="JSP + Spring MVC + MyBatis",
        ),
    )

    assert svc.estimate_confidence(rich) > svc.estimate_confidence(sparse)


def test_rebuild_assistant_summary_mentions_scope_metadata():
    from mellow_link.modules.rebuild_assistant.schemas import RebuildAssetsPayload
    from mellow_link.modules.rebuild_assistant.service import RebuildAssistantService

    svc = RebuildAssistantService()
    prepared = svc.prepare_input(
        goal="이 JSP 주문 조회 화면을 React + REST API로 재구성해줘",
        assets=RebuildAssetsPayload(source_code="<% String sql = \"SELECT * FROM orders\"; %>"),
    )
    result = svc.build_result(prepared)
    summary = svc.format_user_summary(result, scope_limited=prepared.scope_limited, needs_more_input=bool(result.missing_context))

    assert "한 줄 결론" in summary
    assert "재구성 전략" in summary
    assert "레이어별 재구성" in summary
    assert "초안" in summary
    assert "리스크" in summary
    assert "confidence:" in summary


def test_sql_analytics_classifies_risk_analysis_question():
    from mellow_link.modules.sql_analytics.service import SQLAnalyticsService

    svc = SQLAnalyticsService()

    assert svc.classify_question("최근 30일 환불률과 문의 증가율 기준으로 이상 징후를 알려줘") == "risk_analysis"


def test_sql_analytics_classifies_schema_like_question():
    from mellow_link.modules.sql_analytics.service import SQLAnalyticsService

    svc = SQLAnalyticsService()

    assert svc.classify_question("현재 데이터에 어떤 테이블이 있고 각 컬럼이 무엇인지 알려줘") == "schema_like"


def test_sql_analytics_formats_schema_like_summary_without_risk_claims():
    from mellow_link.modules.sql_analytics.service import SQLAnalyticsService

    svc = SQLAnalyticsService()
    formatted = svc.format_unsupported_summary(
        question="현재 데이터에 어떤 테이블이 있고 각 컬럼이 무엇인지 알려줘",
        intent="schema_like",
    )

    assert "리스크 분석 전용" in formatted
    assert "테이블/컬럼 조회" in formatted or "테이블/컬럼" in formatted
    assert "환불률" in formatted


def test_sql_analytics_analyze_question_skips_pipeline_for_unsupported(monkeypatch):
    from mellow_link.modules.sql_analytics.service import SQLAnalyticsService

    svc = SQLAnalyticsService()

    def fail_analyze(*args, **kwargs):
        raise AssertionError("pipeline should not run for schema_like question")

    monkeypatch.setattr(svc, "analyze", fail_analyze)
    result = svc.analyze_question("현재 데이터에 어떤 테이블이 있고 각 컬럼이 무엇인지 알려줘")

    assert result["intent"] == "schema_like"
    assert result["supported"] is False
    assert "리스크 분석 전용" in result["summary"]


def test_research_todos_view_uses_module_mapping():
    from mellow_link.infra.run_events import build_todos_view

    raw_todos = [
        {"todo_id": "R1", "title": "질문 정리"},
        {"todo_id": "R2", "title": "문서 문맥 수집"},
        {"todo_id": "R3", "title": "문서 기반 분석"},
        {"todo_id": "R4", "title": "결과 요약"},
    ]
    events = [
        {"type": "todo_started", "payload": {"todo_id": "R1"}},
        {"type": "todo_done", "payload": {"todo_id": "R1"}},
        {"type": "todo_started", "payload": {"todo_id": "R2"}},
        {"type": "todo_done", "payload": {"todo_id": "R2"}},
        {"type": "todo_started", "payload": {"todo_id": "R3"}},
        {"type": "todo_done", "payload": {"todo_id": "R3"}},
        {"type": "todo_started", "payload": {"todo_id": "R4"}},
        {"type": "todo_done", "payload": {"todo_id": "R4"}},
    ]

    todos_view = build_todos_view("research_assistant", raw_todos, None, events, run_status="completed")

    assert [stage["title"] for stage in todos_view] == ["준비", "처리", "완료"]
    assert [stage["status"] for stage in todos_view] == ["completed", "completed", "completed"]
    assert todos_view[0]["raw_todo_ids"] == ["R1", "R2"]
    assert todos_view[1]["raw_todo_ids"] == ["R3"]
    assert todos_view[2]["raw_todo_ids"] == ["R4"]


def test_rebuild_todos_view_uses_module_mapping():
    from mellow_link.infra.run_events import build_todos_view

    raw_todos = [
        {"todo_id": "B1", "title": "입력 준비"},
        {"todo_id": "B2", "title": "레거시 분석"},
        {"todo_id": "B3", "title": "재구성 설계"},
        {"todo_id": "B4", "title": "초안 생성"},
        {"todo_id": "B5", "title": "결과 정리"},
    ]
    events = [
        {"type": "todo_started", "payload": {"todo_id": "B1"}},
        {"type": "todo_done", "payload": {"todo_id": "B1"}},
        {"type": "todo_started", "payload": {"todo_id": "B2"}},
        {"type": "todo_done", "payload": {"todo_id": "B2"}},
        {"type": "todo_started", "payload": {"todo_id": "B3"}},
        {"type": "todo_done", "payload": {"todo_id": "B3"}},
        {"type": "todo_started", "payload": {"todo_id": "B4"}},
        {"type": "todo_done", "payload": {"todo_id": "B4"}},
        {"type": "todo_started", "payload": {"todo_id": "B5"}},
        {"type": "todo_done", "payload": {"todo_id": "B5"}},
    ]

    todos_view = build_todos_view("rebuild_assistant", raw_todos, None, events, run_status="completed")

    assert [stage["title"] for stage in todos_view] == ["준비", "처리", "완료"]
    assert todos_view[0]["raw_todo_ids"] == ["B1", "B2"]
    assert todos_view[1]["raw_todo_ids"] == ["B3", "B4"]
    assert todos_view[2]["raw_todo_ids"] == ["B5"]
    assert [stage["status"] for stage in todos_view] == ["completed", "completed", "completed"]


def test_unknown_module_fallback_returns_three_stages_and_progress_rounding():
    from mellow_link.infra.run_events import build_todos_view, _compute_normalized_progress_percent

    raw_todos = [{"todo_id": "X1", "title": "Only step"}]
    events = [{"type": "todo_started", "payload": {"todo_id": "X1"}}]

    todos_view = build_todos_view("unknown_module", raw_todos, "X1", events, run_status="running")

    assert len(todos_view) == 3
    assert todos_view[0]["raw_todo_ids"] == []
    assert todos_view[1]["raw_todo_ids"] == ["X1"]
    assert todos_view[2]["raw_todo_ids"] == []
    assert [stage["status"] for stage in todos_view] == ["completed", "in_progress", "pending"]
    assert _compute_normalized_progress_percent(todos_view, "running") == 50


def test_stage_status_priority_prefers_aborted_over_other_states():
    from mellow_link.infra.run_events import build_todos_view

    raw_todos = [
        {"todo_id": "A1", "title": "prep one"},
        {"todo_id": "A2", "title": "prep two"},
    ]
    events = [
        {"type": "todo_started", "payload": {"todo_id": "A1"}},
        {"type": "todo_done", "payload": {"todo_id": "A1"}},
        {"type": "todo_started", "payload": {"todo_id": "A2"}},
    ]

    todos_view = build_todos_view("unknown_module", raw_todos, "A2", events, run_status="failed")

    assert todos_view[0]["status"] == "completed"
    assert todos_view[1]["status"] == "aborted"
    assert todos_view[2]["status"] == "aborted"


def test_llm_service_resolves_request_timeout_override():
    from mellow_link.services.llm_service import LLMService

    svc = LLMService(timeout=30.0)

    timeout_seconds, source = svc._resolve_request_timeout(
        mode="research",
        request_timeout_seconds=90.0,
    )

    assert timeout_seconds == 90.0
    assert source == "http_client"


def test_llm_service_uses_default_timeout_without_override():
    from mellow_link.services.llm_service import LLMService

    svc = LLMService(timeout=30.0)

    timeout_seconds, source = svc._resolve_request_timeout(
        mode="fast",
        request_timeout_seconds=None,
    )

    assert timeout_seconds == 30.0
    assert source == "http_client"


def _run_research_abort_case(monkeypatch, abort_stage: str):
    from mellow_link import app_state
    from mellow_link.modules.research_assistant import runner as research_runner
    from mellow_link.routers.runs import RUN_CONTROL_STATE

    run_id = f"run_abort_{abort_stage}"
    temp_session_id = f"temp_{abort_stage}"
    events = []
    base_service = research_runner.ResearchAssistantService

    class InlineThread:
        def __init__(self, target=None, daemon=None, *args, **kwargs):
            self._target = target

        def start(self):
            if self._target:
                self._target()

    class FakeLLM:
        def __init__(self):
            self.calls = 0
            self._current_model = "qwen3.5:9b"

        def get_model_for_mode(self, mode):
            return self._current_model

        async def generate(self, *args, **kwargs):
            self.calls += 1
            if abort_stage == "attempt_1" and self.calls == 1:
                RUN_CONTROL_STATE[run_id]["abort_requested"] = True
            if abort_stage == "attempt_2" and self.calls == 1:
                return SimpleNamespace(content="")
            return SimpleNamespace(
                content=(
                    "한 줄 결론\n- 구조는 명확하지만 MVP 범위 조정이 필요합니다.\n\n"
                    "핵심 요약\n- 규칙 엔진과 SQL 계층 분리는 타당합니다.\n"
                    "- 다만 AI 해석 레이어는 아직 범위가 넓고 구현 기준이 덜 정리되었습니다.\n"
                    "- 초기 단계에서는 규칙과 데이터 처리에 집중하는 것이 현실적입니다."
                )
            )

        def clear_context(self, context_id):
            return None

        async def unload_model(self):
            self._current_model = None

        async def cleanup_stale_models(self, current_model=None):
            return None

    class FakeService(base_service):
        def build_prompt(self, question: str, context_note: str = "", document_context: str = "") -> str:
            return "primary prompt"

        def build_reduced_prompt(self, question: str, context_note: str = "", document_context: str = "") -> str:
            if abort_stage == "attempt_2":
                RUN_CONTROL_STATE[run_id]["abort_requested"] = True
            return "reduced prompt"

        def format_user_summary(self, raw_summary: str, question: str, has_document_context: bool) -> str:
            if abort_stage == "finalize":
                RUN_CONTROL_STATE[run_id]["abort_requested"] = True
            return super().format_user_summary(raw_summary, question, has_document_context)

    def fake_emit(run_id_arg, event_type, payload, **kwargs):
        events.append({"run_id": run_id_arg, "type": event_type, "payload": payload})

    fake_llm = FakeLLM()
    RUN_CONTROL_STATE.clear()
    RUN_CONTROL_STATE[run_id] = {"paused": False, "abort_requested": False, "running": True}
    monkeypatch.setattr(research_runner.threading, "Thread", InlineThread)
    monkeypatch.setattr(research_runner, "emit_event", fake_emit)
    monkeypatch.setattr(research_runner, "ResearchAssistantService", FakeService)
    monkeypatch.setattr(app_state, "llm_service", fake_llm, raising=False)
    monkeypatch.setattr(app_state, "TEMP_CONTEXT_STORE", {temp_session_id: "document context"}, raising=False)

    research_runner.start_research_run(
        run_id=run_id,
        session_id="session-test",
        question="문서를 평가해줘",
        context_note="",
        temp_session_id=temp_session_id,
    )

    finished = [event for event in events if event["type"] == "run_finished"]
    assert len(finished) == 1
    return finished[0]["payload"], fake_llm.calls, events


def test_rebuild_assistant_runner_emits_structured_result(monkeypatch):
    from mellow_link import app_state
    from mellow_link.modules.rebuild_assistant import runner as rebuild_runner

    events = []

    class InlineThread:
        def __init__(self, target=None, daemon=None, *args, **kwargs):
            self._target = target

        def start(self):
            if self._target:
                self._target()

    def fake_emit(run_id_arg, event_type, payload, **kwargs):
        events.append({"run_id": run_id_arg, "type": event_type, "payload": payload})

    monkeypatch.setattr(rebuild_runner.threading, "Thread", InlineThread)
    monkeypatch.setattr(rebuild_runner, "emit_event", fake_emit)
    monkeypatch.setattr(app_state, "TEMP_CONTEXT_STORE", {"rebuild-temp": "--- [legacy.jsp] ---\n<% String sql = \"SELECT * FROM orders\"; %>"}, raising=False)

    rebuild_runner.start_rebuild_assistant_run(
        run_id="run_rebuild_test",
        session_id="session-test",
        goal="이 JSP 주문 조회 화면을 React + REST API로 재구성해줘",
        assets=rebuild_runner.RebuildAssetsPayload(
            source_code="<% String sql = \"SELECT * FROM orders\"; %>",
            sql_queries="SELECT * FROM orders",
        ),
        constraints=["기존 DB 호환 유지"],
        temp_session_id="rebuild-temp",
    )

    finished = [event for event in events if event["type"] == "run_finished"]
    assert len(finished) == 1
    payload = finished[0]["payload"]
    assert payload["success"] is True
    assert payload["module_id"] == "rebuild_assistant"
    assert payload["run_kind"] == "rebuild_plan"
    assert isinstance(payload["structured_result"]["analysis_summary"], list)
    assert set(payload["structured_result"]["layer_reconstruction"].keys()) == {"database", "backend", "frontend"}
    assert set(payload["structured_result"]["extracted_rules"].keys()) == {"status_permissions", "search_filters", "save_validation"}
    assert isinstance(payload["confidence"], float)
    todo_ids = [event["payload"].get("todo_id") for event in events if event["type"] == "todo_started"]
    assert todo_ids == ["B1", "B2", "B3", "B4", "B5"]


def test_rebuild_assistant_extracted_rules_shape_is_kept_for_sparse_input():
    from mellow_link.modules.rebuild_assistant.schemas import RebuildAssetsPayload
    from mellow_link.modules.rebuild_assistant.service import RebuildAssistantService

    svc = RebuildAssistantService()
    prepared = svc.prepare_input(
        goal="이 기능을 재구성해줘",
        assets=RebuildAssetsPayload(source_code="<% legacy %>"),
    )
    result = svc.build_result(prepared)
    dumped = result.extracted_rules.model_dump()

    assert set(dumped.keys()) == {"status_permissions", "search_filters", "save_validation"}
    assert set(dumped["status_permissions"].keys()) == {
        "entities",
        "roles",
        "statuses",
        "actions",
        "role_action_matrix",
        "status_action_matrix",
        "transition_rules",
        "ui_visibility_rules",
        "policy_hints",
    }
    assert set(dumped["search_filters"].keys()) == {
        "entities",
        "filter_fields",
        "query_params",
        "sort_rules",
        "paging_rules",
        "query_binding_rules",
        "default_filters",
        "result_shape_hints",
    }
    assert set(dumped["save_validation"].keys()) == {
        "entities",
        "required_fields",
        "field_validation_rules",
        "duplicate_check_rules",
        "save_guard_rules",
        "exception_rules",
        "command_boundary_hints",
    }


def _run_rebuild_case(monkeypatch, *, run_id: str, goal: str, assets, temp_context: str = ""):
    from mellow_link import app_state
    from mellow_link.modules.rebuild_assistant import runner as rebuild_runner

    events = []

    class InlineThread:
        def __init__(self, target=None, daemon=None, *args, **kwargs):
            self._target = target

        def start(self):
            if self._target:
                self._target()

    def fake_emit(run_id_arg, event_type, payload, **kwargs):
        events.append({"run_id": run_id_arg, "type": event_type, "payload": payload})

    monkeypatch.setattr(rebuild_runner.threading, "Thread", InlineThread)
    monkeypatch.setattr(rebuild_runner, "emit_event", fake_emit)
    monkeypatch.setattr(app_state, "TEMP_CONTEXT_STORE", {"rebuild-regression-temp": temp_context}, raising=False)

    rebuild_runner.start_rebuild_assistant_run(
        run_id=run_id,
        session_id="session-test",
        goal=goal,
        assets=assets,
        constraints=[],
        temp_session_id="rebuild-regression-temp" if temp_context else None,
    )

    finished = [event for event in events if event["type"] == "run_finished"]
    assert len(finished) == 1
    return finished[0]["payload"]


def test_rebuild_assistant_regression_status_permissions_mode(monkeypatch):
    from mellow_link.modules.rebuild_assistant.schemas import RebuildAssetsPayload

    payload = _run_rebuild_case(
        monkeypatch,
        run_id="run_rebuild_regression_status",
        goal="결재 상태와 권한에 따라 액션이 바뀌는 JSP 화면을 재구성해줘",
        assets=RebuildAssetsPayload(
            source_code="""
if ("APPROVED".equals(status) || userRole.equals("ADMIN")) { showApproveButton = true; }
if ("PENDING".equals(status)) { showRejectButton = true; }
if ("REJECTED".equals(status)) { showResubmitButton = true; }
            """,
            ui_template="""
<c:if test="${sessionScope.role eq 'ADMIN'}"><button>Approve</button></c:if>
<c:if test="${item.status eq 'PENDING'}"><button>Reject</button></c:if>
<c:if test="${item.status eq 'REJECTED'}"><button>Resubmit</button></c:if>
            """,
        ),
    )

    structured = payload["structured_result"]
    assert payload["primary_feature_mode"] == "status_permissions"
    assert set(structured.keys()) == {
        "one_line_conclusion",
        "analysis_summary",
        "rebuild_strategy",
        "layer_reconstruction",
        "recomposition_draft",
        "risks",
        "extracted_rules",
        "confidence",
        "missing_context",
    }
    rules = structured["extracted_rules"]["status_permissions"]
    assert rules["roles"]
    assert rules["statuses"]
    assert rules["actions"]
    assert rules["transition_rules"]
    assert set(structured["extracted_rules"].keys()) == {"status_permissions", "search_filters", "save_validation"}
    assert "정책" in structured["one_line_conclusion"] or "액션" in structured["one_line_conclusion"] or "상태" in structured["one_line_conclusion"]


def test_rebuild_assistant_regression_search_filters_mode(monkeypatch):
    from mellow_link.modules.rebuild_assistant.schemas import RebuildAssetsPayload

    payload = _run_rebuild_case(
        monkeypatch,
        run_id="run_rebuild_regression_search",
        goal="검색 조건이 많은 주문 조회 화면을 재구성해줘",
        assets=RebuildAssetsPayload(
            source_code="""
<form id="searchForm">
String keyword = request.getParameter("keyword");
String statusFilter = request.getParameter("status");
String page = request.getParameter("page");
</form>
<table id="results"></table>
            """,
            sql_queries="""
SELECT * FROM orders
WHERE user_name LIKE ?
AND status = ?
ORDER BY created_at DESC
LIMIT ? OFFSET ?
            """,
        ),
    )

    structured = payload["structured_result"]
    assert payload["primary_feature_mode"] == "search_filters"
    assert set(structured.keys()) == {
        "one_line_conclusion",
        "analysis_summary",
        "rebuild_strategy",
        "layer_reconstruction",
        "recomposition_draft",
        "risks",
        "extracted_rules",
        "confidence",
        "missing_context",
    }
    rules = structured["extracted_rules"]["search_filters"]
    assert rules["filter_fields"]
    assert rules["query_params"]
    assert rules["query_binding_rules"]
    assert rules["sort_rules"] or rules["default_filters"] or rules["result_shape_hints"]
    conclusion = structured["one_line_conclusion"]
    assert "검색" in conclusion or "필터" in conclusion or "쿼리" in conclusion


def test_rebuild_assistant_regression_save_validation_mode(monkeypatch):
    from mellow_link.modules.rebuild_assistant.schemas import RebuildAssetsPayload

    payload = _run_rebuild_case(
        monkeypatch,
        run_id="run_rebuild_regression_save",
        goal="저장 검증과 중복 체크가 많은 등록 기능을 재구성해줘",
        assets=RebuildAssetsPayload(
            source_code="""
if (name == null || name.isBlank()) throw new IllegalArgumentException("required");
if (repository.existsByCode(code)) throw new IllegalStateException("duplicate");
if (!userRole.equals("ADMIN")) throw new SecurityException("forbidden");
repository.save(entity);
            """,
            sql_queries="SELECT count(1) FROM products WHERE code = ?; INSERT INTO products(code, name) VALUES (?, ?);",
        ),
    )

    structured = payload["structured_result"]
    assert payload["primary_feature_mode"] == "save_validation"
    assert set(structured.keys()) == {
        "one_line_conclusion",
        "analysis_summary",
        "rebuild_strategy",
        "layer_reconstruction",
        "recomposition_draft",
        "risks",
        "extracted_rules",
        "confidence",
        "missing_context",
    }
    rules = structured["extracted_rules"]["save_validation"]
    assert rules["required_fields"] or rules["field_validation_rules"]
    assert rules["duplicate_check_rules"]
    assert rules["save_guard_rules"]
    conclusion = structured["one_line_conclusion"]
    assert "검증" in conclusion or "저장" in conclusion or "중복" in conclusion


def test_research_assistant_abort_during_attempt_1_stops_current_run(monkeypatch):
    payload, llm_calls, events = _run_research_abort_case(monkeypatch, "attempt_1")

    assert payload["success"] is False
    assert payload["finish_reason"] == "operator_abort"
    assert payload["failure_reason"] == "aborted_by_user"
    assert payload["abort_requested"] is True
    assert payload["abort_handled"] is True
    assert payload["abort_stage"] == "attempt_1"
    assert llm_calls == 1
    assert not any(event["type"] == "run_finished" and event["payload"].get("success") is True for event in events)


def test_research_assistant_abort_before_attempt_2_skips_retry(monkeypatch):
    payload, llm_calls, events = _run_research_abort_case(monkeypatch, "attempt_2")

    assert payload["success"] is False
    assert payload["finish_reason"] == "operator_abort"
    assert payload["failure_reason"] == "aborted_by_user"
    assert payload["abort_stage"] == "attempt_2"
    assert llm_calls == 1
    assert not any(event["type"] == "run_finished" and event["payload"].get("success") is True for event in events)


def test_research_assistant_abort_before_finalize_skips_result_write(monkeypatch):
    payload, llm_calls, events = _run_research_abort_case(monkeypatch, "finalize")

    assert payload["success"] is False
    assert payload["finish_reason"] == "operator_abort"
    assert payload["failure_reason"] == "aborted_by_user"
    assert payload["abort_stage"] == "finalize"
    assert payload["summary"] == "Run aborted by operator."
    assert llm_calls == 1
    assert not any(event["type"] == "run_finished" and event["payload"].get("success") is True for event in events)
