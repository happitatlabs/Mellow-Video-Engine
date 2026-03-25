"""
보고서 생성 도구용 보안 마스킹 (Security Masking).

- 개인정보, API 키, 패스워드 등: 정규표현식으로 탐지하여 *** 로 치환.
- 시스템 핵심 경로: sandbox 기준 상대 경로(~/...) 형태로만 표시.
"""

import re
from pathlib import Path
from typing import Tuple, Optional

# 치환 문자열 (공통)
_MASK = "***"

# 개인정보·API 키·패스워드 등 민감 패턴 (치환 시 전체 매치를 *** 로)
_SENSITIVE_PATTERNS: Tuple[re.Pattern, ...] = (
    re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b"),  # 이메일
    re.compile(r"\b(?:sk|pk)-[a-zA-Z0-9]{20,}\b"),  # OpenAI 등 API key
    re.compile(r"https?://[^\s\"']*/claim/[^\s\"']+"),  # claim URL
    re.compile(r"\b(?:password|passwd|pwd|secret)\s*[:=]\s*[\"']?[^\s\"']{4,}[\"']?", re.I),
    re.compile(r"\b(?:api[_-]?key|apikey)\s*[:=]\s*[\"']?[^\s\"']{8,}[\"']?", re.I),
    re.compile(r"\bBearer\s+[A-Za-z0-9._-]{20,}\b"),
    re.compile(r"\b[0-9]{6}\s*-?\s*[0-9]{7}\b"),  # 주민등록번호 형식
    # 클라우드·클라이언트 키 확장
    re.compile(r"\b(AKIA|ASIA)[A-Z0-9]{16}\b"),  # AWS Keys
    re.compile(r"\b(ghp|gho|ghs)_[a-zA-Z0-9]{36,}\b"),  # GitHub PAT
    re.compile(r"\bsk-ant-[a-zA-Z0-9_-]{20,}\b"),  # Anthropic API
    re.compile(r"\bAIza[A-Za-z0-9_-]{35}\b"),  # Google API
    re.compile(r"\b(?:10\.|172\.(?:1[6-9]|2[0-9]|3[01])\.|192\.168\.)[0-9.]+\b"),  # 내부 IP
    re.compile(r"C:\\Users\\[^\s\"']+"),  # Windows 절대 경로 (C:\Users\..., sandbox 외부 노출 방지)
)


def mask_sensitive(text: str) -> str:
    """민감 패턴을 *** 로 치환한 문자열 반환."""
    if not text:
        return text
    out = text
    for pat in _SENSITIVE_PATTERNS:
        out = pat.sub(_MASK, out)
    return out


def mask_system_paths(text: str, sandbox_root: Optional[Path] = None) -> str:
    """
    절대 경로를 sandbox 기준 상대 표기(~/...)로 치환.
    sandbox_root가 None이면 경로 치환하지 않음.
    """
    if not text or not sandbox_root:
        return text
    root = Path(sandbox_root).resolve()
    root_str = str(root)
    root_str_alt = root_str.replace("\\", "/")
    # Windows/혼합 대응: 역슬래시 버전도 치환
    out = text.replace(root_str, "~").replace(root_str_alt, "~")
    # ~/ 가 한 번만 나오도록 (이미 ~ 인 경우 중복 치환 방지)
    out = re.sub(r"~+/", "~/", out)
    return out


def mask_report_content(
    text: str,
    sandbox_root: Optional[Path] = None,
) -> Tuple[str, int]:
    """
    보고서 본문에 대해 민감 정보 + 경로 마스킹 적용.

    Returns:
        (masked_text, chars_masked)
        chars_masked: 마스킹으로 줄어든 문자 수(원문 길이 - 결과 길이). 하이브리드 보고 판단용.
    """
    if not text:
        return "", 0
    original_len = len(text)
    out = mask_sensitive(text)
    out = mask_system_paths(out, sandbox_root)
    chars_masked = original_len - len(out)
    return out, max(0, chars_masked)


def is_too_sensitive(
    original_len: int,
    chars_masked: int,
    ratio_threshold: float = 0.15,
) -> bool:
    """치환 비율이 threshold 이상이면 민감하여 vault 안내를 할지 판단."""
    if original_len <= 0:
        return False
    return (chars_masked / original_len) >= ratio_threshold
