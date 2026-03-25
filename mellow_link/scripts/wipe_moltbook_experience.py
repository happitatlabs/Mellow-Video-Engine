#!/usr/bin/env python3
"""
[COMMAND: PROJECT_PURGE_MOLTBOOK] 메모리 DB 정리.

mellow_link_memory.db의 experience_ledger에서 used_tools에 몰트북 도구명이 포함된
레코드를 삭제하거나, DB를 초기화할 수 있습니다.

사용법:
  # 몰트북 도구를 사용한 경험 레코드만 삭제
  python scripts/wipe_moltbook_experience.py --delete-entries

  # DB 파일 삭제 후 재생성 (완전 초기화)
  python scripts/wipe_moltbook_experience.py --reset-db
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


# 제거된 몰트북 도구명 목록 (레코드 필터링용)
_MOLTBOOK_TOOL_NAMES = frozenset({
    "comment_on_moltbook", "upvote_post", "list_moltbook_posts", "read_moltbook_feed",
    "post_to_moltbook", "post_comment", "list_moltbkk_posts", "search_moltbook_posts",
    "list_moltbook_posts_by_author", "get_moltbook_comments", "upvote_moltbook_comment",
    "upvote_moltbook_post", "downvote_moltbook_post", "follow_moltbook_agent",
    "unfollow_moltbook_agent", "register_moltbook", "debug_moltbook_auth",
    "track_moltbook_thread", "untrack_moltbook_thread", "list_tracked_moltbook_threads",
    "save_moltbook_data", "upvote_and_follow_post_author", "upvote_and_ffollow_post_author",
    "follow_agent", "follow_molty", "upvote_and_follow_latest_post_by_author",
})


def delete_moltbook_entries() -> int:
    """experience_ledger에서 used_tools에 몰트북 도구가 포함된 행 삭제."""
    try:
        from mellow_link.infra.memory_database import get_memory_db
    except ImportError as e:
        print(f"[Error] import 실패: {e}")
        return 1
    db = get_memory_db()
    conn = db._connection
    if conn is None:
        print("[Error] DB 연결 없음")
        return 1
    cursor = conn.execute(
        "SELECT id, used_tools FROM experience_ledger WHERE used_tools IS NOT NULL AND used_tools != ''"
    )
    rows = cursor.fetchall()
    deleted = 0
    for row in rows:
        try:
            tools = json.loads(row["used_tools"]) if isinstance(row["used_tools"], str) else row["used_tools"]
        except Exception:
            continue
        if not isinstance(tools, list):
            continue
        if any(t in _MOLTBOOK_TOOL_NAMES for t in tools):
            conn.execute("DELETE FROM experience_ledger WHERE id = ?", (row["id"],))
            deleted += 1
    conn.commit()
    print(f"✅ verified: 몰트북 관련 경험 레코드 {deleted}건 삭제됨.")
    return 0


def reset_db() -> int:
    """mellow_link_memory.db 파일 삭제. 다음 기동 시 재생성됨."""
    # 기본 경로: mellow_link/data/mellow_link_memory.db (memory_database.py와 동일)
    base = Path(__file__).resolve().parent.parent
    db_path = base / "data" / "mellow_link_memory.db"
    if not db_path.exists():
        print("DB 파일이 없습니다. 초기화할 필요 없음.")
        return 0
    try:
        db_path.unlink()
        print(f"✅ verified: {db_path} 삭제됨. 다음 기동 시 새 DB가 생성됩니다.")
    except Exception as e:
        print(f"[Error] 삭제 실패: {e}")
        return 1
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="몰트북 관련 경험 로그 제거")
    ap.add_argument("--delete-entries", action="store_true", help="몰트북 도구 사용 레코드만 삭제")
    ap.add_argument("--reset-db", action="store_true", help="memory DB 파일 삭제(완전 초기화)")
    args = ap.parse_args()
    if args.reset_db:
        return reset_db()
    if args.delete_entries:
        return delete_moltbook_entries()
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
