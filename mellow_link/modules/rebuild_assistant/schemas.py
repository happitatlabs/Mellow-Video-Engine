from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class RebuildAssetsPayload(BaseModel):
    source_code: str = ""
    database_schema: str = ""
    sql_queries: str = ""
    ui_template: str = ""
    framework_info: str = ""

    def has_any_content(self) -> bool:
        return any(
            bool((value or "").strip())
            for value in (
                self.source_code,
                self.database_schema,
                self.sql_queries,
                self.ui_template,
                self.framework_info,
            )
        )


AssetKind = Literal[
    "source_code",
    "database_schema",
    "sql_queries",
    "ui_template",
    "framework_info",
    "unknown",
]

AssetOrigin = Literal[
    "typed_form",
    "temp_upload",
    "api_inline",
    "unknown",
]

SourceBlockKind = Literal[
    "source_code",
    "database_schema",
    "sql_queries",
    "ui_template",
    "framework_info",
    "unclassified_text",
]

MissingContextCode = Literal[
    "goal_missing",
    "structure_inputs_missing",
    "database_context_missing",
    "runtime_context_missing",
]

UnknownCode = Literal[
    "unknown_asset_kind",
    "empty_asset_text",
    "partial_asset_metadata",
]

StructureRiskFlag = Literal[
    "identifier_like_tokens_redacted",
    "sql_identifiers_pseudonymized",
    "high_redaction_density",
]

AnonymizationValidationCode = Literal[
    "shape_not_preserved",
    "asset_links_changed",
    "user_payload_contains_full_report",
    "user_payload_contains_sanitized_input",
    "dev_event_visible_in_user_stream",
]


class InputAssemblerRequestContext(BaseModel):
    goal: str | None = None
    constraints: list[str] = Field(default_factory=list)


class InputAsset(BaseModel):
    asset_id: str
    origin: AssetOrigin = "unknown"
    declared_kind: AssetKind = "unknown"
    text: str = ""
    filename: str | None = None
    media_type: str | None = None
    source_ref: str | None = None


class InputAssemblerV0Input(BaseModel):
    request_context: InputAssemblerRequestContext = Field(default_factory=InputAssemblerRequestContext)
    input_assets: list[InputAsset] = Field(default_factory=list)


class AssetInventoryItem(BaseModel):
    asset_id: str
    origin: AssetOrigin
    declared_kind: AssetKind
    filename: str | None = None
    media_type: str | None = None
    source_ref: str | None = None
    char_count: int = 0
    mapped_block_ids: list[str] = Field(default_factory=list)


class SourceBlock(BaseModel):
    block_id: str
    kind: SourceBlockKind
    text: str = ""
    asset_ids: list[str] = Field(default_factory=list)


class MissingContextItem(BaseModel):
    code: MissingContextCode
    message: str


class UnknownItem(BaseModel):
    code: UnknownCode
    asset_id: str | None = None
    message: str


class InputAssemblerV0Output(BaseModel):
    assembler_version: Literal["input-assembler-v0"] = "input-assembler-v0"
    request_context: InputAssemblerRequestContext = Field(default_factory=InputAssemblerRequestContext)
    asset_inventory: list[AssetInventoryItem] = Field(default_factory=list)
    source_blocks: list[SourceBlock] = Field(default_factory=list)
    missing_context: list[MissingContextItem] = Field(default_factory=list)
    unknowns: list[UnknownItem] = Field(default_factory=list)


class AnonymizationBlockReport(BaseModel):
    block_id: str
    replacement_count: int = 0
    applied_rules: list[str] = Field(default_factory=list)


class AnonymizationAssetReport(BaseModel):
    asset_id: str
    filename_redacted: bool = False
    source_ref_redacted: bool = False


class AnonymizationRedactionSummary(BaseModel):
    total_replacements: int = 0


class AnonymizationReport(BaseModel):
    policy_version: Literal["anonymization-v0-conservative"] = "anonymization-v0-conservative"
    request_context_redacted: bool = False
    block_reports: list[AnonymizationBlockReport] = Field(default_factory=list)
    asset_reports: list[AnonymizationAssetReport] = Field(default_factory=list)
    redaction_summary: AnonymizationRedactionSummary = Field(default_factory=AnonymizationRedactionSummary)
    structure_risk_flags: list[StructureRiskFlag] = Field(default_factory=list)


class AnonymizationV0Output(BaseModel):
    sanitized_input: InputAssemblerV0Output
    anonymization_report: AnonymizationReport = Field(default_factory=AnonymizationReport)


class AnonymizationBlockCount(BaseModel):
    block_id: str
    replacement_count: int = 0


class AnonymizationSummary(BaseModel):
    applied: bool = False
    policy_version: Literal["anonymization-v0-conservative"] = "anonymization-v0-conservative"
    total_replacements: int = 0
    request_context_redacted: bool = False
    block_counts: list[AnonymizationBlockCount] = Field(default_factory=list)
    risk_flags: list[StructureRiskFlag] = Field(default_factory=list)


class AnonymizationValidationFinding(BaseModel):
    code: AnonymizationValidationCode
    message: str


