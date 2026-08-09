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


def _admin_headers():
    from mellow_link.infra.database import SessionLocal
    from mellow_link.infra import User, UserRole, create_default_folders_for_user, create_access_token

    username = f"admin_{uuid.uuid4().hex[:8]}"
    with SessionLocal() as db:
        user = User(username=username, hashed_password="test-hash", role=UserRole.ADMIN.value)
        db.add(user)
        db.commit()
        db.refresh(user)
        create_default_folders_for_user(db, user.id, role=UserRole.ADMIN.value)
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


def test_run_events_hide_debug_anonymization_payload_from_user_api(client):
    from mellow_link.infra.run_events import (
        EVENT_TYPE_DEBUG_ANONYMIZATION_REPORT,
        EVENT_TYPE_RUN_FINISHED,
        emit_event,
    )

    user_headers = _user_headers()
    admin_headers = _admin_headers()
    create_res = client.post(
        "/runs?module_id=rebuild_assistant&run_kind=rebuild_plan",
        headers=user_headers,
    )
    assert create_res.status_code == 200, create_res.text
    run_id = create_res.json()["run_id"]

    emit_event(
        run_id,
        EVENT_TYPE_DEBUG_ANONYMIZATION_REPORT,
        {
            "policy_version": "anonymization-v0-conservative",
            "validation": {
                "passed": True,
                "shape_preserved": True,
                "user_surface_safe": True,
                "findings": [],
            },
            "report_summary": {
                "applied": True,
                "total_replacements": 4,
                "request_context_redacted": True,
                "risk_flags": ["sql_identifiers_pseudonymized"],
            },
            "block_previews": [
                {
                    "block_id": "block:source_code",
                    "kind": "source_code",
                    "replacement_count": 2,
                    "preview_text": "public class CLASS_001 { return fetch(\"API_PATH_001\"); }",
                }
            ],
            "anonymization_report": {
                "policy_version": "anonymization-v0-conservative",
                "request_context_redacted": True,
                "block_reports": [
                    {
                        "block_id": "block:source_code",
                        "replacement_count": 2,
                        "applied_rules": ["CLASS", "API_PATH"],
                    }
                ],
                "asset_reports": [],
                "redaction_summary": {"total_replacements": 4},
                "structure_risk_flags": ["sql_identifiers_pseudonymized"],
            },
        },
    )
    emit_event(
        run_id,
        EVENT_TYPE_RUN_FINISHED,
        {
            "success": True,
            "summary": "익명화 요약 포함",
            "anonymization_summary": {
                "applied": True,
                "policy_version": "anonymization-v0-conservative",
                "total_replacements": 4,
                "request_context_redacted": True,
                "block_counts": [{"block_id": "block:source_code", "replacement_count": 2}],
                "risk_flags": ["sql_identifiers_pseudonymized"],
            },
            "module_id": "rebuild_assistant",
            "run_kind": "rebuild_plan",
        },
    )

    user_events_res = client.get(f"/runs/{run_id}/events?format=json", headers=user_headers)
    assert user_events_res.status_code == 200, user_events_res.text
    user_events = user_events_res.json()["events"]
    assert all(event["type"] != "debug_anonymization_report" for event in user_events)
    user_finished = [event for event in user_events if event["type"] == "run_finished"]
    assert len(user_finished) == 1
    assert "anonymization_summary" in user_finished[0]["payload"]
    assert "anonymization_report" not in user_finished[0]["payload"]
    assert "sanitized_input" not in user_finished[0]["payload"]

    dev_events_res = client.get(f"/api/dev/runs/{run_id}/events", headers=admin_headers)
    assert dev_events_res.status_code == 200, dev_events_res.text
    dev_events = dev_events_res.json()["events"]
    debug_events = [event for event in dev_events if event["type"] == "debug_anonymization_report"]
    assert len(debug_events) == 1
    assert "anonymization_report" in debug_events[0]["payload"]
    assert "sanitized_input" not in debug_events[0]["payload"]


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


