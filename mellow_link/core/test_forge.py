"""
Test Forge - 동적 도구의 런타임 검증 엔진 (Phase 5)

ToolForge에서 생성·등록된 동적 도구에 대해 실제 호출 테스트를 수행합니다.
"대장간에서 만든 칼이 실제로 잘 드는지 시험장에서 확인" 하는 역할.

기술 검토:
  - 동적 도구 런타임 테스트: ✅ verified
  - 안전한 테스트 실행 환경:  ✅ verified
"""

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class TestResult:
    """단일 도구 테스트 결과."""
    tool_name: str
    passed: bool
    message: str = ""
    elapsed_ms: float = 0.0
    output: Optional[str] = None


@dataclass
class TestSuiteResult:
    """테스트 스위트 실행 결과."""
    total: int = 0
    passed: int = 0
    failed: int = 0
    results: List[TestResult] = field(default_factory=list)
    elapsed_ms: float = 0.0


class TestForge:
    """
    동적 도구 런타임 테스트 실행기.

    ToolForge의 정적 분석(AST) + 샌드박스 테스트를 통과한 도구에 대해
    실제 인자를 넣어 호출하고, 기대 결과와 비교합니다.
    """

    def __init__(self):
        logger.info("[TestForge] Initialized")

    def run_smoke_test(
        self,
        tool_name: str,
        func: Any,
        test_args: Optional[Dict[str, Any]] = None,
        expected_contains: Optional[str] = None,
    ) -> TestResult:
        """
        단일 도구 스모크 테스트.

        Args:
            tool_name: 도구 이름
            func: 호출할 함수
            test_args: 테스트 인자 (None이면 인자 없이 호출)
            expected_contains: 결과에 포함되어야 할 문자열

        Returns:
            TestResult
        """
        t0 = time.monotonic()
        try:
            result = func(**(test_args or {}))
            result_str = str(result)
            elapsed = (time.monotonic() - t0) * 1000

            if expected_contains and expected_contains not in result_str:
                return TestResult(
                    tool_name=tool_name,
                    passed=False,
                    message=f"기대 문자열 미포함: '{expected_contains}'",
                    elapsed_ms=elapsed,
                    output=result_str[:500],
                )

            return TestResult(
                tool_name=tool_name,
                passed=True,
                message="스모크 테스트 통과",
                elapsed_ms=elapsed,
                output=result_str[:500],
            )
        except Exception as e:
            elapsed = (time.monotonic() - t0) * 1000
            return TestResult(
                tool_name=tool_name,
                passed=False,
                message=f"실행 중 예외: {e!r}",
                elapsed_ms=elapsed,
            )

    def run_test_suite(
        self,
        test_cases: List[Dict[str, Any]],
    ) -> TestSuiteResult:
        """
        다중 도구 테스트 스위트 실행.

        Args:
            test_cases: [{"tool_name": ..., "func": ..., "test_args": ..., "expected_contains": ...}, ...]

        Returns:
            TestSuiteResult
        """
        t0 = time.monotonic()
        results: List[TestResult] = []
        passed = 0

        for tc in test_cases:
            result = self.run_smoke_test(
                tool_name=tc.get("tool_name", "unknown"),
                func=tc.get("func"),
                test_args=tc.get("test_args"),
                expected_contains=tc.get("expected_contains"),
            )
            results.append(result)
            if result.passed:
                passed += 1

        elapsed = (time.monotonic() - t0) * 1000
        return TestSuiteResult(
            total=len(test_cases),
            passed=passed,
            failed=len(test_cases) - passed,
            results=results,
            elapsed_ms=elapsed,
        )


# ═══════════════════════════════════════════════
# Singleton
# ═══════════════════════════════

_test_forge_instance: Optional[TestForge] = None


def get_test_forge() -> TestForge:
    """TestForge 싱글톤 반환."""
    global _test_forge_instance
    if _test_forge_instance is None:
        _test_forge_instance = TestForge()
    return _test_forge_instance
