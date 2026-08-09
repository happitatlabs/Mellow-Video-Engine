from __future__ import annotations

import re
from typing import Any

from mellow_link.infra.run_events import DEV_ONLY_EVENT_TYPES, EVENT_TYPE_DEBUG_ANONYMIZATION_REPORT

from .schemas import (
    AnonymizationBlockCount,
    AnonymizationBlockPreview,
    AnonymizationDebugEventPayload,
    AnonymizationDebugReportSummary,
    AnonymizationSummary,
    AnonymizationV0Output,
    AnonymizationValidationFinding,
    AnonymizationValidationResult,
    InputAssemblerV0Output,
)

DEBUG_ANONYMIZATION_EVENT_TYPE = EVENT_TYPE_DEBUG_ANONYMIZATION_REPORT
PREVIEW_TEXT_MAX_LENGTH = 160


def build_anonymization_summary(anonymization_output: AnonymizationV0Output) -> AnonymizationSummary:
    report = anonymization_output.anonymization_report
    return AnonymizationSummary(
        applied=report.redaction_summary.total_replacements > 0,
        policy_version=report.policy_version,
        total_replacements=report.redaction_summary.total_replacements,
        request_context_redacted=report.request_context_redacted,
        block_counts=[
            AnonymizationBlockCount(
                block_id=block_report.block_id,
                replacement_count=block_report.replacement_count,
            )
            for block_report in report.block_reports
        ],
        risk_flags=list(report.structure_risk_flags),
    )


def build_anonymization_debug_payload(
    *,
    original_input: InputAssemblerV0Output,
    anonymization_output: AnonymizationV0Output,
) -> AnonymizationDebugEventPayload:
    summary = build_anonymization_summary(anonymization_output)
    validation = validate_anonymization_exposure(
        original_input=original_input,
        anonymization_output=anonymization_output,
        user_summary=summary,
        debug_event_type=DEBUG_ANONYMIZATION_EVENT_TYPE,
    )
    replacement_count_by_block = {
        block_report.block_id: block_report.replacement_count
        for block_report in anonymization_output.anonymization_report.block_reports
    }
    block_previews = [
        AnonymizationBlockPreview(
            block_id=block.block_id,
            kind=block.kind,
            replacement_count=replacement_count_by_block.get(block.block_id, 0),
            preview_text=build_preview_text(block.text),
        )
        for block in anonymization_output.sanitized_input.source_blocks
    ]
    return AnonymizationDebugEventPayload(
        policy_version=anonymization_output.anonymization_report.policy_version,
        validation=validation,
        report_summary=AnonymizationDebugReportSummary(
            applied=summary.applied,
            total_replacements=summary.total_replacements,
            request_context_redacted=summary.request_context_redacted,
            risk_flags=list(summary.risk_flags),
        ),
        block_previews=block_previews,
        anonymization_report=anonymization_output.anonymization_report,
    )


def validate_anonymization_exposure(
    *,
    original_input: InputAssemblerV0Output,
    anonymization_output: AnonymizationV0Output,
    user_summary: AnonymizationSummary,
    debug_event_type: str,
) -> AnonymizationValidationResult:
    findings: list[AnonymizationValidationFinding] = []
    sanitized_input = anonymization_output.sanitized_input

    shape_preserved = _is_shape_preserved(original_input, sanitized_input)
    if not shape_preserved:
        findings.append(
            AnonymizationValidationFinding(
                code="shape_not_preserved",
                message="source_blocks 또는 asset_inventory의 불변 shape가 유지되지 않았습니다.",
            )
        )

    asset_links_preserved = _are_asset_links_preserved(original_input, sanitized_input)
    if not asset_links_preserved:
        findings.append(
            AnonymizationValidationFinding(
                code="asset_links_changed",
                message="asset_id, asset_ids 또는 mapped_block_ids 연결이 변경되었습니다.",
            )
        )

    summary_dict = user_summary.model_dump()
    user_surface_safe = True
    if _contains_key(summary_dict, "anonymization_report"):
        user_surface_safe = False
        findings.append(
            AnonymizationValidationFinding(
                code="user_payload_contains_full_report",
                message="사용자 payload에 anonymization_report가 포함되어 있습니다.",
            )
        )
    if _contains_key(summary_dict, "sanitized_input"):
        user_surface_safe = False
        findings.append(
            AnonymizationValidationFinding(
                code="user_payload_contains_sanitized_input",
                message="사용자 payload에 sanitized_input이 포함되어 있습니다.",
            )
        )

    if debug_event_type not in DEV_ONLY_EVENT_TYPES:
        findings.append(
            AnonymizationValidationFinding(
                code="dev_event_visible_in_user_stream",
                message="debug_anonymization_report가 dev-only event 집합에 등록되어 있지 않습니다.",
            )
        )

    passed = shape_preserved and asset_links_preserved and user_surface_safe and debug_event_type in DEV_ONLY_EVENT_TYPES
    return AnonymizationValidationResult(
        passed=passed,
        shape_preserved=shape_preserved,
        user_surface_safe=user_surface_safe,
        findings=findings,
    )


def build_preview_text(text: str) -> str:
    """
    preview_text는 반드시 sanitized_input.source_blocks[].text에서 생성한다.
    서버에서 줄바꿈/공백을 정규화한 뒤 앞 160자만 사용하고, HTML escape는 렌더러에서만 수행한다.
    """
    normalized = (text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized[:PREVIEW_TEXT_MAX_LENGTH]


def _is_shape_preserved(original_input: InputAssemblerV0Output, sanitized_input: InputAssemblerV0Output) -> bool:
    if len(original_input.source_blocks) != len(sanitized_input.source_blocks):
        return False
    if len(original_input.asset_inventory) != len(sanitized_input.asset_inventory):
        return False
    for original_block, sanitized_block in zip(original_input.source_blocks, sanitized_input.source_blocks):
        if original_block.block_id != sanitized_block.block_id:
            return False
        if original_block.kind != sanitized_block.kind:
            return False
        if list(original_block.asset_ids) != list(sanitized_block.asset_ids):
            return False
    for original_asset, sanitized_asset in zip(original_input.asset_inventory, sanitized_input.asset_inventory):
        if original_asset.asset_id != sanitized_asset.asset_id:
            return False
        if original_asset.origin != sanitized_asset.origin:
            return False
        if original_asset.declared_kind != sanitized_asset.declared_kind:
            return False
        if list(original_asset.mapped_block_ids) != list(sanitized_asset.mapped_block_ids):
            return False
    return True


def _are_asset_links_preserved(original_input: InputAssemblerV0Output, sanitized_input: InputAssemblerV0Output) -> bool:
    original_asset_links = [
        (asset.asset_id, tuple(asset.mapped_block_ids))
        for asset in original_input.asset_inventory
    ]
    sanitized_asset_links = [
        (asset.asset_id, tuple(asset.mapped_block_ids))
        for asset in sanitized_input.asset_inventory
    ]
    if original_asset_links != sanitized_asset_links:
        return False
    original_block_links = [
        (block.block_id, tuple(block.asset_ids))
        for block in original_input.source_blocks
    ]
    sanitized_block_links = [
        (block.block_id, tuple(block.asset_ids))
        for block in sanitized_input.source_blocks
    ]
    return original_block_links == sanitized_block_links


def _contains_key(obj: Any, target_key: str) -> bool:
    if isinstance(obj, dict):
        for key, value in obj.items():
            if key == target_key:
                return True
            if _contains_key(value, target_key):
                return True
        return False
    if isinstance(obj, list):
        return any(_contains_key(item, target_key) for item in obj)
    return False
