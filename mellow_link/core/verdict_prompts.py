"""
Verdict 모델용 공유 프롬프트/가이드라인.

Python 코드 생성 시 사용되는 I/O 표준 등 Verdict 관련 지침을 로드합니다.
"""

import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_VERDICT_IO_STANDARDS: Optional[str] = None


def get_verdict_io_standards() -> str:
    """Verdict용 Python I/O 표준 가이드라인 로드."""
    global _VERDICT_IO_STANDARDS
    if _VERDICT_IO_STANDARDS is not None:
        return _VERDICT_IO_STANDARDS
    try:
        base = Path(__file__).resolve().parent.parent
        p = base / "prompts" / "verdict_python_io_standards.txt"
        if p.exists():
            _VERDICT_IO_STANDARDS = p.read_text(encoding="utf-8").strip()
        else:
            _VERDICT_IO_STANDARDS = ""
    except Exception as e:
        logger.debug("[VerdictPrompts] IO standards load failed: %s", e)
        _VERDICT_IO_STANDARDS = ""
    return _VERDICT_IO_STANDARDS
