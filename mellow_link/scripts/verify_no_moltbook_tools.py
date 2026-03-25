#!/usr/bin/env python3
"""
[COMMAND: PROJECT_PURGE_MOLTBOOK] 검증 스크립트.

get_tool_names()를 호출하여 몰트북 관련 도구명이 단 하나도 남아있지 않은지 확인합니다.
실행: python -m mellow_link.scripts.verify_no_moltbook_tools
또는: cd mellow_link && python scripts/verify_no_moltbook_tools.py
"""
from __future__ import annotations

import sys


def main() -> int:
    # agent_tools를 로드하면 동적 레지스트리에 모든 도구가 등록됨
    import mellow_link.core.agent_tools  # noqa: F401
    from mellow_link.core.dynamic_registry import get_dynamic_registry

    registry = get_dynamic_registry()
    names = registry.get_tool_names()
    moltbook_related = [n for n in names if "moltbook" in n.lower()]
    if moltbook_related:
        print(f"[FAIL] 몰트북 관련 도구명이 남아 있음: {moltbook_related}")
        return 1
    print("✅ verified: get_tool_names() 결과에 몰트북 관련 이름이 단 하나도 없습니다.")
    print(f"   등록된 도구 수: {len(names)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
