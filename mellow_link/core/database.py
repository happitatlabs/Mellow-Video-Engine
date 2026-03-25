"""
Evolution Ledger Database - 자가 학습용 진화 원장

SQLite 기반 evolution_history 테이블을 별도 DB 파일로 관리하여
기존 RAG DB, memory_database, aventurine_v3.db와 충돌하지 않도록 함.
"""

import sqlite3
import logging
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Tuple, Dict

logger = logging.getLogger(__name__)

# 기존 DB와 분리된 전용 파일
_BASE = Path(__file__).resolve().parent.parent
_DATA_DIR = _BASE / "data"
_DATA_DIR.mkdir(parents=True, exist_ok=True)
_EVOLUTION_LEDGER_PATH = _DATA_DIR / "evolution_ledger.db"


def _get_connection() -> sqlite3.Connection:
    """진화 원장 전용 SQLite 연결 (별도 파일)."""
    conn = sqlite3.connect(str(_EVOLUTION_LEDGER_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


@contextmanager
def _ledger_connection():
    """진화 원장 DB 연결 컨텍스트."""
    conn = _get_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _ensure_schema(conn: sqlite3.Connection) -> None:
    """evolution_history 테이블 생성 및 리소스 추적 칼럼 마이그레이션."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS evolution_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            proposal_id TEXT NOT NULL,
            target_file TEXT NOT NULL,
            user_request TEXT,
            verdict_code TEXT,
            audit_critique TEXT,
            status TEXT NOT NULL CHECK (status IN ('SUCCESS', 'FAIL', 'REJECTED')),
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            token_usage INTEGER,
            cost REAL,
            latency REAL
        )
    """)
    for col, typ in (("token_usage", "INTEGER"), ("cost", "REAL"), ("latency", "REAL")):
        try:
            conn.execute(f"ALTER TABLE evolution_history ADD COLUMN {col} {typ}")
        except sqlite3.OperationalError:
            pass
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_evolution_history_target 
        ON evolution_history(target_file)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_evolution_history_created 
        ON evolution_history(created_at DESC)
    """)
    conn.commit()


def save_evolution_record(
    proposal_id: str,
    target_file: str,
    user_request: str,
    verdict_code: str,
    audit_critique: str,
    status: str,  # SUCCESS | FAIL | REJECTED
    token_usage: Optional[int] = None,
    cost: Optional[float] = None,
    latency: Optional[float] = None,
) -> bool:
    """
    진화 원장에 기록 저장.
    pre_flight_check 실패, Audit 거부, 성공 모두 기록.
    token_usage, cost, latency는 가성비 분석용.
    """
    if status not in ("SUCCESS", "FAIL", "REJECTED"):
        logger.warning("[EvolutionLedger] Invalid status=%s, defaulting to FAIL", status)
        status = "FAIL"
    target_file = (target_file or "").strip() or "(unknown)"
    try:
        with _ledger_connection() as conn:
            _ensure_schema(conn)
            conn.execute(
                """
                INSERT INTO evolution_history 
                (proposal_id, target_file, user_request, verdict_code, audit_critique, status,
                 token_usage, cost, latency)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    proposal_id[:64],
                    target_file[:512],
                    (user_request or "")[:2000],
                    (verdict_code or "")[:50000],
                    (audit_critique or "")[:8000],
                    status,
                    token_usage,
                    cost,
                    latency,
                ),
            )
        logger.debug("[EvolutionLedger] Saved record proposal_id=%s status=%s", proposal_id[:8], status)
        return True
    except Exception as e:
        logger.warning("[EvolutionLedger] Save failed: %s", e)
        return False


def get_evolution_history_for_proposals(proposal_ids: List[str]) -> Dict[str, dict]:
    """
    proposal_id별 최신 evolution_history 레코드 조회.
    Monitor Flow API에서 Guardian critique, status 연동용.
    Returns:
        {proposal_id: {"audit_critique": str, "status": str, "created_at": str}}
    """
    if not proposal_ids:
        return {}
    try:
        with _ledger_connection() as conn:
            _ensure_schema(conn)
            placeholders = ",".join("?" * len(proposal_ids))
            cursor = conn.execute(
                """
                SELECT proposal_id, audit_critique, status, created_at
                FROM evolution_history
                WHERE proposal_id IN ({})
                ORDER BY created_at DESC
                """.format(placeholders),
                tuple(proposal_ids),
            )
            rows = cursor.fetchall()
        result: Dict[str, dict] = {}
        for row in rows:
            pid = row["proposal_id"]
            if pid not in result:
                result[pid] = {
                    "audit_critique": (row["audit_critique"] or "").strip(),
                    "status": row["status"] or "REJECTED",
                    "created_at": row["created_at"] or "",
                }
        return result
    except Exception as e:
        logger.warning("[EvolutionLedger] get_evolution_history_for_proposals failed: %s", e)
        return {}


