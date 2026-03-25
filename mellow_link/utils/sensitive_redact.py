"""
로그/이벤트 파이프라인 민감정보 마스킹 (공통).

- KEY/SECRET/TOKEN/BEARER/Authorization/OPENAI/ANTHROPIC/GOOGLE 포함 문자열 마스킹
- run_events, 로깅 Formatter 등에서 사용
"""

import logging
import re
from typing import Any, List, Tuple

# 치환 결과 (공통)
REDACTED_PLACEHOLDER = "[REDACTED]"
REDACTED_API_KEY = "[REDACTED_API_KEY]"

# 민감 패턴: (정규식, 치환 문자열)
# KEY/SECRET/TOKEN/BEARER/Authorization/OPENAI/ANTHROPIC/GOOGLE 강화
REDACTION_PATTERNS: List[Tuple[str, str]] = [
    # Authorization 헤더 (한 줄 전체 치환)
    (r"(?i)authorization\s*:\s*[^\n]+", "Authorization: [REDACTED]"),
    (r"(?i)bearer\s+[a-zA-Z0-9_\-\.]{20,}", "Bearer [REDACTED]"),
    # env 스타일: OPENAI_API_KEY=..., ANTHROPIC_API_KEY=..., GOOGLE_API_KEY=...
    (r"(?i)(OPENAI_API_KEY|ANTHROPIC_API_KEY|GOOGLE_API_KEY|GUARDIAN_[A-Z_]*KEY)\s*[:=]\s*[\"']?[^\s\"'\n]{10,}[\"']?", r"\1=[REDACTED]"),
    # 일반 KEY/SECRET/TOKEN (이름=값)
    (r"(?i)(api[_-]?key|apikey)\s*[:=]\s*[\"']?([a-zA-Z0-9_\-\.]{20,})[\"']?", r"\1: [REDACTED]"),
    (r"(?i)(password|passwd|pwd|secret|token)\s*[:=]\s*[\"']?([^\s\"'<>]{8,})[\"']?", r"\1: [REDACTED]"),
    # OpenAI / Anthropic / Google 키 형식 (값만 있어도 마스킹)
    (r"sk-[a-zA-Z0-9_\-]{32,}", REDACTED_API_KEY),
    (r"sk-ant-[a-zA-Z0-9\-_]{50,}", REDACTED_API_KEY),
    (r"AIza[0-9A-Za-z\-_]{35}", REDACTED_API_KEY),
    # OPENAI/ANTHROPIC/GOOGLE 문자열 뒤에 나오는 긴 값 (키 이름 없이 값만 있을 때)
    (r"(?i)(openai|anthropic|google)\s*[:\s]+\s*[a-zA-Z0-9_\-\.]{20,}", r"\1: [REDACTED]"),
]

_COMPILED_PATTERNS: List[Tuple[re.Pattern, str]] = [
    (re.compile(p), r) for p, r in REDACTION_PATTERNS
]


def redact_sensitive_data(text: str) -> str:
    """
    민감한 정보를 마스킹.
    KEY/SECRET/TOKEN/BEARER/Authorization/OPENAI/ANTHROPIC/GOOGLE 포함 문자열 처리.
    """
    if not text or not isinstance(text, str):
        return text
    result = text
    for pattern, replacement in _COMPILED_PATTERNS:
        result = pattern.sub(replacement, result)
    return result


def redact_dict_recursive(obj: Any) -> Any:
    """딕셔너리를 재귀 순회하며 문자열 필드만 redact."""
    if isinstance(obj, dict):
        return {k: redact_dict_recursive(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [redact_dict_recursive(item) for item in obj]
    if isinstance(obj, str):
        return redact_sensitive_data(obj)
    return obj


def redact_for_logging(message: str) -> str:
    """로깅용: 메시지 문자열만 redact (Formatter에서 호출)."""
    return redact_sensitive_data(message)


class SensitiveRedactingFormatter(logging.Formatter):
    """
    로그 포맷 결과 전체(메시지 + traceback)에 민감정보 마스킹 적용.
    logger.exception / traceback 출력 경로에서 env·키 유출 방지.
    """

    def __init__(self, fmt=None, datefmt=None, style="%"):
        super().__init__(fmt=fmt, datefmt=datefmt, style=style)

    def format(self, record: logging.LogRecord) -> str:
        formatted = super().format(record)
        return redact_sensitive_data(formatted)
