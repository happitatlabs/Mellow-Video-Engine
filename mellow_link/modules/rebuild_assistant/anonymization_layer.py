from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field

from .schemas import (
    AnonymizationAssetReport,
    AnonymizationBlockReport,
    AnonymizationRedactionSummary,
    AnonymizationReport,
    AnonymizationV0Output,
    InputAssemblerV0Output,
)

_TOKEN_NAMESPACES = (
    "SECRET",
    "EMAIL",
    "PHONE",
    "HOST",
    "PATH",
    "ACCOUNT_ID",
    "PERSON",
    "ORG",
    "TABLE",
    "COL",
    "CLASS",
    "FUNC",
    "API_PATH",
    "FILE",
    "SRCREF",
)

_TOKENIZED_VALUE_PATTERN = re.compile(rf"^(?:{'|'.join(_TOKEN_NAMESPACES)})_\d{{3}}$")
_WORD_LIKE_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|[가-힣]+|\d+")
_SQL_TYPE_PATTERN = (
    r"bigint|int|integer|smallint|tinyint|varchar|nvarchar|char|text|date|datetime|"
    r"timestamp|numeric|decimal|float|double|boolean|bool"
)
_SQL_KEYWORDS = {
    "select", "from", "where", "join", "left", "right", "inner", "outer", "full", "on",
    "and", "or", "not", "update", "set", "insert", "into", "delete", "create", "table",
    "order", "group", "by", "having", "limit", "offset", "values", "case", "when", "then",
    "else", "end", "distinct", "as", "is", "null", "like", "in", "exists", "desc", "asc",
}


@dataclass
class _ReplacementContext:
    registry: dict[tuple[str, str], str] = field(default_factory=dict)
    counters: dict[str, int] = field(default_factory=lambda: defaultdict(int))

    def token_for(self, namespace: str, original: str) -> str:
        key = (namespace, original)
        if key not in self.registry:
            self.counters[namespace] += 1
            self.registry[key] = f"{namespace}_{self.counters[namespace]:03d}"
        return self.registry[key]


@dataclass
class _SanitizeOutcome:
    text: str
    replacement_count: int = 0
    applied_rules: list[str] = field(default_factory=list)
    namespaces: set[str] = field(default_factory=set)


