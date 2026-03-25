from __future__ import annotations

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "decision_engine_sample.db"

CREATE_TABLES_SQL = """
CREATE TABLE customer_service_metrics (
    date TEXT NOT NULL,
    segment TEXT NOT NULL,
    refund_rate REAL NOT NULL,
    inquiry_growth REAL NOT NULL,
    PRIMARY KEY (date, segment)
);

CREATE TABLE customer_churn_metrics (
    date TEXT NOT NULL,
    segment TEXT NOT NULL,
    churn_rate REAL NOT NULL,
    refund_rate REAL NOT NULL,
    PRIMARY KEY (date, segment)
);

CREATE TABLE inquiry_metrics (
    date TEXT NOT NULL,
    segment TEXT NOT NULL,
    inquiry_growth REAL NOT NULL,
    inquiry_count INTEGER NOT NULL,
    PRIMARY KEY (date, segment)
);
"""

SERVICE_ROWS = [
    ("2026-03-05", "all", 0.050, 0.100),
    ("2026-03-15", "all", 0.060, 0.160),
    ("2026-03-28", "all", 0.082, 0.180),
    ("2026-03-05", "premium", 0.045, 0.090),
    ("2026-03-15", "premium", 0.055, 0.140),
    ("2026-03-28", "premium", 0.075, 0.170),
    ("2026-03-05", "general", 0.054, 0.110),
    ("2026-03-15", "general", 0.063, 0.158),
    ("2026-03-28", "general", 0.088, 0.190),
]

CHURN_ROWS = [
    ("2026-03-05", "all", 0.060, 0.050),
    ("2026-03-15", "all", 0.078, 0.060),
    ("2026-03-28", "all", 0.091, 0.082),
    ("2026-03-05", "premium", 0.055, 0.045),
    ("2026-03-15", "premium", 0.071, 0.055),
    ("2026-03-28", "premium", 0.084, 0.075),
    ("2026-03-05", "general", 0.064, 0.054),
    ("2026-03-15", "general", 0.081, 0.063),
    ("2026-03-28", "general", 0.096, 0.088),
]

INQUIRY_ROWS = [
    ("2026-03-05", "all", 0.100, 380),
    ("2026-03-15", "all", 0.160, 520),
    ("2026-03-28", "all", 0.180, 710),
    ("2026-03-05", "premium", 0.090, 130),
    ("2026-03-15", "premium", 0.140, 170),
    ("2026-03-28", "premium", 0.170, 250),
    ("2026-03-05", "general", 0.110, 250),
    ("2026-03-15", "general", 0.158, 350),
    ("2026-03-28", "general", 0.190, 460),
]


def recreate_db() -> None:
    if DB_PATH.exists():
        DB_PATH.unlink()

    conn = sqlite3.connect(DB_PATH)
    try:
        conn.executescript(CREATE_TABLES_SQL)
        conn.executemany(
            "INSERT INTO customer_service_metrics (date, segment, refund_rate, inquiry_growth) VALUES (?, ?, ?, ?)",
            SERVICE_ROWS,
        )
        conn.executemany(
            "INSERT INTO customer_churn_metrics (date, segment, churn_rate, refund_rate) VALUES (?, ?, ?, ?)",
            CHURN_ROWS,
        )
        conn.executemany(
            "INSERT INTO inquiry_metrics (date, segment, inquiry_growth, inquiry_count) VALUES (?, ?, ?, ?)",
            INQUIRY_ROWS,
        )
        conn.commit()
    finally:
        conn.close()


def print_verification() -> None:
    conn = sqlite3.connect(DB_PATH)
    try:
        queries = [
            (
                "normal_case",
                """
                SELECT refund_rate, inquiry_growth
                FROM customer_service_metrics
                WHERE date BETWEEN '2026-03-01' AND '2026-03-10'
                  AND segment = 'all'
                ORDER BY date DESC LIMIT 1
                """,
            ),
            (
                "warning_case",
                """
                SELECT refund_rate, inquiry_growth
                FROM customer_service_metrics
                WHERE date BETWEEN '2026-03-01' AND '2026-03-20'
                  AND segment = 'all'
                ORDER BY date DESC LIMIT 1
                """,
            ),
            (
                "high_risk_case",
                """
                SELECT churn_rate, refund_rate
                FROM customer_churn_metrics
                WHERE date BETWEEN '2026-03-01' AND '2026-03-31'
                  AND segment = 'all'
                ORDER BY date DESC LIMIT 1
                """,
            ),
        ]

        for name, sql in queries:
            row = conn.execute(sql).fetchone()
            print(name, dict(zip([c[0] for c in conn.execute(sql).description], row)) if row else None)
    finally:
        conn.close()


if __name__ == "__main__":
    recreate_db()
    print(f"Created sample DB: {DB_PATH}")
    print_verification()