def test_input_assembler_v0_emits_neutral_output_contract():
    from mellow_link.modules.rebuild_assistant.input_assembler import InputAssemblerV0
    from mellow_link.modules.rebuild_assistant.schemas import InputAssemblerV0Input

    assembler = InputAssemblerV0()
    output = assembler.assemble(
        InputAssemblerV0Input(
            request_context={
                "goal": "주문 조회 JSP 기능의 현재 구조를 해석한다.",
                "constraints": ["기존 DB 스키마는 유지", "판단 없이 구조 사실만 추출", "기존 DB 스키마는 유지"],
            },
            input_assets=[
                {
                    "asset_id": "asset-source",
                    "origin": "typed_form",
                    "declared_kind": "source_code",
                    "text": "line1\r\nline2\r\n",
                },
                {
                    "asset_id": "asset-ui",
                    "origin": "temp_upload",
                    "declared_kind": "ui_template",
                    "text": "<form>...</form>",
                    "filename": "order.jsp",
                    "source_ref": "temp-1",
                },
                {
                    "asset_id": "asset-unknown",
                    "origin": "temp_upload",
                    "declared_kind": "unknown",
                    "text": "mystery block",
                },
            ],
        )
    )

    dumped = output.model_dump()
    assert dumped["assembler_version"] == "input-assembler-v0"
    assert dumped["request_context"] == {
        "goal": "주문 조회 JSP 기능의 현재 구조를 해석한다.",
        "constraints": ["기존 DB 스키마는 유지", "판단 없이 구조 사실만 추출"],
    }
    assert set(dumped.keys()) == {
        "assembler_version",
        "request_context",
        "asset_inventory",
        "source_blocks",
        "missing_context",
        "unknowns",
    }
    assert [item["asset_id"] for item in dumped["asset_inventory"]] == ["asset-source", "asset-ui", "asset-unknown"]
    assert [block["kind"] for block in dumped["source_blocks"]] == ["source_code", "ui_template", "unclassified_text"]
    assert dumped["source_blocks"][0]["text"] == "line1\nline2"
    assert all("주문 조회 JSP 기능의 현재 구조를 해석한다." not in block["text"] for block in dumped["source_blocks"])
    assert all("기존 DB 스키마는 유지" not in block["text"] for block in dumped["source_blocks"])
    assert {item["code"] for item in dumped["missing_context"]} == {"database_context_missing", "runtime_context_missing"}
    assert {item["code"] for item in dumped["unknowns"]} == {"unknown_asset_kind", "partial_asset_metadata"}


def test_input_assembler_v0_tracks_empty_assets_without_promoting_them():
    from mellow_link.modules.rebuild_assistant.input_assembler import InputAssemblerV0
    from mellow_link.modules.rebuild_assistant.schemas import InputAssemblerV0Input

    assembler = InputAssemblerV0()
    output = assembler.assemble(
        InputAssemblerV0Input(
            request_context={},
            input_assets=[
                {
                    "asset_id": "empty-source",
                    "origin": "typed_form",
                    "declared_kind": "source_code",
                    "text": "   \r\n  ",
                }
            ],
        )
    )

    assert output.source_blocks == []
    assert output.asset_inventory[0].mapped_block_ids == []
    assert {item.code for item in output.unknowns} == {"empty_asset_text"}
    assert {item.code for item in output.missing_context} == {
        "goal_missing",
        "structure_inputs_missing",
        "database_context_missing",
        "runtime_context_missing",
    }