class AnonymizationLayerV0:
    """Structure-preserving anonymization layer.

    v0에서는 고신뢰 구조 식별자에 대해서만 pseudonymization을 적용하고,
    일반 변수명 및 문맥상 모호한 식별자는 유지한다.
    """

    POLICY_VERSION = "anonymization-v0-conservative"

    _AUTH_HEADER_PATTERN = re.compile(r"(?i)(authorization\s*:\s*bearer\s+)([A-Za-z0-9_\-\.]{20,})")
    _ENV_SECRET_PATTERN = re.compile(
        r"(?i)((?:OPENAI_API_KEY|ANTHROPIC_API_KEY|GOOGLE_API_KEY|GUARDIAN_[A-Z_]*KEY)\s*[:=]\s*[\"']?)([^\s\"'\n]{10,})([\"']?)"
    )
    _GENERIC_SECRET_PATTERN = re.compile(
        r"(?i)((?:api[_-]?key|apikey|password|passwd|pwd|secret|token)\s*[:=]\s*[\"']?)([^\s\"'<>]{8,})([\"']?)"
    )
    _BARE_SECRET_PATTERN = re.compile(r"\b(sk-[A-Za-z0-9_\-]{20,}|sk-ant-[A-Za-z0-9\-_]{20,}|AIza[0-9A-Za-z\-_]{20,})\b")
    _EMAIL_PATTERN = re.compile(r"(?<![\w.-])([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})(?![\w.-])")
    _PHONE_PATTERN = re.compile(r"(?<!\w)(\+?\d(?:[\d\-\s]{7,}\d))(?!\w)")
    _URL_HOST_PATTERN = re.compile(r"(?i)\b(https?://)([A-Za-z0-9.-]+)(:\d+)?")
    _IP_PATTERN = re.compile(r"\b((?:\d{1,3}\.){3}\d{1,3})\b")
    _WINDOWS_PATH_PATTERN = re.compile(r"(?i)([A-Z]:\\(?:[^\\/:*?\"<>|\r\n]+\\)+[^\\/:*?\"<>|\r\n]+\.[A-Za-z0-9]+)")
    _UNIX_PATH_PATTERN = re.compile(r"(?<![:\w])((?:/[A-Za-z0-9_.-]+){2,}/[A-Za-z0-9_.-]+\.[A-Za-z0-9]+)")
    _ACCOUNT_ID_PATTERN = re.compile(
        r"(?i)((?:user[_-]?id|account[_-]?id|login[_-]?id|member[_-]?id)\b\s*[:=]\s*[\"']?)([A-Za-z0-9_@-]{3,})(?=[\"'\s,\)\]}]|$)([\"']?)"
    )
    _PERSON_LABEL_PATTERN = re.compile(
        r"(?i)((?:name|user\s*name|customer\s*name|person|담당자|이름)\b\s*[:=]\s*[\"']?)(([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)|([가-힣]{2,10}))([\"']?)"
    )
    _ORG_LABEL_PATTERN = re.compile(
        r"(?i)((?:company|client|customer|organization|org|고객사|회사)\b\s*[:=]\s*[\"']?)([A-Za-z][A-Za-z0-9 .&-]{1,60}|[가-힣A-Za-z0-9 .&-]{2,40})([\"']?)"
    )
    _SQL_TABLE_PATTERN = re.compile(r"(?i)\b(from|join|update|into|table)\s+([A-Za-z_][A-Za-z0-9_$]*)")
    _SQL_COLUMN_DEF_PATTERN = re.compile(rf"(?i)\b([A-Za-z_][A-Za-z0-9_$]*)\b(?=\s+(?:{_SQL_TYPE_PATTERN})\b)")
    _SQL_CONTEXT_COLUMN_PATTERN = re.compile(
        r"(?i)\b(select|where|and|or|order\s+by|group\s+by|having|on|set)\s+([A-Za-z_][A-Za-z0-9_$]*(?:\.[A-Za-z_][A-Za-z0-9_$]*)?)"
    )
    _SQL_LIST_COLUMN_PATTERN = re.compile(
        r"(?i)(,\s*)([A-Za-z_][A-Za-z0-9_$]*(?:\.[A-Za-z_][A-Za-z0-9_$]*)?)(?=\s*(?:,|from|where|group|order|having|limit|offset|$))"
    )
    _SQL_INSERT_COLUMNS_PATTERN = re.compile(
        r"(?i)(\binsert\s+into\s+[A-Za-z_][A-Za-z0-9_$]*\s*\()([^)]+)(\))"
    )
    _CLASS_PATTERN = re.compile(r"(?i)\b(class|interface|enum)\s+([A-Za-z_][A-Za-z0-9_]*)")
    _PY_FUNCTION_PATTERN = re.compile(r"(?m)\bdef\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(")
    _JS_FUNCTION_PATTERN = re.compile(r"(?m)\bfunction\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(")
    _JAVA_METHOD_PATTERN = re.compile(
        r"(?m)\b(?:public|private|protected)\s+(?:static\s+)?(?:[A-Za-z_][A-Za-z0-9_<>\[\]]*\s+)+([A-Za-z_][A-Za-z0-9_]*)\s*\("
    )
    _API_PATH_PATTERN = re.compile(
        r"([\"'])(/(?:api|v\d+|rest|admin|internal)(?:/[A-Za-z0-9._{}-]+)+)(\1)"
    )

    def sanitize(self, input_model: InputAssemblerV0Output) -> AnonymizationV0Output:
        context = _ReplacementContext()
        sanitized_input = input_model.model_copy(deep=True)
        total_replacements = 0
        request_context_redacted = False
        replaced_namespaces: set[str] = set()
        structure_risk_flags: list[str] = []

        goal_outcome = self._sanitize_text(sanitized_input.request_context.goal, context)
        if sanitized_input.request_context.goal != goal_outcome.text:
            request_context_redacted = True
            sanitized_input.request_context.goal = goal_outcome.text
        total_replacements += goal_outcome.replacement_count
        replaced_namespaces.update(goal_outcome.namespaces)

        sanitized_constraints: list[str] = []
        for constraint in sanitized_input.request_context.constraints:
            outcome = self._sanitize_text(constraint, context)
            sanitized_constraints.append(outcome.text)
            if constraint != outcome.text:
                request_context_redacted = True
            total_replacements += outcome.replacement_count
            replaced_namespaces.update(outcome.namespaces)
        sanitized_input.request_context.constraints = sanitized_constraints

        asset_reports: list[AnonymizationAssetReport] = []
        for asset in sanitized_input.asset_inventory:
            filename, filename_count, filename_rules, filename_namespaces = self._sanitize_filename(asset.filename, context)
            source_ref, source_ref_count, source_ref_rules, source_ref_namespaces = self._sanitize_source_ref(asset.source_ref, context)
            filename_redacted = filename != asset.filename
            source_ref_redacted = source_ref != asset.source_ref
            asset.filename = filename
            asset.source_ref = source_ref
            total_replacements += filename_count + source_ref_count
            replaced_namespaces.update(filename_namespaces)
            replaced_namespaces.update(source_ref_namespaces)
            asset_reports.append(
                AnonymizationAssetReport(
                    asset_id=asset.asset_id,
                    filename_redacted=filename_redacted,
                    source_ref_redacted=source_ref_redacted,
                )
            )

        block_reports: list[AnonymizationBlockReport] = []
        for block in sanitized_input.source_blocks:
            original_text = block.text
            outcome = self._sanitize_text(block.text, context)
            block.text = outcome.text
            total_replacements += outcome.replacement_count
            replaced_namespaces.update(outcome.namespaces)
            block_reports.append(
                AnonymizationBlockReport(
                    block_id=block.block_id,
                    replacement_count=outcome.replacement_count,
                    applied_rules=list(outcome.applied_rules),
                )
            )
            token_count = max(len(_WORD_LIKE_PATTERN.findall(original_text or "")), 1)
            if outcome.replacement_count and outcome.replacement_count / token_count >= 0.20:
                self._append_unique(structure_risk_flags, "high_redaction_density")

        if replaced_namespaces & {"TABLE", "COL", "CLASS", "FUNC", "API_PATH"}:
            self._append_unique(structure_risk_flags, "identifier_like_tokens_redacted")
        if replaced_namespaces & {"TABLE", "COL"}:
            self._append_unique(structure_risk_flags, "sql_identifiers_pseudonymized")

        return AnonymizationV0Output(
            sanitized_input=sanitized_input,
            anonymization_report=AnonymizationReport(
                request_context_redacted=request_context_redacted,
                block_reports=block_reports,
                asset_reports=asset_reports,
                redaction_summary=AnonymizationRedactionSummary(total_replacements=total_replacements),
                structure_risk_flags=structure_risk_flags,
            ),
        )

    def _sanitize_text(self, text: str | None, context: _ReplacementContext) -> _SanitizeOutcome:
        current = text or ""
        outcome = _SanitizeOutcome(text=current)
        if not current:
            return outcome
        for transform in (
            self._sanitize_secrets,
            self._sanitize_email,
            self._sanitize_phone,
            self._sanitize_hosts,
            self._sanitize_paths,
            self._sanitize_account_ids,
            self._sanitize_labeled_entities,
            self._sanitize_structured_identifiers,
        ):
            transformed = transform(current, context)
            current = transformed.text
            outcome.replacement_count += transformed.replacement_count
            outcome.namespaces.update(transformed.namespaces)
            for rule_name in transformed.applied_rules:
                self._append_unique(outcome.applied_rules, rule_name)
        outcome.text = current
        return outcome

    def _sanitize_secrets(self, text: str, context: _ReplacementContext) -> _SanitizeOutcome:
        current = text
        outcome = _SanitizeOutcome(text=current)
        for pattern in (self._AUTH_HEADER_PATTERN, self._ENV_SECRET_PATTERN, self._GENERIC_SECRET_PATTERN):
            current, count = self._replace_group(current, pattern, 2, "SECRET", context)
            if count:
                outcome.replacement_count += count
                outcome.namespaces.add("SECRET")
                self._append_unique(outcome.applied_rules, "secret_token")
        current, count = self._replace_group(current, self._BARE_SECRET_PATTERN, 0, "SECRET", context)
        if count:
            outcome.replacement_count += count
            outcome.namespaces.add("SECRET")
            self._append_unique(outcome.applied_rules, "secret_token")
        outcome.text = current
        return outcome

    def _sanitize_email(self, text: str, context: _ReplacementContext) -> _SanitizeOutcome:
        current, count = self._replace_group(text, self._EMAIL_PATTERN, 1, "EMAIL", context)
        return self._single_rule_outcome(current, count, "email", "EMAIL")

    def _sanitize_phone(self, text: str, context: _ReplacementContext) -> _SanitizeOutcome:
        current, count = self._replace_group(text, self._PHONE_PATTERN, 1, "PHONE", context)
        return self._single_rule_outcome(current, count, "phone", "PHONE")

    def _sanitize_hosts(self, text: str, context: _ReplacementContext) -> _SanitizeOutcome:
        current = text
        outcome = _SanitizeOutcome(text=current)
        current, count = self._replace_group(current, self._URL_HOST_PATTERN, 2, "HOST", context)
        if count:
            outcome.replacement_count += count
            outcome.namespaces.add("HOST")
            self._append_unique(outcome.applied_rules, "host")
        current, count = self._replace_group(current, self._IP_PATTERN, 1, "HOST", context)
        if count:
            outcome.replacement_count += count
            outcome.namespaces.add("HOST")
            self._append_unique(outcome.applied_rules, "host")
        outcome.text = current
        return outcome

    def _sanitize_paths(self, text: str, context: _ReplacementContext) -> _SanitizeOutcome:
        current = text
        outcome = _SanitizeOutcome(text=current)
        for pattern in (self._WINDOWS_PATH_PATTERN, self._UNIX_PATH_PATTERN):
            current, count = self._replace_group(current, pattern, 1, "PATH", context)
            if count:
                outcome.replacement_count += count
                outcome.namespaces.add("PATH")
                self._append_unique(outcome.applied_rules, "path")
        outcome.text = current
        return outcome

    def _sanitize_account_ids(self, text: str, context: _ReplacementContext) -> _SanitizeOutcome:
        current, count = self._replace_group(text, self._ACCOUNT_ID_PATTERN, 2, "ACCOUNT_ID", context)
        return self._single_rule_outcome(current, count, "account_id", "ACCOUNT_ID")

    def _sanitize_labeled_entities(self, text: str, context: _ReplacementContext) -> _SanitizeOutcome:
        current = text
        outcome = _SanitizeOutcome(text=current)
        current, person_count = self._replace_group(current, self._PERSON_LABEL_PATTERN, 2, "PERSON", context)
        if person_count:
            outcome.replacement_count += person_count
            outcome.namespaces.add("PERSON")
            self._append_unique(outcome.applied_rules, "person")
        current, org_count = self._replace_group(current, self._ORG_LABEL_PATTERN, 2, "ORG", context)
        if org_count:
            outcome.replacement_count += org_count
            outcome.namespaces.add("ORG")
            self._append_unique(outcome.applied_rules, "org")
        outcome.text = current
        return outcome

    def _sanitize_structured_identifiers(self, text: str, context: _ReplacementContext) -> _SanitizeOutcome:
        current = text
        outcome = _SanitizeOutcome(text=current)

        current, count = self._replace_group(current, self._SQL_TABLE_PATTERN, 2, "TABLE", context)
        if count:
            outcome.replacement_count += count
            outcome.namespaces.add("TABLE")
            self._append_unique(outcome.applied_rules, "sql_table")

        current, count = self._replace_group(current, self._SQL_COLUMN_DEF_PATTERN, 1, "COL", context, normalizer=self._normalize_sql_identifier)
        if count:
            outcome.replacement_count += count
            outcome.namespaces.add("COL")
            self._append_unique(outcome.applied_rules, "sql_column")

        current, context_count = self._replace_sql_context_columns(current, context)
        if context_count:
            outcome.replacement_count += context_count
            outcome.namespaces.add("COL")
            self._append_unique(outcome.applied_rules, "sql_column")

        current, insert_count = self._replace_sql_insert_columns(current, context)
        if insert_count:
            outcome.replacement_count += insert_count
            outcome.namespaces.add("COL")
            self._append_unique(outcome.applied_rules, "sql_column")

        current, count = self._replace_group(current, self._CLASS_PATTERN, 2, "CLASS", context)
        if count:
            outcome.replacement_count += count
            outcome.namespaces.add("CLASS")
            self._append_unique(outcome.applied_rules, "class_identifier")

        for pattern in (self._PY_FUNCTION_PATTERN, self._JS_FUNCTION_PATTERN, self._JAVA_METHOD_PATTERN):
            current, count = self._replace_group(current, pattern, 1, "FUNC", context)
            if count:
                outcome.replacement_count += count
                outcome.namespaces.add("FUNC")
                self._append_unique(outcome.applied_rules, "function_identifier")

        current, count = self._replace_group(current, self._API_PATH_PATTERN, 2, "API_PATH", context)
        if count:
            outcome.replacement_count += count
            outcome.namespaces.add("API_PATH")
            self._append_unique(outcome.applied_rules, "api_path")

        outcome.text = current
        return outcome

    def _sanitize_filename(
        self,
        filename: str | None,
        context: _ReplacementContext,
    ) -> tuple[str | None, int, list[str], set[str]]:
        if not filename:
            return filename, 0, [], set()
        if "." in filename:
            stem, extension = filename.rsplit(".", 1)
            extension_part = f".{extension}"
        else:
            stem, extension_part = filename, ""
        if not stem or _TOKENIZED_VALUE_PATTERN.match(stem):
            return filename, 0, [], set()
        return f"{context.token_for('FILE', stem)}{extension_part}", 1, ["filename"], {"FILE"}

    def _sanitize_source_ref(
        self,
        source_ref: str | None,
        context: _ReplacementContext,
    ) -> tuple[str | None, int, list[str], set[str]]:
        if not source_ref or _TOKENIZED_VALUE_PATTERN.match(source_ref):
            return source_ref, 0, [], set()
        return context.token_for("SRCREF", source_ref), 1, ["source_ref"], {"SRCREF"}

    def _replace_sql_context_columns(self, text: str, context: _ReplacementContext) -> tuple[str, int]:
        current, first_count = self._replace_sql_column_group(text, self._SQL_CONTEXT_COLUMN_PATTERN, 2, context)
        current, list_count = self._replace_sql_column_group(current, self._SQL_LIST_COLUMN_PATTERN, 2, context)
        return current, first_count + list_count

    def _replace_sql_insert_columns(self, text: str, context: _ReplacementContext) -> tuple[str, int]:
        count = 0

        def repl(match: re.Match[str]) -> str:
            nonlocal count
            prefix, raw_columns, suffix = match.groups()
            rewritten: list[str] = []
            changed = False
            for part in raw_columns.split(","):
                column = part.strip()
                if not column:
                    rewritten.append(part)
                    continue
                tokenized = self._pseudonymize_sql_identifier(column, context, "COL")
                if tokenized != column:
                    count += 1
                    changed = True
                rewritten.append(part.replace(column, tokenized, 1))
            if not changed:
                return match.group(0)
            return f"{prefix}{','.join(rewritten)}{suffix}"

        return self._SQL_INSERT_COLUMNS_PATTERN.sub(repl, text), count

    def _replace_sql_column_group(
        self,
        text: str,
        pattern: re.Pattern[str],
        group_index: int,
        context: _ReplacementContext,
    ) -> tuple[str, int]:
        count = 0

        def repl(match: re.Match[str]) -> str:
            nonlocal count
            raw_value = match.group(group_index)
            if not raw_value:
                return match.group(0)
            replaced_value = self._pseudonymize_sql_identifier(raw_value, context, "COL")
            if replaced_value == raw_value:
                return match.group(0)
            count += 1
            return self._replace_group_slice(match, group_index, replaced_value)

        return pattern.sub(repl, text), count

    def _pseudonymize_sql_identifier(self, raw_value: str, context: _ReplacementContext, namespace: str) -> str:
        value = self._normalize_sql_identifier(raw_value)
        if not value:
            return raw_value
        if "." in value:
            qualifier, identifier = value.rsplit(".", 1)
            token = self._sql_token_or_none(identifier, context, namespace)
            if not token:
                return raw_value
            return f"{qualifier}.{token}"
        token = self._sql_token_or_none(value, context, namespace)
        return token or raw_value

    def _sql_token_or_none(self, identifier: str, context: _ReplacementContext, namespace: str) -> str | None:
        normalized = self._normalize_sql_identifier(identifier)
        if not normalized:
            return None
        if normalized.lower() in _SQL_KEYWORDS or _TOKENIZED_VALUE_PATTERN.match(normalized):
            return None
        return context.token_for(namespace, normalized)

    def _normalize_sql_identifier(self, value: str) -> str:
        return (value or "").strip().strip(",").strip("()")

    def _replace_group(
        self,
        text: str,
        pattern: re.Pattern[str],
        group_index: int,
        namespace: str,
        context: _ReplacementContext,
        *,
        normalizer=None,
    ) -> tuple[str, int]:
        count = 0

        def repl(match: re.Match[str]) -> str:
            nonlocal count
            raw_value = match.group(group_index)
            if not raw_value:
                return match.group(0)
            lookup_value = normalizer(raw_value) if normalizer else raw_value
            if not lookup_value or _TOKENIZED_VALUE_PATTERN.match(lookup_value):
                return match.group(0)
            token = context.token_for(namespace, lookup_value)
            if token == raw_value:
                return match.group(0)
            count += 1
            return self._replace_group_slice(match, group_index, token)

        return pattern.sub(repl, text), count

    def _replace_group_slice(self, match: re.Match[str], group_index: int, replacement: str) -> str:
        if group_index == 0:
            return replacement
        start, end = match.span(group_index)
        prefix = match.group(0)[: start - match.start(0)]
        suffix = match.group(0)[end - match.start(0) :]
        return f"{prefix}{replacement}{suffix}"

    def _single_rule_outcome(self, text: str, count: int, rule_name: str, namespace: str) -> _SanitizeOutcome:
        if not count:
            return _SanitizeOutcome(text=text)
        return _SanitizeOutcome(
            text=text,
            replacement_count=count,
            applied_rules=[rule_name],
            namespaces={namespace},
        )

    def _append_unique(self, items: list[str], value: str) -> None:
        if value not in items:
            items.append(value)
