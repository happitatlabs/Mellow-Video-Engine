from __future__ import annotations

import re
from dataclasses import dataclass, field

from .schemas import (
    ExtractedRulesEnvelope,
    LayeredListResult,
    RebuildAssetsPayload,
    SaveValidationRules,
    SearchFilterRules,
    StatusPermissionsRules,
    StructuredRebuildResult,
)


@dataclass
class FeatureSignals:
    concepts: list[str] = field(default_factory=list)
    status_permissions: list[str] = field(default_factory=list)
    search_filters: list[str] = field(default_factory=list)
    save_validation: list[str] = field(default_factory=list)
    technical: list[str] = field(default_factory=list)
    scores: dict[str, float] = field(default_factory=dict)
    primary_feature_mode: str = "general"
    secondary_feature_mode: str | None = None


@dataclass
class PreparedRebuildInput:
    goal: str
    assets: RebuildAssetsPayload
    constraints: list[str]
    temp_context: str = ""
    legacy_bundle: str = ""
    scope_limited: bool = False
    missing_context: list[str] | None = None
    signals: FeatureSignals = field(default_factory=FeatureSignals)

    def __post_init__(self) -> None:
        if self.missing_context is None:
            self.missing_context = []


class RebuildAssistantService:
    SCOPE_LIMIT_PATTERNS = (
        r"whole[\s-]?system",
        r"entire system",
        r"full[\s-]?site",
        r"multi[\s-]?service",
        r"microservice",
        r"deployable",
        r"production[\s-]?ready",
        r"full[\s-]?database migration",
        r"full migration",
        r"전체\s*시스템",
        r"전체\s*사이트",
        r"전면\s*재구축",
        r"멀티\s*서비스",
        r"마이크로서비스",
        r"배포\s*가능",
        r"실행\s*가능한\s*전체\s*코드",
        r"전체\s*데이터베이스\s*마이그레이션",
    )
    CONCEPT_PATTERNS = (
        "order", "orders", "payment", "invoice", "approval", "request", "requests",
        "user", "member", "account", "product", "item", "customer", "notice",
        "document", "contract", "shipment", "refund", "claim", "board", "post",
        "comment", "report", "schedule", "booking", "reservation", "employee",
        "role", "status", "policy", "audit", "search", "filter", "query",
        "save", "submit", "validation",
        "주문", "결재", "요청", "사용자", "회원", "상품", "고객", "문서", "계약", "환불",
        "게시판", "댓글", "보고서", "예약", "직원", "권한", "상태", "정책", "감사", "검색",
        "필터", "조회", "저장", "등록", "검증",
    )

    def prepare_input(
        self,
        *,
        goal: str,
        assets: RebuildAssetsPayload,
        constraints: list[str] | None = None,
        temp_context: str = "",
    ) -> PreparedRebuildInput:
        constraints = [(item or "").strip() for item in (constraints or []) if (item or "").strip()]
        cleaned_assets = RebuildAssetsPayload(
            source_code=(assets.source_code or "").strip(),
            database_schema=(assets.database_schema or "").strip(),
            sql_queries=(assets.sql_queries or "").strip(),
            ui_template=(assets.ui_template or "").strip(),
            framework_info=(assets.framework_info or "").strip(),
        )
        parts = [
            self._section("Source Code", cleaned_assets.source_code),
            self._section("Database Schema", cleaned_assets.database_schema),
            self._section("SQL Queries", cleaned_assets.sql_queries),
            self._section("UI Template", cleaned_assets.ui_template),
            self._section("Framework Info", cleaned_assets.framework_info),
            self._section("Uploaded Context", (temp_context or "").strip()),
        ]
        prepared = PreparedRebuildInput(
            goal=(goal or "").strip(),
            assets=cleaned_assets,
            constraints=constraints,
            temp_context=(temp_context or "").strip(),
            legacy_bundle="\n\n".join(part for part in parts if part),
            scope_limited=self.is_scope_limited(goal),
        )
        prepared.signals = self.extract_feature_signals(prepared)
        prepared.missing_context = self.detect_missing_context(prepared)
        return prepared

    def is_scope_limited(self, goal: str) -> bool:
        text = (goal or "").strip().lower()
        return bool(text) and any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in self.SCOPE_LIMIT_PATTERNS)

    def detect_missing_context(self, prepared: PreparedRebuildInput) -> list[str]:
        missing: list[str] = []
        if not prepared.assets.source_code and not prepared.assets.ui_template and not prepared.temp_context:
            missing.append("레거시 화면 또는 서버 코드가 부족합니다.")
        if not prepared.assets.database_schema and not prepared.assets.sql_queries:
            missing.append("DB 스키마 또는 SQL 쿼리 정보가 부족합니다.")
        if not prepared.assets.framework_info:
            missing.append("기존 프레임워크/런타임 정보가 부족합니다.")
        if not prepared.signals.status_permissions and not prepared.signals.search_filters and not prepared.signals.save_validation:
            missing.append("핵심 기능 흐름(권한/조회/저장 규칙)을 드러내는 코드 단서가 더 필요합니다.")
        return missing

    def extract_feature_signals(self, prepared: PreparedRebuildInput) -> FeatureSignals:
        bundle = prepared.legacy_bundle.lower()
        concepts = self._extract_concepts(prepared)
        status_permissions = self._extract_status_permission_signals(bundle)
        search_filters = self._extract_search_filter_signals(bundle)
        save_validation = self._extract_save_validation_signals(bundle)
        technical = []
        if self._looks_like_jsp(prepared):
            technical.append("JSP/서버 템플릿 렌더링")
        if self._contains_sql_in_ui(prepared):
            technical.append("UI 근접 SQL 결합")
        if "request.getparameter" in bundle or "param." in bundle:
            technical.append("request parameter 기반 흐름")
        if self._has_join_heaviness(prepared.assets.sql_queries):
            technical.append("복합 조인 쿼리")
        scores = self._score_feature_modes(
            prepared,
            status_permissions=status_permissions,
            search_filters=search_filters,
            save_validation=save_validation,
        )
        primary_mode, secondary_mode = self._pick_feature_modes(scores)
        return FeatureSignals(
            concepts=concepts,
            status_permissions=status_permissions,
            search_filters=search_filters,
            save_validation=save_validation,
            technical=technical,
            scores=scores,
            primary_feature_mode=primary_mode,
            secondary_feature_mode=secondary_mode,
        )

    def analyze_assets(self, prepared: PreparedRebuildInput) -> list[str]:
        findings: list[str] = []
        if self._looks_like_jsp(prepared):
            findings.append("JSP/서버 템플릿 기반 UI로 추정되며 프레젠테이션과 서버 책임이 섞여 있습니다.")
        if self._contains_sql_in_ui(prepared):
            findings.append("SQL 또는 데이터 접근 로직이 UI/템플릿과 가깝게 결합되어 있습니다.")
        if prepared.signals.concepts:
            findings.append(f"주요 도메인 개념은 {', '.join(prepared.signals.concepts[:3])} 중심으로 보입니다.")
        if prepared.signals.status_permissions:
            findings.append(
                "status_permissions 신호가 보여 역할/상태/가능 액션 표시가 화면 분기와 섞여 있으며 정책 추출이 필요합니다: "
                + ", ".join(prepared.signals.status_permissions[:3])
            )
        if prepared.signals.search_filters:
            findings.append(
                "search_filters 신호가 보여 조회 조건, 검색 파라미터, 동적 쿼리 조합이 한 흐름에 묶여 있습니다: "
                + ", ".join(prepared.signals.search_filters[:3])
            )
        if prepared.signals.save_validation:
            findings.append(
                "save_validation 신호가 보여 저장 전 검증, 중복 체크, 저장 가드가 화면/서비스 경계 없이 퍼져 있습니다: "
                + ", ".join(prepared.signals.save_validation[:3])
            )
        if prepared.assets.database_schema:
            findings.append("기존 스키마 호환성을 유지해야 하므로 API/백엔드 분리 시 DB 계약을 우선 보존해야 합니다.")
        if not findings:
            findings.append("제공된 자산 범위에서는 단일 기능 수준의 레거시 웹 화면과 데이터 접근 계층이 함께 얽혀 있는 것으로 보입니다.")
        return findings[:6]

    def infer_target_architecture(self, prepared: PreparedRebuildInput) -> list[str]:
        concept = self._primary_concept(prepared)
        resource = self._resource_name(prepared)
        primary = prepared.signals.primary_feature_mode
        secondary = prepared.signals.secondary_feature_mode
        strategy = [
            f"{concept} 기능을 단일 범위에서 UI, API, DB 접근 책임으로 분리한 layered architecture로 재구성합니다.",
            f"프론트엔드는 React 기반 `{self._page_name(prepared)}`와 하위 컴포넌트로 나누고 `{resource}` API를 기준으로 상태를 관리합니다.",
            f"백엔드는 `{resource}` 전용 controller/service/repository 경계로 나눠 기존 SQL 의존도를 단계적으로 축소합니다.",
        ]
        if primary == "status_permissions":
            strategy.append("status_permissions를 주 모드로 보고 role/status/action visibility와 상태 전이 규칙을 policy layer로 추출합니다.")
        elif primary == "search_filters":
            strategy[1] = f"프론트엔드는 React 기반 `{self._page_name(prepared)}`와 `{self._filter_bar_name(prepared)}`, `{self._results_table_name(prepared)}`로 나누고 검색 상태를 관리합니다."
            strategy[2] = f"백엔드는 `{self._query_dto_name(prepared)}`와 `{self._query_mapper_name(prepared)}`를 기준으로 검색 API와 SQL parameterization 규칙을 분리합니다."
            strategy.append("search_filters를 주 모드로 보고 query parameters, search filter state, search API, SQL parameterization 규칙을 조회 모델로 분리합니다.")
        elif primary == "save_validation":
            strategy.append("save_validation을 주 모드로 보고 validation rules, save guards, duplicate checks를 command/DTO와 validator 경계로 분리합니다.")
        if secondary == "status_permissions":
            strategy.append("보조 신호로 status_permissions가 감지되어 액션 노출과 상태 전이 규칙도 함께 정리합니다.")
        elif secondary == "search_filters":
            strategy.append("보조 신호로 search_filters가 감지되어 필터 상태와 조회 파라미터 정규화도 함께 반영합니다.")
        elif secondary == "save_validation":
            strategy.append("보조 신호로 save_validation이 감지되어 저장 가드와 중복 체크도 함께 반영합니다.")
        if prepared.scope_limited:
            strategy.insert(0, "요청 범위가 V0 한계를 넘으므로 전체 마이그레이션 대신 단일 기능 재구성 전략으로 축소합니다.")
        if prepared.constraints:
            strategy.append(f"제약 조건 반영: {prepared.constraints[0]}")
        return strategy[:6]

    def build_layer_reconstruction(self, prepared: PreparedRebuildInput) -> LayeredListResult:
        resource = self._resource_name(prepared)
        primary = prepared.signals.primary_feature_mode
        secondary = prepared.signals.secondary_feature_mode
        database = [
            f"`{resource}` 관련 테이블/컬럼 계약은 유지하되 조회와 저장 책임을 repository 계층으로 이동합니다.",
        ]
        if primary == "search_filters":
            database.append("query parameter와 검색 필터 조합을 SQL parameterization 규칙으로 정리하고 동적 문자열 결합을 제거합니다.")
        elif primary == "save_validation":
            database.append("중복 체크와 저장 전 선행 조회는 저장 커맨드와 분리된 제약 검사 쿼리로 정리합니다.")
        elif primary == "status_permissions":
            database.append("상태 전이와 권한 판정에 필요한 status/action 기준 컬럼은 읽기 모델에서 명시적으로 조회합니다.")
        else:
            database.append("복잡한 조인/조건식은 재사용 가능한 query method 또는 view 수준으로 정리합니다.")
        if secondary == "save_validation" and primary != "save_validation":
            database.append("중복 체크와 저장 전 선행 조회는 저장 커맨드와 분리된 제약 검사 쿼리로 정리합니다.")
        elif secondary == "search_filters" and primary != "search_filters":
            database.append("보조 조회 신호를 반영해 주요 검색 조건은 파라미터 바인딩으로 고정합니다.")
        if prepared.assets.database_schema or prepared.assets.sql_queries:
            database.append("스키마 변경은 최소화하고 V0에서는 호환 레이어를 우선 설계합니다.")

        backend = [
            f"`{resource}` 기능 전용 REST endpoint와 service layer를 분리합니다.",
        ]
        if primary == "status_permissions":
            backend.append("role/status/action visibility 규칙을 policy service 또는 authorization rule 객체로 추출합니다.")
            backend.append("상태 전이 허용 여부를 transition policy로 분리해 화면 분기와 저장 로직에서 공용 사용합니다.")
        elif primary == "search_filters":
            backend[0] = f"`{resource}` 검색 전용 API와 service layer를 분리합니다."
            backend.append(f"검색/필터 입력은 `{self._query_dto_name(prepared)}`로 정규화하고 정렬, 페이징, 조건식을 명시적으로 매핑합니다.")
            backend.append(f"동적 검색 조건은 `{self._query_mapper_name(prepared)}` 또는 명시적 criteria mapper로 관리합니다.")
        elif primary == "save_validation":
            backend.append("save guard, duplicate check, validation rule을 command DTO와 validator 계층으로 분리합니다.")
            backend.append("저장 전 제약 검사는 command handler 진입 전에 수행하고, 저장 후처리와 분리합니다.")
        if secondary == "status_permissions" and primary != "status_permissions":
            backend.append("보조 정책 신호를 반영해 주요 액션 노출 조건은 policy service에서 계산합니다.")
        elif secondary == "search_filters" and primary != "search_filters":
            backend.append("보조 조회 신호를 반영해 query DTO를 함께 둡니다.")
        elif secondary == "save_validation" and primary != "save_validation":
            backend.append("보조 저장 신호를 반영해 핵심 저장 경로에 validator를 둡니다.")
        if primary == "general":
            backend.append("서비스 계층에서 JSP 내 조건문과 분기 로직을 명시적 비즈니스 규칙으로 추출합니다.")

        frontend = [
            f"`{self._page_name(prepared)}`를 중심으로 페이지 컨테이너와 하위 UI 컴포넌트를 분리합니다.",
        ]
        if primary == "status_permissions":
            frontend.append("사용자 역할과 엔티티 상태에 따른 버튼 노출/비활성화 규칙을 UI policy hook으로 분리합니다.")
        elif primary == "search_filters":
            frontend[0] = f"`{self._page_name(prepared)}`를 중심으로 `{self._filter_bar_name(prepared)}`와 `{self._results_table_name(prepared)}`를 분리합니다."
            frontend.append("검색 필터 상태, 폼 값, 결과 목록 상태를 별도 query state 모델로 관리합니다.")
        elif primary == "save_validation":
            frontend.append("저장 폼 검증 메시지와 제출 가드를 view model 또는 form schema 기준으로 분리합니다.")
        if secondary == "status_permissions" and primary != "status_permissions":
            frontend.append("보조 정책 신호를 반영해 액션 버튼 가시성 계산을 분리합니다.")
        elif secondary == "search_filters" and primary != "search_filters":
            frontend.append("보조 조회 신호를 반영해 필터 상태를 별도 query state로 유지합니다.")
        elif secondary == "save_validation" and primary != "save_validation":
            frontend.append("보조 저장 신호를 반영해 제출 전 검증 메시지를 분리합니다.")
        if not (prepared.assets.source_code or prepared.assets.ui_template):
            frontend = ["화면 자산이 부족하므로 프론트엔드는 API 계약 기준의 최소 컴포넌트 분해만 제안합니다."]
        return LayeredListResult(database=database[:4], backend=backend[:4], frontend=frontend[:4])

    def build_recomposition_draft(self, prepared: PreparedRebuildInput) -> LayeredListResult:
        resource = self._resource_name(prepared)
        page = self._page_name(prepared)
        singular = self._singular_resource(resource)
        primary = prepared.signals.primary_feature_mode
        secondary = prepared.signals.secondary_feature_mode

        database = [
            f"예시: `{resource}_repository.search()`와 `{resource}_repository.save_{singular}()`로 조회와 저장 경로를 분리합니다.",
        ]
        if primary == "search_filters":
            database.append(f"예시: `{resource}_search_params` 매핑 규칙을 두고 WHERE 절은 바인딩 파라미터로만 조립합니다.")
        elif primary == "save_validation":
            database.append(f"예시: 저장 전 `{singular}` 중복 여부와 상태 충돌을 확인하는 guard query를 분리합니다.")
        elif primary == "status_permissions":
            database.append(f"예시: `{resource}_status_view` 또는 projection에서 role/status/action visibility 계산에 필요한 상태 컬럼을 묶어 조회합니다.")
        if secondary == "search_filters" and primary != "search_filters":
            database.append(f"예시: 보조 조회 신호를 반영해 `{resource}_search_params`를 함께 둡니다.")
        elif secondary == "save_validation" and primary != "save_validation":
            database.append(f"예시: 보조 저장 신호를 반영해 `{singular}` 중복 guard query를 추가합니다.")
        if not prepared.assets.database_schema and not prepared.assets.sql_queries:
            database = [f"DB 자산이 부족하므로 `{resource}` 저장소 인터페이스 초안과 파라미터 계약만 제공합니다."]

        backend = [
            f"예시: `GET /api/{resource}`, `GET /api/{resource}/{{id}}`, `POST /api/{resource}` 형태로 `{resource}` API 초안을 둡니다.",
        ]
        if primary == "status_permissions":
            backend.append(f"예시: `{singular}_policy_service.can_transition(role, status, action)`로 role/status/action visibility를 계산합니다.")
            backend.append(f"예시: `{singular}_transition_policy`에서 승인/반려/취소 같은 상태 전이 규칙을 분리합니다.")
        elif primary == "search_filters":
            backend[0] = f"예시: `GET /api/{resource}/search`와 `GET /api/{resource}/{{id}}` 형태로 `{resource}` 검색 API 초안을 둡니다."
            backend.append(f"예시: `{self._query_dto_name(prepared)}`로 query parameters, search filter state, paging/sort 조건을 수집합니다.")
            backend.append(f"예시: `{self._query_mapper_name(prepared)}`에서 SQL parameterization과 criteria mapping을 담당합니다.")
        elif primary == "save_validation":
            backend.append(f"예시: `{page}CommandDTO`와 validator에서 save guards, duplicate checks, business rule validation을 분리합니다.")
            backend.append(f"예시: `{singular}_command_handler`에서 검증 완료 후 저장만 담당하도록 분리합니다.")
        if secondary == "status_permissions" and primary != "status_permissions":
            backend.append(f"예시: 보조 정책 신호를 반영해 `{singular}_policy_service`를 함께 둡니다.")
        elif secondary == "search_filters" and primary != "search_filters":
            backend.append(f"예시: 보조 조회 신호를 반영해 `{page}QueryDTO`를 함께 둡니다.")
        elif secondary == "save_validation" and primary != "save_validation":
            backend.append(f"예시: 보조 저장 신호를 반영해 `{page}CommandDTO`와 validator를 함께 둡니다.")
        if prepared.scope_limited:
            backend.insert(0, "전체 코드 생성 대신 단일 기능 endpoint 초안만 제공합니다.")

        frontend = [
            f"예시: `{page}`, `{page}Table`, `{page}DetailPanel` 구성으로 화면 골격을 나눕니다.",
        ]
        if primary == "search_filters":
            frontend[0] = f"예시: `{self._page_name(prepared)}`, `{self._filter_bar_name(prepared)}`, `{self._results_table_name(prepared)}` 구성으로 검색 화면 골격을 나눕니다."
            frontend.append(f"예시: `{self._filter_bar_name(prepared)}`와 query state hook으로 검색 필터/조회 조건을 관리합니다.")
        elif primary == "status_permissions":
            frontend.append(f"예시: `{page}ActionButtons`에서 role/status/action visibility 규칙을 해석해 버튼을 노출합니다.")
        elif primary == "save_validation":
            frontend.append(f"예시: `{page}Form`에서 field validation, duplicate warning, submit guard를 분리합니다.")
        if secondary == "search_filters" and primary != "search_filters":
            frontend.append(f"예시: 보조 조회 신호를 반영해 `{page}FilterBar`를 함께 둡니다.")
        elif secondary == "status_permissions" and primary != "status_permissions":
            frontend.append(f"예시: 보조 정책 신호를 반영해 `{page}ActionButtons`를 함께 둡니다.")
        elif secondary == "save_validation" and primary != "save_validation":
            frontend.append(f"예시: 보조 저장 신호를 반영해 `{page}Form` 검증 로직을 함께 둡니다.")

        return LayeredListResult(database=database[:4], backend=backend[:4], frontend=frontend[:4])

    def build_risks(self, prepared: PreparedRebuildInput) -> list[str]:
        risks = []
        if prepared.signals.status_permissions:
            risks.append("권한/상태 전이 규칙이 숨겨져 있으면 액션 노출 조건을 잘못 재현할 수 있습니다.")
        if prepared.signals.search_filters:
            risks.append("검색/필터 조합이 많으면 기존 결과 정렬과 누락 없는 SQL parameterization 검증이 필요합니다.")
        if prepared.signals.save_validation:
            risks.append("저장 시점의 중복 체크와 상태 가드를 놓치면 운영 데이터 무결성이 깨질 수 있습니다.")
        if not risks:
            risks.extend(
                [
                    "인증/권한 흐름이 자산에 드러나지 않으면 실제 API 분리 시 추가 분석이 필요합니다.",
                    "화면 이벤트와 DB 규칙이 강하게 결합된 경우 일부 비즈니스 규칙이 누락될 수 있습니다.",
                ]
            )
        if prepared.missing_context:
            risks.append("입력 자산이 제한적이므로 제안은 설계 초안 수준이며 추가 파일 확인이 필요합니다.")
        return risks[:4]

    def extract_rules(self, prepared: PreparedRebuildInput) -> ExtractedRulesEnvelope:
        primary = prepared.signals.primary_feature_mode
        secondary = prepared.signals.secondary_feature_mode
        envelope = ExtractedRulesEnvelope()
        if primary == "status_permissions":
            envelope.status_permissions = self.extract_status_permissions_rules(prepared)
        elif primary == "search_filters":
            envelope.search_filters = self.extract_search_filter_rules(prepared)
        elif primary == "save_validation":
            envelope.save_validation = self.extract_save_validation_rules(prepared)

        if secondary == "status_permissions" and primary != "status_permissions":
            envelope.status_permissions = self.extract_status_permissions_rules(prepared, supplemental=True)
        elif secondary == "search_filters" and primary != "search_filters":
            envelope.search_filters = self.extract_search_filter_rules(prepared, supplemental=True)
        elif secondary == "save_validation" and primary != "save_validation":
            envelope.save_validation = self.extract_save_validation_rules(prepared, supplemental=True)
        return envelope

    def extract_status_permissions_rules(
        self,
        prepared: PreparedRebuildInput,
        supplemental: bool = False,
    ) -> StatusPermissionsRules:
        text = prepared.legacy_bundle
        roles = [role.upper() for role in self._extract_unique_matches(text, r"\b(admin|manager|user|operator|guest|owner|reviewer|approver)\b")]
        statuses = [status.upper() for status in self._extract_unique_matches(text, r"\b(pending|approved|rejected|draft|submitted|active|inactive|closed|cancelled)\b")]
        actions = self._extract_unique_matches(text, r"\b(approve|reject|resubmit|cancel|submit|close|reopen)\b")
        entities = self._rule_entities(prepared)

        role_action_matrix: list[dict] = []
        for role in roles:
            allowed_actions = [action for action in actions if re.search(rf"{role}.*{action}|{action}.*{role}", text, flags=re.IGNORECASE)]
            if allowed_actions:
                role_action_matrix.append({"role": role, "allowed_actions": self._dedupe_list(allowed_actions)})

        status_action_matrix: list[dict] = []
        for status in statuses:
            visible_actions = [action for action in actions if re.search(rf"{status}.*{action}|{action}.*{status}", text, flags=re.IGNORECASE)]
            if visible_actions:
                status_action_matrix.append({"status": status, "visible_actions": self._dedupe_list(visible_actions)})

        transition_rules: list[dict] = []
        for status in statuses:
            for action in actions:
                if not re.search(rf"{status}.*{action}|{action}.*{status}", text, flags=re.IGNORECASE):
                    continue
                condition = self._transition_condition_hint(text, roles, status, action)
                transition_rules.append(
                    {
                        "from_status": status,
                        "action": action,
                        "to_status": self._infer_target_status(action, statuses),
                        "condition": condition,
                    }
                )

        ui_visibility_rules: list[str] = []
        if actions and statuses:
            for action in actions[:3]:
                related_statuses = [
                    status for status in statuses if re.search(rf"{status}.*{action}|{action}.*{status}", text, flags=re.IGNORECASE)
                ]
                related_roles = [
                    role for role in roles if re.search(rf"{role}.*{action}|{action}.*{role}", text, flags=re.IGNORECASE)
                ]
                if related_statuses or related_roles:
                    role_fragment = f" and role is {' or '.join(related_roles)}" if related_roles else ""
                    status_fragment = f"when status is {' or '.join(related_statuses)}" if related_statuses else "when action state is satisfied"
                    ui_visibility_rules.append(f"show {action} button only {status_fragment}{role_fragment}".strip())
        if not ui_visibility_rules and re.search(r"<c:if|<c:choose|if\s*\(", text, flags=re.IGNORECASE):
            ui_visibility_rules.append("conditional action visibility is embedded in JSP or server-side branches")

        policy_hints: list[str] = []
        if roles or actions or statuses:
            policy_hints.append("extract role/action visibility into policy service")
        if transition_rules:
            policy_hints.append("extract state transition checks into transition policy")
        if re.search(r"<c:if|<c:choose|if\s*\(", text, flags=re.IGNORECASE):
            policy_hints.append("move conditional button rendering rules out of the view layer")

        if supplemental:
            role_action_matrix = role_action_matrix[:1]
            status_action_matrix = status_action_matrix[:1]
            transition_rules = transition_rules[:1]
            ui_visibility_rules = ui_visibility_rules[:1]
            policy_hints = policy_hints[:1]

        return StatusPermissionsRules(
            entities=entities,
            roles=roles,
            statuses=statuses,
            actions=actions,
            role_action_matrix=role_action_matrix,
            status_action_matrix=status_action_matrix,
            transition_rules=self._dedupe_dicts(transition_rules),
            ui_visibility_rules=self._dedupe_list(ui_visibility_rules),
            policy_hints=self._dedupe_list(policy_hints),
        )

    def extract_search_filter_rules(
        self,
        prepared: PreparedRebuildInput,
        supplemental: bool = False,
    ) -> SearchFilterRules:
        text = prepared.legacy_bundle
        entities = self._rule_entities(prepared)
        query_params = self._extract_unique_matches(
            text,
            r"request\.getParameter\(\"([^\"]+)\"\)|@RequestParam\(\"([^\"]+)\"\)|\b(keyword|status|page|sort|dateFrom|dateTo|category|region|includeClosed|filter)\b",
        )

        filter_fields: list[dict] = []
        for name in query_params:
            field_type = self._infer_filter_field_type(name)
            filter_fields.append({"name": name, "type": field_type, "required": False})

        sort_rules: list[dict] = []
        sql_text = prepared.assets.sql_queries or text
        order_match = re.search(r"order\s+by\s+([a-z0-9_\.]+)(?:\s+(asc|desc))?", sql_text, flags=re.IGNORECASE)
        if order_match:
            sort_rules.append(
                {
                    "field": order_match.group(1).split(".")[-1],
                    "direction": (order_match.group(2) or "asc").lower(),
                    "default": True,
                }
            )

        paging_rules: list[dict] = []
        if re.search(r"\blimit\b", sql_text, flags=re.IGNORECASE):
            paging_rules.append({"param": "limit", "style": "limit", "default": False})
        if re.search(r"\boffset\b", sql_text, flags=re.IGNORECASE):
            paging_rules.append({"param": "offset", "style": "offset", "default": False})
        if any(param.lower() == "page" for param in query_params):
            paging_rules.append({"param": "page", "style": "page", "default": False})

        query_binding_rules: list[str] = []
        if query_params:
            query_binding_rules.append(f"use bound params for {', '.join(query_params[:4])} filters")
        if re.search(r"\bwhere\b", sql_text, flags=re.IGNORECASE):
            query_binding_rules.append("avoid string concatenation in WHERE clause")
        if re.search(r"\blike\b", sql_text, flags=re.IGNORECASE):
            query_binding_rules.append("parameterize LIKE predicates instead of inline SQL composition")

        default_filters: list[str] = []
        if any(param.lower() == "status" for param in query_params):
            default_filters.append("preserve default status filter behavior from the legacy search form")
        if re.search(r"includeClosed|closed", text, flags=re.IGNORECASE):
            default_filters.append("exclude CLOSED unless includeClosed is explicitly enabled")

        result_shape_hints: list[str] = []
        columns = self._extract_select_columns(sql_text)
        if columns:
            result_shape_hints.append(f"list result with {', '.join(columns[:4])}")
        elif re.search(r"\b(table|grid|list|results?)\b", text, flags=re.IGNORECASE):
            result_shape_hints.append("list result shape is rendered as a table/grid in the legacy view")

        if supplemental:
            filter_fields = filter_fields[:2]
            sort_rules = sort_rules[:1]
            paging_rules = paging_rules[:1]
            query_binding_rules = query_binding_rules[:1]
            default_filters = default_filters[:1]
            result_shape_hints = result_shape_hints[:1]

        return SearchFilterRules(
            entities=entities,
            filter_fields=filter_fields,
            query_params=query_params,
            sort_rules=sort_rules,
            paging_rules=paging_rules,
            query_binding_rules=self._dedupe_list(query_binding_rules),
            default_filters=self._dedupe_list(default_filters),
            result_shape_hints=self._dedupe_list(result_shape_hints),
        )

    def extract_save_validation_rules(
        self,
        prepared: PreparedRebuildInput,
        supplemental: bool = False,
    ) -> SaveValidationRules:
        text = prepared.legacy_bundle
        entities = self._rule_entities(prepared)
        required_fields = self._extract_unique_matches(
            text,
            r"if\s*\(\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*==\s*null|([a-zA-Z_][a-zA-Z0-9_]*)\.isBlank\(\)|required\s+([a-zA-Z_][a-zA-Z0-9_]*)",
        )
        field_validation_rules: list[str] = []
        for field in required_fields:
            field_validation_rules.append(f"{field} must not be empty")
        if re.search(r"validate|validator", text, flags=re.IGNORECASE):
            field_validation_rules.append("legacy save flow includes validator-style checks before persistence")

        duplicate_check_rules: list[str] = []
        duplicate_fields = self._extract_unique_matches(text, r"existsBy([A-Z][a-zA-Z0-9_]*)|duplicate\s+([a-zA-Z_][a-zA-Z0-9_]*)")
        for field in duplicate_fields:
            normalized = re.sub(r"([a-z])([A-Z])", r"\1 \2", field).replace("_", " ").lower()
            duplicate_check_rules.append(f"prevent duplicate records by {normalized}")
        if not duplicate_check_rules and re.search(r"\b(duplicate|exists|already exists|unique|중복)\b", text, flags=re.IGNORECASE):
            duplicate_check_rules.append("prevent duplicate records before save")

        save_guard_rules: list[str] = []
        if re.search(r"\b(forbidden|cannot save|blocked|guard)\b", text, flags=re.IGNORECASE):
            save_guard_rules.append("apply pre-save guard before persistence")
        if re.search(r"\b(save|insert|update|submit|persist)\b", text, flags=re.IGNORECASE) and (
            required_fields or duplicate_check_rules or re.search(r"throw\s+new", text, flags=re.IGNORECASE)
        ):
            save_guard_rules.append("run validation and duplicate guards before persistence")
        if re.search(r"\b(role|admin|manager)\b", text, flags=re.IGNORECASE) and re.search(r"\b(save|insert|update|submit)\b", text, flags=re.IGNORECASE):
            save_guard_rules.append("enforce role-based save restrictions before save")
        if re.search(r"\b(status|state|pending|approved|closed|draft)\b", text, flags=re.IGNORECASE) and re.search(r"\b(save|update|submit)\b", text, flags=re.IGNORECASE):
            save_guard_rules.append("enforce status-change restrictions before save")

        exception_rules = self._extract_unique_matches(
            text,
            r"throw\s+new\s+([A-Za-z]+Exception)|\b(IllegalStateException|ValidationException|SecurityException|IllegalArgumentException)\b",
        )
        exception_rules = [f"raise {rule}" for rule in exception_rules]

        command_boundary_hints: list[str] = []
        if required_fields or duplicate_check_rules or save_guard_rules:
            command_boundary_hints.append("split validation from persistence")
            command_boundary_hints.append("use command DTO and validator")
        if exception_rules:
            command_boundary_hints.append("normalize save-time exceptions into explicit validation results")

        if supplemental:
            field_validation_rules = field_validation_rules[:2]
            duplicate_check_rules = duplicate_check_rules[:1]
            save_guard_rules = save_guard_rules[:1]
            exception_rules = exception_rules[:1]
            command_boundary_hints = command_boundary_hints[:1]

        return SaveValidationRules(
            entities=entities,
            required_fields=required_fields,
            field_validation_rules=self._dedupe_list(field_validation_rules),
            duplicate_check_rules=self._dedupe_list(duplicate_check_rules),
            save_guard_rules=self._dedupe_list(save_guard_rules),
            exception_rules=self._dedupe_list(exception_rules),
            command_boundary_hints=self._dedupe_list(command_boundary_hints),
        )

    def estimate_confidence(self, prepared: PreparedRebuildInput) -> float:
        score = 0.1
        score += min(0.18, len(prepared.assets.source_code) / 6000)
        score += min(0.16, len(prepared.assets.ui_template) / 5000)
        score += min(0.16, len(prepared.assets.sql_queries) / 2500)
        score += min(0.14, len(prepared.assets.database_schema) / 2500)
        score += min(0.06, len(prepared.assets.framework_info) / 1000)
        score += min(0.06, len(prepared.temp_context) / 4000)
        signal_groups = sum(
            1
            for group in (
                prepared.signals.status_permissions,
                prepared.signals.search_filters,
                prepared.signals.save_validation,
            )
            if group
        )
        score += signal_groups * 0.08
        score += min(0.06, len(prepared.signals.concepts) * 0.02)
        dominance_gap = self._dominance_gap(prepared.signals.scores)
        score += min(0.08, dominance_gap * 0.12)
        score -= min(0.22, 0.06 * len(prepared.missing_context))
        if prepared.scope_limited:
            score -= 0.05
        if prepared.missing_context:
            score = min(score, 0.94)
        return max(0.0, min(1.0, round(score, 2)))

    def build_result(self, prepared: PreparedRebuildInput) -> StructuredRebuildResult:
        confidence = self.estimate_confidence(prepared)
        extracted_rules = self.extract_rules(prepared)
        return StructuredRebuildResult(
            one_line_conclusion=self._build_conclusion(prepared, confidence),
            analysis_summary=self.analyze_assets(prepared),
            rebuild_strategy=self.infer_target_architecture(prepared),
            layer_reconstruction=self.build_layer_reconstruction(prepared),
            recomposition_draft=self.build_recomposition_draft(prepared),
            risks=self.build_risks(prepared),
            extracted_rules=extracted_rules,
            confidence=confidence,
            missing_context=list(prepared.missing_context),
        )

    def format_user_summary(
        self,
        result: StructuredRebuildResult,
        *,
        scope_limited: bool,
        needs_more_input: bool,
    ) -> str:
        lines = [
            "한 줄 결론",
            f"- {result.one_line_conclusion}",
            "",
            "핵심 요약",
            *self._render_bullets(result.analysis_summary),
            "",
            "재구성 전략",
            *self._render_bullets(result.rebuild_strategy),
            "",
            "레이어별 재구성",
            "DB",
            *self._render_bullets(result.layer_reconstruction.database),
            "API",
            *self._render_bullets(result.layer_reconstruction.backend),
            "UI",
            *self._render_bullets(result.layer_reconstruction.frontend),
            "",
            "초안",
            "DB",
            *self._render_bullets(result.recomposition_draft.database),
            "API",
            *self._render_bullets(result.recomposition_draft.backend),
            "UI",
            *self._render_bullets(result.recomposition_draft.frontend),
            "",
            "리스크",
            *self._render_bullets(result.risks),
        ]
        if result.missing_context:
            lines.extend(["", "추가 필요 정보", *self._render_bullets(result.missing_context)])
        lines.extend(
            [
                "",
                "실행 메타",
                f"- confidence: {result.confidence:.2f}",
                f"- scope_limited: {'true' if scope_limited else 'false'}",
                f"- needs_more_input: {'true' if needs_more_input else 'false'}",
            ]
        )
        return "\n".join(lines).strip()

    def _build_conclusion(self, prepared: PreparedRebuildInput, confidence: float) -> str:
        concept = self._primary_concept(prepared)
        primary = prepared.signals.primary_feature_mode
        if prepared.scope_limited:
            return f"요청 범위를 단일 {concept} 기능 재구성으로 축소해 React + REST API 중심 현대화 전략을 제안합니다."
        if primary == "status_permissions":
            return f"이 {concept} 기능은 역할/상태 기반 액션 규칙을 정책 레이어로 추출하는 방향의 재구성이 적합합니다."
        if primary == "search_filters":
            return f"이 {concept} 기능은 검색 필터 상태와 쿼리 조합을 API/쿼리 모델로 분리하는 재구성이 적합합니다."
        if primary == "save_validation":
            return f"이 {concept} 기능은 저장 검증과 중복 체크를 command/validator로 분리하는 재구성이 적합합니다."
        if confidence < 0.45:
            return "자산이 제한적이므로 단일 기능 기준의 보수적 재구성 초안을 제안합니다."
        return f"이 {concept} 기능은 React 프론트엔드와 REST API 백엔드로 단계적 재구성이 적합합니다."

    def _extract_concepts(self, prepared: PreparedRebuildInput) -> list[str]:
        text = " ".join(
            [
                prepared.goal,
                prepared.assets.source_code,
                prepared.assets.ui_template,
                prepared.assets.sql_queries,
                prepared.assets.database_schema,
                prepared.temp_context,
            ]
        ).lower()
        found: list[str] = []
        for token in self.CONCEPT_PATTERNS:
            if token.lower() in text and token not in found:
                found.append(token)
        return found[:6]

    def _extract_status_permission_signals(self, text: str) -> list[str]:
        checks = [
            ("role/status/action visibility", r"\b(role|permission|auth|authorize|grant|deny|admin|manager|approv|reject|cancel|status|state|visible|disabled)\b"),
            ("JSP conditional action branch", r"<c:if|<c:choose|if\s*\(|disabled=|readonly=|display:\s*none"),
            ("status transition rule", r"\b(approve|reject|cancel|close|reopen|submit|complete|draft|pending|active|inactive)\b"),
        ]
        return [label for label, pattern in checks if re.search(pattern, text, flags=re.IGNORECASE)]

    def _extract_search_filter_signals(self, text: str) -> list[str]:
        checks = [
            ("query parameters", r"request\.getparameter|@requestparam|param\.|querystring|searchword|keyword"),
            ("search filter state", r"\b(search|filter|sort|page|paging|datefrom|dateto|condition|criteria|statusfilter)\b"),
            ("SQL parameterization candidate", r"\b(where|order by|group by|like|join|limit|offset)\b"),
        ]
        return [label for label, pattern in checks if re.search(pattern, text, flags=re.IGNORECASE)]

    def _extract_save_validation_signals(self, text: str) -> list[str]:
        checks = [
            ("validation rules", r"\b(validate|validator|required|invalid|length|format|null check|isblank|pattern)\b"),
            ("save guards", r"\b(save|insert|update|merge|submit|persist|commit)\b"),
            ("duplicate checks", r"\b(duplicate|exists|already exists|unique|dup check|중복)\b"),
        ]
        return [label for label, pattern in checks if re.search(pattern, text, flags=re.IGNORECASE)]

    def _primary_concept(self, prepared: PreparedRebuildInput) -> str:
        return prepared.signals.concepts[0] if prepared.signals.concepts else "legacy"

    def _rule_entities(self, prepared: PreparedRebuildInput) -> list[str]:
        resource = self._singular_resource(self._resource_name(prepared))
        if resource:
            return [resource]
        concept = self._primary_concept(prepared)
        normalized = re.sub(r"[^a-z0-9]+", "_", concept.lower()).strip("_")
        return [normalized or "legacy_feature"]

    def _transition_condition_hint(self, text: str, roles: list[str], status: str, action: str) -> str:
        matched_roles = [role for role in roles if re.search(rf"{role}.*{action}|{action}.*{role}", text, flags=re.IGNORECASE)]
        if matched_roles:
            return " or ".join(role.lower() for role in matched_roles)
        if re.search(r"<c:if|<c:choose|if\s*\(", text, flags=re.IGNORECASE):
            return "legacy conditional branch"
        return f"status is {status}"

    def _infer_target_status(self, action: str, statuses: list[str]) -> str:
        mapping = {
            "approve": "APPROVED",
            "reject": "REJECTED",
            "resubmit": "PENDING",
            "submit": "SUBMITTED",
            "close": "CLOSED",
            "reopen": "PENDING",
            "cancel": "CANCELLED",
        }
        candidate = mapping.get(action.lower())
        if candidate and candidate in statuses:
            return candidate
        return candidate or "UNKNOWN"

    def _infer_filter_field_type(self, name: str) -> str:
        lowered = name.lower()
        if lowered in {"includeclosed", "enabled", "active", "visible"}:
            return "checkbox"
        if "date" in lowered:
            return "date"
        if lowered in {"page", "limit", "offset"}:
            return "number"
        return "text"

    def _extract_select_columns(self, sql_text: str) -> list[str]:
        if not sql_text:
            return []
        match = re.search(r"select\s+(.*?)\s+from\s", sql_text, flags=re.IGNORECASE | re.DOTALL)
        if not match:
            return []
        segment = match.group(1)
        if "*" in segment:
            return []
        columns: list[str] = []
        for raw in segment.split(","):
            token = raw.strip().split()[-1].split(".")[-1]
            token = token.strip("`\"")
            if token:
                columns.append(token.lower())
        return self._dedupe_list(columns)

    def _score_feature_modes(
        self,
        prepared: PreparedRebuildInput,
        *,
        status_permissions: list[str],
        search_filters: list[str],
        save_validation: list[str],
    ) -> dict[str, float]:
        bundle = prepared.legacy_bundle.lower()
        scores = {
            "status_permissions": 0.0,
            "search_filters": 0.0,
            "save_validation": float(len(save_validation)) * 1.35,
        }
        role_hits = self._match_count(bundle, r"\b(role|admin|manager|user|permission|auth|authorize)\b")
        action_hits = self._match_count(bundle, r"\b(approve|reject|resubmit|cancel)\b")
        visibility_hits = self._match_count(bundle, r"\b(button|visible|disabled|readonly|show[a-z]*button)\b")
        transition_hits = self._match_count(bundle, r"\b(status|state|pending|approved|rejected|draft|submitted|complete|closed)\b")
        conditional_render_hits = self._match_count(bundle, r"<c:if|<c:choose|if\s*\(")
        strong_status_bundle = sum(
            1
            for value in (
                action_hits >= 1,
                visibility_hits >= 1,
                transition_hits >= 2,
                role_hits >= 1 and conditional_render_hits >= 1,
            )
            if value
        )
        scores["status_permissions"] += float(len(status_permissions)) * 0.55
        scores["status_permissions"] += min(0.55, role_hits * 0.06 + transition_hits * 0.05)
        if strong_status_bundle >= 2:
            scores["status_permissions"] += 1.4 + min(
                2.8,
                action_hits * 0.75 + visibility_hits * 0.55 + transition_hits * 0.32 + conditional_render_hits * 0.35,
            )
            if role_hits >= 1 and action_hits >= 1 and visibility_hits >= 1 and transition_hits >= 2:
                scores["status_permissions"] += 1.7
            if action_hits >= 2 and conditional_render_hits >= 1 and transition_hits >= 2:
                scores["status_permissions"] += 0.9
        elif strong_status_bundle == 1:
            scores["status_permissions"] += min(
                0.9,
                action_hits * 0.28 + visibility_hits * 0.2 + transition_hits * 0.12 + conditional_render_hits * 0.15,
            )

        filter_param_hits = self._match_count(bundle, r"request\.getparameter|@requestparam|keyword|filter|sort|page|criteria|condition|searchword")
        filter_state_hits = self._match_count(bundle, r"\b(search|filter|sort|page|paging|datefrom|dateto|statusfilter|query state|form|results?|list)\b")
        dynamic_query_hits = self._match_count(bundle, r"\b(where|order by|group by|like|limit|offset|append|concat|dynamic sql)\b")
        search_form_hits = self._match_count(bundle, r"<form|searchform|filterbar|조회|검색")
        result_list_hits = self._match_count(bundle, r"\b(table|grid|list|results?)\b")
        strong_search_bundle = sum(
            1
            for value in (
                filter_param_hits >= 2,
                dynamic_query_hits >= 2,
                filter_state_hits >= 2,
                search_form_hits >= 1 and result_list_hits >= 1,
            )
            if value
        )
        if strong_search_bundle >= 3:
            scores["search_filters"] += 1.5 + min(
                3.0,
                filter_param_hits * 0.48 + dynamic_query_hits * 0.42 + filter_state_hits * 0.25 + result_list_hits * 0.2,
            )
            if search_form_hits >= 1 and filter_param_hits >= 2 and dynamic_query_hits >= 2:
                scores["search_filters"] += 1.0
            if result_list_hits >= 1 and filter_state_hits >= 2:
                scores["search_filters"] += 0.6
            if search_form_hits >= 1 and result_list_hits >= 1 and filter_param_hits >= 2:
                scores["search_filters"] += 0.75
        elif strong_search_bundle == 2:
            scores["search_filters"] += 0.9 + min(
                1.9,
                filter_param_hits * 0.32 + dynamic_query_hits * 0.28 + filter_state_hits * 0.18,
            )
            if search_form_hits >= 1 and result_list_hits >= 1:
                scores["search_filters"] += 0.45
        else:
            scores["search_filters"] += min(0.8, filter_param_hits * 0.12 + dynamic_query_hits * 0.1 + filter_state_hits * 0.08)

        required_hits = self._match_count(bundle, r"\b(required|not null|isblank|mandatory|empty|validate|validator|invalid)\b")
        duplicate_hits = self._match_count(bundle, r"\b(duplicate|exists|already exists|unique|dup check|중복)\b")
        save_hits = self._match_count(bundle, r"\b(save|insert|update|merge|submit|persist|commit)\b")
        guard_hits = self._match_count(bundle, r"\b(before save|pre-save|guard|cannot save|blocked|forbidden|exception|illegalstate|validationexception|throw\s+new)\b")
        role_save_hits = self._match_count(bundle, r"\b(role|admin|manager)\b") if save_hits > 0 else 0
        status_save_hits = self._match_count(bundle, r"\b(status|state|pending|approved|closed|draft)\b") if save_hits > 0 else 0
        strong_save_bundle = sum(
            1
            for value in (
                required_hits >= 1,
                duplicate_hits >= 1,
                guard_hits >= 1,
                role_save_hits >= 1 or status_save_hits >= 2,
            )
            if value
        )
        scores["save_validation"] += min(
            4.2,
            required_hits * 0.8 + duplicate_hits * 1.05 + save_hits * 0.35 + guard_hits * 1.0 + role_save_hits * 0.35 + status_save_hits * 0.2,
        )
        if strong_save_bundle >= 2:
            scores["save_validation"] += 1.0
        if re.search(r"throw\s+new|validator|validate\s*\(", bundle, flags=re.IGNORECASE):
            scores["save_validation"] += 0.7
        if prepared.assets.sql_queries:
            if strong_search_bundle >= 2:
                scores["search_filters"] += 0.6
            scores["save_validation"] += 0.5 if re.search(r"\b(insert|update|merge)\b", prepared.assets.sql_queries, flags=re.IGNORECASE) else 0.0
        if prepared.assets.database_schema:
            scores["status_permissions"] += 0.2 if "status" in prepared.assets.database_schema.lower() else 0.0
        if scores["status_permissions"] >= 3.4:
            scores["status_permissions"] += 0.45
        if scores["save_validation"] >= 3.0:
            scores["save_validation"] += 0.7
        if scores["search_filters"] >= 3.2:
            scores["search_filters"] += 0.2
        return scores

    def _pick_feature_modes(self, scores: dict[str, float]) -> tuple[str, str | None]:
        adjusted_scores = dict(scores)
        adjusted_scores["status_permissions"] += 0.35 if scores.get("status_permissions", 0.0) >= 4.4 else 0.0
        adjusted_scores["save_validation"] += 0.3 if scores.get("save_validation", 0.0) >= 2.5 else 0.0
        adjusted_scores["search_filters"] -= 0.25 if scores.get("search_filters", 0.0) < 3.4 else 0.0
        adjusted_scores["search_filters"] += 0.85 if scores.get("search_filters", 0.0) >= 4.0 else 0.0
        if scores.get("status_permissions", 0.0) >= 5.0 and scores.get("search_filters", 0.0) >= 4.0:
            adjusted_scores["status_permissions"] += 0.6
        ordered = sorted(adjusted_scores.items(), key=lambda item: item[1], reverse=True)
        if not ordered or ordered[0][1] <= 0:
            return "general", None
        primary = ordered[0][0]
        secondary = None
        if len(ordered) > 1 and ordered[1][1] >= max(1.0, ordered[0][1] * 0.55):
            secondary = ordered[1][0]
        return primary, secondary

    def _dominance_gap(self, scores: dict[str, float]) -> float:
        ordered = sorted(scores.values(), reverse=True)
        if not ordered:
            return 0.0
        if len(ordered) == 1:
            return ordered[0]
        return max(0.0, ordered[0] - ordered[1])

    def _resource_name(self, prepared: PreparedRebuildInput) -> str:
        concept = self._primary_concept(prepared)
        slug = re.sub(r"[^a-z0-9]+", "_", concept.lower()).strip("_")
        mapping = {
            "주문": "orders",
            "결재": "approvals",
            "요청": "requests",
            "사용자": "users",
            "회원": "members",
            "상품": "products",
            "고객": "customers",
            "문서": "documents",
            "계약": "contracts",
            "환불": "refunds",
            "예약": "reservations",
            "보고서": "reports",
            "권한": "policies",
            "상태": "statuses",
        }
        return mapping.get(concept, slug or "legacy_feature")

    def _page_name(self, prepared: PreparedRebuildInput) -> str:
        resource = self._resource_name(prepared)
        parts = [part.capitalize() for part in resource.split("_") if part]
        base = "".join(parts) or "LegacyFeature"
        if prepared.signals.primary_feature_mode == "search_filters":
            if base.endswith("s") and len(base) > 1:
                base = base[:-1]
            return base + "SearchPage"
        if base.endswith("s") and len(base) > 1:
            return base[:-1] + "Page"
        return base + "Page"

    def _filter_bar_name(self, prepared: PreparedRebuildInput) -> str:
        page = self._page_name(prepared)
        return page.replace("Page", "FilterBar")

    def _results_table_name(self, prepared: PreparedRebuildInput) -> str:
        page = self._page_name(prepared)
        return page.replace("Page", "ResultsTable")

    def _query_dto_name(self, prepared: PreparedRebuildInput) -> str:
        page = self._page_name(prepared)
        return page + "QueryDTO"

    def _query_mapper_name(self, prepared: PreparedRebuildInput) -> str:
        return f"{self._resource_name(prepared)}_query_mapper"

    def _singular_resource(self, resource: str) -> str:
        if resource.endswith("ies"):
            return resource[:-3] + "y"
        if resource.endswith("s") and len(resource) > 1:
            return resource[:-1]
        return resource

    def _looks_like_jsp(self, prepared: PreparedRebuildInput) -> bool:
        text = "\n".join([prepared.assets.source_code, prepared.assets.ui_template, prepared.temp_context]).lower()
        return "<%" in text or "<jsp:" in text or "c:foreach" in text or "c:if" in text

    def _contains_sql_in_ui(self, prepared: PreparedRebuildInput) -> bool:
        text = "\n".join([prepared.assets.source_code, prepared.assets.ui_template]).lower()
        return bool(re.search(r"\b(select|insert|update|delete)\b", text))

    def _has_join_heaviness(self, sql_text: str) -> bool:
        lowered = (sql_text or "").lower()
        return lowered.count(" join ") >= 2 or lowered.count("case when") >= 2

    def _section(self, title: str, value: str) -> str:
        if not (value or "").strip():
            return ""
        return f"[{title}]\n{value.strip()}"

    def _render_bullets(self, items: list[str]) -> list[str]:
        return [f"- {item}" for item in items] if items else ["- 정보가 충분하지 않습니다."]

    def _match_count(self, text: str, pattern: str) -> int:
        return len(re.findall(pattern, text, flags=re.IGNORECASE))

    def _extract_unique_matches(self, text: str, pattern: str) -> list[str]:
        matches = re.findall(pattern, text, flags=re.IGNORECASE)
        results: list[str] = []
        for match in matches:
            if isinstance(match, tuple):
                for item in match:
                    cleaned = (item or "").strip()
                    if cleaned:
                        results.append(cleaned)
                        break
            else:
                cleaned = (match or "").strip()
                if cleaned:
                    results.append(cleaned)
        return self._dedupe_list(results)

    def _dedupe_list(self, items: list[str]) -> list[str]:
        seen: set[str] = set()
        output: list[str] = []
        for item in items:
            key = item.lower()
            if key in seen:
                continue
            seen.add(key)
            output.append(item)
        return output

    def _dedupe_dicts(self, items: list[dict]) -> list[dict]:
        seen: set[str] = set()
        output: list[dict] = []
        for item in items:
            key = repr(sorted(item.items()))
            if key in seen:
                continue
            seen.add(key)
            output.append(item)
        return output