def test_anonymization_layer_v0_preserves_shape_and_pseudonymizes_deterministically():
    from mellow_link.modules.rebuild_assistant.anonymization_layer import AnonymizationLayerV0
    from mellow_link.modules.rebuild_assistant.schemas import InputAssemblerV0Output

    source = InputAssemblerV0Output(
        request_context={
            "goal": "john.doe@example.com 계정의 주문 조회 구조를 분석한다.",
            "constraints": ["내부 서버 https://corp.example.internal 를 외부 공유용으로 정리"],
        },
        asset_inventory=[
            {
                "asset_id": "typed_form:source_code",
                "origin": "typed_form",
                "declared_kind": "source_code",
                "filename": "order_controller.java",
                "source_ref": "temp-session-123",
                "mapped_block_ids": ["block:source_code"],
                "char_count": 120,
            },
            {
                "asset_id": "typed_form:sql_queries",
                "origin": "typed_form",
                "declared_kind": "sql_queries",
                "filename": "order_query.sql",
                "mapped_block_ids": ["block:sql_queries"],
                "char_count": 140,
            },
        ],
        source_blocks=[
            {
                "block_id": "block:source_code",
                "kind": "source_code",
                "asset_ids": ["typed_form:source_code"],
                "text": """
public class OrderController {
    public OrderSummary loadOrder() {
        return fetch("/api/orders/list");
    }
}
                """.strip(),
            },
            {
                "block_id": "block:sql_queries",
                "kind": "sql_queries",
                "asset_ids": ["typed_form:sql_queries"],
                "text": """
CREATE TABLE orders (id bigint, user_id varchar(50), status varchar(20));
SELECT o.id, o.user_id, o.status FROM orders o JOIN users u ON u.id = o.user_id ORDER BY o.status DESC
                """.strip(),
            },
        ],
    )

    layer = AnonymizationLayerV0()
    first = layer.sanitize(source)
    second = layer.sanitize(source)

    assert first.model_dump() == second.model_dump()
    assert [item.asset_id for item in first.sanitized_input.asset_inventory] == [
        "typed_form:source_code",
        "typed_form:sql_queries",
    ]
    assert [block.block_id for block in first.sanitized_input.source_blocks] == ["block:source_code", "block:sql_queries"]
    assert first.sanitized_input.asset_inventory[0].filename == "FILE_001.java"
    assert first.sanitized_input.asset_inventory[0].source_ref == "SRCREF_001"
    assert "EMAIL_001" in (first.sanitized_input.request_context.goal or "")
    assert "HOST_001" in first.sanitized_input.request_context.constraints[0]
    assert "CLASS_001" in first.sanitized_input.source_blocks[0].text
    assert "FUNC_001" in first.sanitized_input.source_blocks[0].text
    assert "API_PATH_001" in first.sanitized_input.source_blocks[0].text
    assert "TABLE_001" in first.sanitized_input.source_blocks[1].text
    assert "COL_001" in first.sanitized_input.source_blocks[1].text
    assert "typed_form:source_code" in first.sanitized_input.source_blocks[0].asset_ids
    assert "sql_identifiers_pseudonymized" in first.anonymization_report.structure_risk_flags
    assert "identifier_like_tokens_redacted" in first.anonymization_report.structure_risk_flags
    assert first.anonymization_report.redaction_summary.total_replacements > 0


def test_anonymization_layer_v0_keeps_ambiguous_variable_names_under_conservative_policy():
    from mellow_link.modules.rebuild_assistant.anonymization_layer import AnonymizationLayerV0
    from mellow_link.modules.rebuild_assistant.schemas import InputAssemblerV0Output

    source = InputAssemblerV0Output(
        source_blocks=[
            {
                "block_id": "block:source_code",
                "kind": "source_code",
                "asset_ids": ["typed_form:source_code"],
                "text": 'String userId = request.getParameter("userId"); int count = 1;',
            }
        ]
    )

    output = AnonymizationLayerV0().sanitize(source)

    assert output.sanitized_input.source_blocks[0].text == 'String userId = request.getParameter("userId"); int count = 1;'
    assert output.anonymization_report.redaction_summary.total_replacements == 0
    assert output.anonymization_report.structure_risk_flags == []