def fetch_past_failure_context(target_file: Optional[str] = None, limit: int = 3) -> str:
    """
    특정 파일(또는 전체)의 과거 실패 사례와 감사 피드백을 최대 limit건 요약.
    Tower 프롬프트 주입용.
    """
    try:
        with _ledger_connection() as conn:
            _ensure_schema(conn)
            if target_file and target_file.strip():
                cursor = conn.execute(
                    """
                    SELECT target_file, audit_critique, status, created_at
                    FROM evolution_history
                    WHERE status IN ('FAIL', 'REJECTED')
                      AND (target_file = ? OR target_file LIKE ?)
                    ORDER BY created_at DESC
                    LIMIT ?
                    """,
                    (target_file.strip(), f"%{target_file.strip()}%", limit),
                )
            else:
                cursor = conn.execute(
                    """
                    SELECT target_file, audit_critique, status, created_at
                    FROM evolution_history
                    WHERE status IN ('FAIL', 'REJECTED')
                    ORDER BY created_at DESC
                    LIMIT ?
                    """,
                    (limit,),
                )
            rows = cursor.fetchall()
        if not rows:
            return ""
        lines = ["## [중요] 과거 실패 사례 - 이번 제안에서 반드시 회피하라"]
        for i, row in enumerate(rows, 1):
            tgt = row["target_file"] or "(unknown)"
            critique = (row["audit_critique"] or "").strip()[:600]
            st = row["status"] or "REJECTED"
            lines.append(f"\n### 사례 {i} (대상: {tgt}, {st})")
            lines.append(f"지적 내용: {critique}")
        return "\n".join(lines)
    except Exception as e:
        logger.warning("[EvolutionLedger] get_past_failure_context failed: %s", e)
        return ""


def analyze_cost_efficiency(
    target_file: Optional[str] = None,
    limit: int = 100,
) -> Tuple[float, float, List[dict]]:
    """
    성공 1건당 평균 소모 비용 및 가성비가 낮은 패턴 분석.
    Returns:
        (avg_cost_per_success, worst_ratio, worst_patterns)
        - avg_cost_per_success: 성공 건당 평균 비용 (USD)
        - worst_ratio: 실패 대비 비용이 높은 패턴의 비율
        - worst_patterns: [{"target_file": ..., "fail_count": ..., "total_cost": ..., "success_count": ...}, ...]
    """
    try:
        with _ledger_connection() as conn:
            _ensure_schema(conn)
            # 성공 건당 평균 비용
            if target_file and target_file.strip():
                cur = conn.execute(
                    """
                    SELECT COALESCE(AVG(cost), 0) as avg_cost
                    FROM evolution_history
                    WHERE status = 'SUCCESS' AND cost IS NOT NULL AND cost > 0
                      AND (target_file = ? OR target_file LIKE ?)
                    """,
                    (target_file.strip(), f"%{target_file.strip()}%"),
                )
            else:
                cur = conn.execute(
                    """
                    SELECT COALESCE(AVG(cost), 0) as avg_cost
                    FROM evolution_history
                    WHERE status = 'SUCCESS' AND cost IS NOT NULL AND cost > 0
                    """,
                )
            row = cur.fetchone()
            avg_cost_per_success = float(row["avg_cost"] or 0)

            # 가성비 낮은 패턴: 실패는 많고 비용은 많이 든 target_file
            cur = conn.execute(
                """
                SELECT target_file,
                       SUM(CASE WHEN status IN ('FAIL','REJECTED') THEN 1 ELSE 0 END) as fail_count,
                       SUM(CASE WHEN status = 'SUCCESS' THEN 1 ELSE 0 END) as success_count,
                       COALESCE(SUM(cost), 0) as total_cost
                FROM evolution_history
                WHERE target_file IS NOT NULL AND target_file != ''
                GROUP BY target_file
                HAVING fail_count > 0 AND total_cost > 0
                ORDER BY total_cost DESC, fail_count DESC
                LIMIT ?
                """,
                (limit,),
            )
            rows = cur.fetchall()
            worst_patterns = [
                {
                    "target_file": r["target_file"],
                    "fail_count": r["fail_count"],
                    "success_count": r["success_count"],
                    "total_cost": float(r["total_cost"] or 0),
                }
                for r in rows
            ]
            worst_ratio = 0.0
            if worst_patterns:
                p = worst_patterns[0]
                total = p["fail_count"] + p["success_count"]
                if total > 0 and p["total_cost"] > 0:
                    worst_ratio = p["total_cost"] / max(1, p["success_count"]) if p["success_count"] else p["total_cost"]
        return avg_cost_per_success, worst_ratio, worst_patterns
    except Exception as e:
        logger.warning("[EvolutionLedger] analyze_cost_efficiency failed: %s", e)
        return 0.0, 0.0, []


