from __future__ import annotations

from collections import defaultdict

from .schemas import (
    AssetInventoryItem,
    InputAssemblerRequestContext,
    InputAssemblerV0Input,
    InputAssemblerV0Output,
    MissingContextItem,
    SourceBlock,
    UnknownItem,
)

_BLOCK_KIND_BY_ASSET_KIND = {
    "source_code": "source_code",
    "database_schema": "database_schema",
    "sql_queries": "sql_queries",
    "ui_template": "ui_template",
    "framework_info": "framework_info",
    "unknown": "unclassified_text",
}

_BLOCK_ORDER = (
    "source_code",
    "database_schema",
    "sql_queries",
    "ui_template",
    "framework_info",
    "unclassified_text",
)

_BLOCK_ID_BY_KIND = {kind: f"block:{kind}" for kind in _BLOCK_ORDER}


class InputAssemblerV0:
    def assemble(self, payload: InputAssemblerV0Input) -> InputAssemblerV0Output:
        request_context = InputAssemblerRequestContext(
            goal=self._normalize_goal(payload.request_context.goal),
            constraints=self._normalize_constraints(payload.request_context.constraints),
        )

        grouped_texts: dict[str, list[str]] = defaultdict(list)
        grouped_asset_ids: dict[str, list[str]] = defaultdict(list)
        asset_inventory: list[AssetInventoryItem] = []
        unknowns: list[UnknownItem] = []

        for asset in payload.input_assets:
            normalized_text = self._normalize_text(asset.text)
            block_kind = _BLOCK_KIND_BY_ASSET_KIND.get(asset.declared_kind, "unclassified_text")
            mapped_block_ids = [_BLOCK_ID_BY_KIND[block_kind]] if normalized_text else []

            asset_inventory.append(
                AssetInventoryItem(
                    asset_id=asset.asset_id,
                    origin=asset.origin,
                    declared_kind=asset.declared_kind,
                    filename=asset.filename,
                    media_type=asset.media_type,
                    source_ref=asset.source_ref,
                    char_count=len(normalized_text),
                    mapped_block_ids=mapped_block_ids,
                )
            )

            if asset.declared_kind == "unknown":
                unknowns.append(
                    UnknownItem(
                        code="unknown_asset_kind",
                        asset_id=asset.asset_id,
                        message="declared_kind가 unknown으로 전달되었습니다.",
                    )
                )

            if not normalized_text:
                unknowns.append(
                    UnknownItem(
                        code="empty_asset_text",
                        asset_id=asset.asset_id,
                        message="text가 비어 있어 source block으로 승격되지 않았습니다.",
                    )
                )
                continue

            if self._has_partial_metadata(asset):
                unknowns.append(
                    UnknownItem(
                        code="partial_asset_metadata",
                        asset_id=asset.asset_id,
                        message="filename/media_type/source_ref 메타데이터가 부분적으로만 제공되었습니다.",
                    )
                )

            grouped_texts[block_kind].append(normalized_text)
            grouped_asset_ids[block_kind].append(asset.asset_id)

        source_blocks = [
            SourceBlock(
                block_id=_BLOCK_ID_BY_KIND[kind],
                kind=kind,
                text="\n\n".join(grouped_texts[kind]),
                asset_ids=grouped_asset_ids[kind],
            )
            for kind in _BLOCK_ORDER
            if grouped_texts.get(kind)
        ]

        missing_context = self._build_missing_context(request_context, grouped_texts)

        return InputAssemblerV0Output(
            request_context=request_context,
            asset_inventory=asset_inventory,
            source_blocks=source_blocks,
            missing_context=missing_context,
            unknowns=unknowns,
        )

    def _normalize_goal(self, goal: str | None) -> str | None:
        normalized = self._normalize_text(goal)
        return normalized or None

    def _normalize_constraints(self, constraints: list[str] | None) -> list[str]:
        if not constraints:
            return []
        normalized: list[str] = []
        seen: set[str] = set()
        for item in constraints:
            cleaned = self._normalize_text(item)
            if not cleaned or cleaned in seen:
                continue
            seen.add(cleaned)
            normalized.append(cleaned)
        return normalized

    def _normalize_text(self, text: str | None) -> str:
        if text is None:
            return ""
        normalized = str(text).replace("\r\n", "\n").replace("\r", "\n").strip()
        return normalized

    def _has_partial_metadata(self, asset) -> bool:
        metadata_values = [asset.filename, asset.media_type, asset.source_ref]
        present_count = sum(1 for value in metadata_values if value not in (None, ""))
        return 0 < present_count < len(metadata_values)

    def _build_missing_context(
        self,
        request_context: InputAssemblerRequestContext,
        grouped_texts: dict[str, list[str]],
    ) -> list[MissingContextItem]:
        items: list[MissingContextItem] = []

        if not request_context.goal:
            items.append(
                MissingContextItem(
                    code="goal_missing",
                    message="구조 분석 대상과 목적을 설명하는 goal이 없습니다.",
                )
            )

        if not (
            grouped_texts.get("source_code")
            or grouped_texts.get("ui_template")
            or grouped_texts.get("unclassified_text")
        ):
            items.append(
                MissingContextItem(
                    code="structure_inputs_missing",
                    message="레거시 화면 또는 서버 코드가 부족합니다.",
                )
            )

        if not (grouped_texts.get("database_schema") or grouped_texts.get("sql_queries")):
            items.append(
                MissingContextItem(
                    code="database_context_missing",
                    message="DB 스키마 또는 SQL 쿼리 정보가 부족합니다.",
                )
            )

        if not grouped_texts.get("framework_info"):
            items.append(
                MissingContextItem(
                    code="runtime_context_missing",
                    message="기존 프레임워크/런타임 정보가 부족합니다.",
                )
            )

        return items