class AnonymizationValidationResult(BaseModel):
    passed: bool = True
    shape_preserved: bool = True
    user_surface_safe: bool = True
    findings: list[AnonymizationValidationFinding] = Field(default_factory=list)


class AnonymizationDebugReportSummary(BaseModel):
    applied: bool = False
    total_replacements: int = 0
    request_context_redacted: bool = False
    risk_flags: list[StructureRiskFlag] = Field(default_factory=list)


class AnonymizationBlockPreview(BaseModel):
    block_id: str
    kind: SourceBlockKind
    replacement_count: int = 0
    preview_text: str = ""


class AnonymizationDebugEventPayload(BaseModel):
    policy_version: Literal["anonymization-v0-conservative"] = "anonymization-v0-conservative"
    validation: AnonymizationValidationResult = Field(default_factory=AnonymizationValidationResult)
    report_summary: AnonymizationDebugReportSummary = Field(default_factory=AnonymizationDebugReportSummary)
    block_previews: list[AnonymizationBlockPreview] = Field(default_factory=list)
    anonymization_report: AnonymizationReport = Field(default_factory=AnonymizationReport)


class LayeredListResult(BaseModel):
    database: list[str] = Field(default_factory=list)
    backend: list[str] = Field(default_factory=list)
    frontend: list[str] = Field(default_factory=list)


class StatusPermissionsRules(BaseModel):
    entities: list[str] = Field(default_factory=list)
    roles: list[str] = Field(default_factory=list)
    statuses: list[str] = Field(default_factory=list)
    actions: list[str] = Field(default_factory=list)
    role_action_matrix: list[dict] = Field(default_factory=list)
    status_action_matrix: list[dict] = Field(default_factory=list)
    transition_rules: list[dict] = Field(default_factory=list)
    ui_visibility_rules: list[str] = Field(default_factory=list)
    policy_hints: list[str] = Field(default_factory=list)


class SearchFilterRules(BaseModel):
    entities: list[str] = Field(default_factory=list)
    filter_fields: list[dict] = Field(default_factory=list)
    query_params: list[str] = Field(default_factory=list)
    sort_rules: list[dict] = Field(default_factory=list)
    paging_rules: list[dict] = Field(default_factory=list)
    query_binding_rules: list[str] = Field(default_factory=list)
    default_filters: list[str] = Field(default_factory=list)
    result_shape_hints: list[str] = Field(default_factory=list)


class SaveValidationRules(BaseModel):
    entities: list[str] = Field(default_factory=list)
    required_fields: list[str] = Field(default_factory=list)
    field_validation_rules: list[str] = Field(default_factory=list)
    duplicate_check_rules: list[str] = Field(default_factory=list)
    save_guard_rules: list[str] = Field(default_factory=list)
    exception_rules: list[str] = Field(default_factory=list)
    command_boundary_hints: list[str] = Field(default_factory=list)


class ExtractedRulesEnvelope(BaseModel):
    status_permissions: StatusPermissionsRules = Field(default_factory=StatusPermissionsRules)
    search_filters: SearchFilterRules = Field(default_factory=SearchFilterRules)
    save_validation: SaveValidationRules = Field(default_factory=SaveValidationRules)


class CompanyRuleProfile(BaseModel):
    profile_name: str = "default_placeholder"
    enabled: bool = False
    rule_sources: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class StructuredRebuildResult(BaseModel):
    one_line_conclusion: str = ""
    analysis_summary: list[str] = Field(default_factory=list)
    rebuild_strategy: list[str] = Field(default_factory=list)
    layer_reconstruction: LayeredListResult = Field(default_factory=LayeredListResult)
    recomposition_draft: LayeredListResult = Field(default_factory=LayeredListResult)
    risks: list[str] = Field(default_factory=list)
    extracted_rules: ExtractedRulesEnvelope = Field(default_factory=ExtractedRulesEnvelope)
    confidence: float = 0.0
    missing_context: list[str] = Field(default_factory=list)

    @field_validator("confidence", mode="before")
    @classmethod
    def clamp_confidence(cls, value: float) -> float:
        try:
            numeric = float(value)
        except Exception:
            numeric = 0.0
        return max(0.0, min(1.0, numeric))


class RebuildAssistantStartRequest(BaseModel):
    goal: str = Field(..., description="Single feature/page legacy reconstruction goal")
    assets: RebuildAssetsPayload = Field(default_factory=RebuildAssetsPayload)
    constraints: list[str] = Field(default_factory=list)
    temp_session_id: str | None = None

    @field_validator("goal")
    @classmethod
    def validate_goal(cls, value: str) -> str:
        stripped = (value or "").strip()
        if len(stripped) < 8:
            raise ValueError("goal must be at least 8 characters after trimming")
        return stripped

    @model_validator(mode="after")
    def validate_assets_or_temp_context(self) -> "RebuildAssistantStartRequest":
        if self.assets.has_any_content() or (self.temp_session_id or "").strip():
            return self
        raise ValueError("Provide at least one asset or temp_session_id")


class RebuildAssistantStartResponse(BaseModel):
    run_id: str
    session_id: str
    module_id: str = "rebuild_assistant"
    run_kind: str = "rebuild_plan"