def get_cost_efficiency_briefing(
    cost: float,
    target_file: Optional[str] = None,
) -> str:
    """
    결재 보고서용 가성비 브리핑 문자열 생성.
    '이번 작업으로 약 $X의 비용이 소모되었으며, 이는 과거 평균 대비 N% 효율적입니다'
    """
    if cost <= 0:
        return ""
    try:
        avg_cost, _, _ = analyze_cost_efficiency(target_file=target_file, limit=50)
        if avg_cost <= 0:
            return f"이번 작업으로 약 ${cost:.4f}의 비용이 소모되었습니다."
        ratio = (avg_cost - cost) / avg_cost if avg_cost else 0
        if ratio > 0:
            pct = int(ratio * 100)
            return f"이번 작업으로 약 ${cost:.4f}의 비용이 소모되었으며, 이는 과거 평균 대비 {pct}% 효율적입니다."
        if ratio < 0:
            pct = int(-ratio * 100)
            return f"이번 작업으로 약 ${cost:.4f}의 비용이 소모되었으며, 이는 과거 평균 대비 {pct}% 더 소모되었습니다."
        return f"이번 작업으로 약 ${cost:.4f}의 비용이 소모되었습니다."
    except Exception as e:
        logger.warning("[EvolutionLedger] get_cost_efficiency_briefing failed: %s", e)
        return f"이번 작업으로 약 ${cost:.4f}의 비용이 소모되었습니다."


def get_daily_evolution_stats() -> Tuple[float, int]:
    """
    금일 evolution_history 기준 일일 누적 비용(USD)과 사이클 수.
    Returns:
        (daily_cost_usd, daily_cycle_count)
    """
    try:
        with _ledger_connection() as conn:
            _ensure_schema(conn)
            today = datetime.now().strftime("%Y-%m-%d")
            cur = conn.execute(
                """
                SELECT COALESCE(SUM(cost), 0) as total_cost, COUNT(*) as cnt
                FROM evolution_history
                WHERE date(created_at) = date(?)
                """,
                (today,),
            )
            row = cur.fetchone()
        cost = float(row["total_cost"] or 0) if row else 0.0
        cnt = int(row["cnt"] or 0) if row else 0
        return cost, cnt
    except Exception as e:
        logger.warning("[EvolutionLedger] get_daily_evolution_stats failed: %s", e)
        return 0.0, 0


def predict_low_roi(target_file: Optional[str] = None) -> Tuple[bool, str]:
    """
    현재 요청이 '성공 확률 낮고 비용만 높을 것'으로 예상되는지 판단.
    Returns:
        (is_low_roi, reason_message)
    """
    try:
        _, _, worst_patterns = analyze_cost_efficiency(target_file=target_file, limit=10)
        if not worst_patterns:
            return False, ""
        # 해당 target_file이 실패 많고 비용 높은 패턴에 포함되는지
        tgt_norm = (target_file or "").strip().lower()
        for p in worst_patterns:
            pf = (p.get("target_file") or "").lower()
            if tgt_norm and (tgt_norm in pf or pf in tgt_norm):
                fail_count = p.get("fail_count", 0)
                success_count = p.get("success_count", 0)
                total_cost = p.get("total_cost", 0)
                if fail_count >= 2 and success_count == 0 and total_cost > 0.01:
                    return True, (
                        f"이 판은 판돈(토큰) 대비 수익률이 낮으니, "
                        f"방식을 바꾸거나 보류하는 게 어떨까요? "
                        f"({p['target_file']} 대상 과거 {fail_count}회 실패, 비용 ${total_cost:.3f})"
                    )
        return False, ""
    except Exception as e:
        logger.warning("[EvolutionLedger] predict_low_roi failed: %s", e)
        return False, ""
