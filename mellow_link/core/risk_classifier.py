"""
위험도 분류기 - Tiered Auditing용

코드 분석으로 위험도를 1~3으로 분류하여 검수관 라우팅에 사용합니다.
- Level 1 (단순 조회): GPT-4o-mini 검수 또는 경량 검수
- Level 2 (파일 쓰기/수정): Claude 3.5 Sonnet 정밀 검수
- Level 3 (네트워크/시스템): Claude 최종 승인 필수
"""

import re
import logging
from typing import Tuple

logger = logging.getLogger(__name__)

# Level 3: 네트워크/시스템 - 최고 위험 (감시망 강화)
_LEVEL3_PATTERNS = [
    r"\bsubprocess\b",
    r"\bos\.system\b",
    r"\beval\s*\(",
    r"\bexec\s*\(",
    r"__import__\s*\(",
    r"\brequests\.(get|post|put|delete|patch)\b",
    r"\burllib\.request\.(urlopen|Request)\b",
    r"\bsocket\.\w+",
    r"\bPopen\b",
    r"subprocess\.run\b",
    r"subprocess\.call\b",
    r"\bmultiprocessing\.Process\b",
    r"\bthreading\.Thread\b",
    r"shell\s*=\s*True",
    r"\.communicate\s*\(",
    r"\bimportlib\b",
    r"\bctypes\b",
    r"\bbuiltins\b",
    r"asyncio\.create_subprocess\b",
    r"asyncio\.create_subprocess_exec\b",
    r"asyncio\.create_subprocess_shell\b",
    r"\bwebbrowser\b",
    r"\bhttp\.client\b",
    r"\bhttplib\b",
    r"\bsmtplib\b",
    r"\bftplib\b",
    r"\btelnetlib\b",
    r"__builtins__",
    r"\\x[0-9a-fA-F]{2}",
    r"chr\s*\(",
]

# Level 2: 파일 쓰기/수정
_LEVEL2_PATTERNS = [
    r"open\s*\([^)]*['\"]w",
    r"open\s*\([^)]*['\"]a",
    r"open\s*\([^)]*['\"]x",
    r"\.write_text\s*\(",
    r"\.write_bytes\s*\(",
    r"\.write\s*\(",
    r"\bshutil\.(copy|move|rmtree)\b",
    r"\bos\.remove\b",
    r"\bos\.unlink\b",
    r"Path\([^)]+\)\.unlink",
    r"\.unlink\s*\(",
    r"\bPath\([^)]+\)\.touch\b",
    r"\.mkdir\s*\(",
    r"\.makedirs\b",
]

# Level 1: 단순 조회 - listdir, iterdir, read, print 등
# (위 패턴에 매칭되지 않으면 Level 1)


def classify_code_risk_level(code: str) -> Tuple[int, str]:
    """
    Python 코드의 위험도를 1~3으로 분류.

    Args:
        code: 분석할 Python 코드

    Returns:
        (level, reason) - level 1|2|3, 간단한 사유
    """
    if not code or not isinstance(code, str):
        return 1, "empty_or_invalid"

    text = code.strip()
    if len(text) < 10:
        return 1, "trivial"

    # 주석/문자열 내 패턴 오탐 감소: 간단히 """ """ 블록 제거 (미완성 고려해 보수적)
    def _reduce_strings(s: str) -> str:
        # 삼중 따옴표 블록 제거
        s = re.sub(r'"""[^"]*"""', '""', s, flags=re.DOTALL)
        s = re.sub(r"'''[^']*'''", "''", s, flags=re.DOTALL)
        return s

    reduced = _reduce_strings(text)

    # Level 3 체크
    for pat in _LEVEL3_PATTERNS:
        if re.search(pat, reduced, re.IGNORECASE):
            logger.debug("[RiskClassifier] Level 3: pattern=%s", pat)
            return 3, f"pattern:{pat[:30]}"

    # Level 2 체크
    for pat in _LEVEL2_PATTERNS:
        if re.search(pat, reduced, re.IGNORECASE):
            logger.debug("[RiskClassifier] Level 2: pattern=%s", pat)
            return 2, f"pattern:{pat[:30]}"

    # Level 1: 단순 조회/계산
    return 1, "read_only"
