from __future__ import annotations

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