def test_anonymization_debug_payload_uses_sanitized_block_preview_and_structured_validation():
    from mellow_link.modules.rebuild_assistant.anonymization_exposure import (
        build_anonymization_debug_payload,
        build_preview_text,
    )
    from mellow_link.modules.rebuild_assistant.anonymization_layer import AnonymizationLayerV0
    from mellow_link.modules.rebuild_assistant.schemas import InputAssemblerV0Output

    source = InputAssemblerV0Output(
        request_context={"goal": "john.doe@example.com 구조를 확인한다.", "constraints": []},
        asset_inventory=[
            {
                "asset_id": "typed_form:source_code",
                "origin": "typed_form",
                "declared_kind": "source_code",
                "mapped_block_ids": ["block:source_code"],
                "char_count": 40,
            }
        ],
        source_blocks=[
            {
                "block_id": "block:source_code",
                "kind": "source_code",
                "asset_ids": ["typed_form:source_code"],
                "text": "public class OrderController {\r\n\tpublic void load() { return fetch(\"/api/orders/list\"); }\r\n}\r\n",
            }
        ],
    )

    anonymization_output = AnonymizationLayerV0().sanitize(source)
    payload = build_anonymization_debug_payload(
        original_input=source,
        anonymization_output=anonymization_output,
    )
    preview = payload.block_previews[0]

    assert preview.preview_text == build_preview_text(anonymization_output.sanitized_input.source_blocks[0].text)
    assert "\n" not in preview.preview_text
    assert "\t" not in preview.preview_text
    assert len(preview.preview_text) <= 160
    assert payload.validation.passed is True
    assert payload.validation.findings == []
    assert payload.anonymization_report.redaction_summary.total_replacements >= 1


def test_anonymization_validation_reports_structured_findings_for_shape_and_visibility_violations():
    from mellow_link.modules.rebuild_assistant.anonymization_exposure import validate_anonymization_exposure
    from mellow_link.modules.rebuild_assistant.schemas import (
        AnonymizationSummary,
        AnonymizationV0Output,
        InputAssemblerV0Output,
    )

    original = InputAssemblerV0Output(
        asset_inventory=[
            {
                "asset_id": "typed_form:source_code",
                "origin": "typed_form",
                "declared_kind": "source_code",
                "mapped_block_ids": ["block:source_code"],
                "char_count": 10,
            }
        ],
        source_blocks=[
            {
                "block_id": "block:source_code",
                "kind": "source_code",
                "asset_ids": ["typed_form:source_code"],
                "text": "legacy text",
            }
        ],
    )
    mutated = InputAssemblerV0Output(
        asset_inventory=[
            {
                "asset_id": "typed_form:source_code",
                "origin": "typed_form",
                "declared_kind": "source_code",
                "mapped_block_ids": ["block:sql_queries"],
                "char_count": 10,
            }
        ],
        source_blocks=[
            {
                "block_id": "block:sql_queries",
                "kind": "sql_queries",
                "asset_ids": ["typed_form:source_code"],
                "text": "sanitized text",
            }
        ],
    )

    validation = validate_anonymization_exposure(
        original_input=original,
        anonymization_output=AnonymizationV0Output(sanitized_input=mutated),
        user_summary=AnonymizationSummary(),
        debug_event_type="log",
    )

    assert validation.passed is False
    assert validation.shape_preserved is False
    assert validation.user_surface_safe is True
    assert {finding.code for finding in validation.findings} == {
        "shape_not_preserved",
        "asset_links_changed",
        "dev_event_visible_in_user_stream",
    }


