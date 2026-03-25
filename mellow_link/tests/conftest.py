"""
Pytest conftest: ensure project root is on sys.path so `mellow_link` can be imported.
Register markers for CI split: core_required vs env_policy.
"""
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def pytest_configure(config):
    """Register custom markers (core_required, env_policy) for CI/test separation."""
    config.addinivalue_line(
        "markers",
        "core_required: Core behavior; CI should require pass (default for most tests).",
    )
    config.addinivalue_line(
        "markers",
        "env_policy: Environment or security-policy dependent; run optionally in CI.",
    )