def test_rebuild_assistant_prepare_input_exposes_assembler_output():
    from mellow_link.modules.rebuild_assistant.schemas import RebuildAssetsPayload
    from mellow_link.modules.rebuild_assistant.service import RebuildAssistantService

    svc = RebuildAssistantService()
    prepared = svc.prepare_input(
        goal="이 JSP 주문 조회 화면을 React + REST API로 재구성해줘",
        assets=RebuildAssetsPayload(
            source_code="<% String userId = request.getParameter(\"userId\"); %>",
            sql_queries="SELECT * FROM orders WHERE user_id = ?",
        ),
        constraints=["기존 DB 스키마는 유지"],
        temp_context="--- [legacy.jsp] ---\n<form><input name=\"userId\" /></form>",
        temp_source_ref="rebuild-temp",
    )

    assert prepared.assembler_output is not None
    assert prepared.anonymization_output is not None
    assert prepared.assembler_output.request_context.goal == "이 JSP 주문 조회 화면을 React + REST API로 재구성해줘"
    assert prepared.assembler_output.request_context.constraints == ["기존 DB 스키마는 유지"]
    assert [block.kind for block in prepared.assembler_output.source_blocks] == [
        "source_code",
        "sql_queries",
        "unclassified_text",
    ]
    assert prepared.temp_context == "--- [legacy.jsp] ---\n<form><input name=\"userId\" /></form>"
    assert prepared.legacy_bundle == "\n\n".join(block.text for block in prepared.anonymization_output.sanitized_input.source_blocks)
    assert all(prepared.goal not in block.text for block in prepared.assembler_output.source_blocks)
    assert set(item.code for item in prepared.assembler_output.missing_context) == {"runtime_context_missing"}


def test_rebuild_assistant_prepare_input_uses_sanitized_bundle_for_downstream_analysis():
    from mellow_link.modules.rebuild_assistant.schemas import RebuildAssetsPayload
    from mellow_link.modules.rebuild_assistant.service import RebuildAssistantService

    svc = RebuildAssistantService()
    prepared = svc.prepare_input(
        goal="john.doe@example.com 계정의 주문 조회 기능을 재구성해줘",
        assets=RebuildAssetsPayload(
            source_code="""
public class OrderController {
    public OrderSummary loadOrder() {
        return fetch("/api/orders/list");
    }
}
            """.strip(),
            sql_queries="SELECT id, user_id, status FROM orders WHERE user_id = ? ORDER BY status DESC",
        ),
        constraints=["내부 서버 https://corp.example.internal 기준"],
        temp_context="로그 파일 경로: C:\\legacy\\orders\\order_list.jsp",
        temp_source_ref="temp-session-123",
    )

    assert prepared.anonymization_output is not None
    sanitized_input = prepared.anonymization_output.sanitized_input
    assert "EMAIL_001" in prepared.goal
    assert "HOST_001" in prepared.constraints[0]
    assert "API_PATH_001" in prepared.legacy_bundle
    assert "TABLE_001" in prepared.legacy_bundle
    assert "PATH_001" in prepared.temp_context
    assert "/api/orders/list" not in prepared.legacy_bundle
    assert " FROM orders " not in prepared.legacy_bundle
    assert prepared.legacy_bundle == "\n\n".join(block.text for block in sanitized_input.source_blocks if block.text)


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
    assert set(payload["anonymization_summary"].keys()) == {
        "applied",
        "policy_version",
        "total_replacements",
        "request_context_redacted",
        "block_counts",
        "risk_flags",
    }
    assert "anonymization_report" not in payload
    assert "sanitized_input" not in payload
    debug_events = [event for event in events if event["type"] == "debug_anonymization_report"]
    assert len(debug_events) == 1
    debug_payload = debug_events[0]["payload"]
    assert set(debug_payload.keys()) == {
        "policy_version",
        "validation",
        "report_summary",
        "block_previews",
        "anonymization_report",
    }
    assert "sanitized_input" not in debug_payload
    assert isinstance(debug_payload["validation"]["findings"], list)
    assert isinstance(debug_payload["block_previews"], list)
    assert debug_payload["validation"]["passed"] is True
    assert all(
        set(item.keys()) == {"block_id", "kind", "replacement_count", "preview_text"}
        for item in debug_payload["block_previews"]
    )
    anonymization_logs = [
        event for event in events
        if event["type"] == "log" and event["payload"].get("message") == "anonymization complete"
    ]
    assert len(anonymization_logs) == 1
    assert set(anonymization_logs[0]["payload"].keys()) == {
        "level",
        "message",
        "policy_version",
        "applied",
        "total_replacements",
        "validation_passed",
    }
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
