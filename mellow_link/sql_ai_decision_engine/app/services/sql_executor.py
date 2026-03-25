from __future__ import annotations

import sqlite3
from typing import Protocol


class SQLExecutor(Protocol):
    def execute(self, template_name: str, sql: str, params: dict) -> dict:
        ...


class MockSQLExecutor:
    """Fallback executor for isolated tests."""

    def execute(self, template_name: str, sql: str, params: dict) -> dict:
        if template_name == "refund_analysis.sql":
            rows = [{"refund_rate": 0.081, "inquiry_growth": 0.17}]
        elif template_name == "churn_analysis.sql":
            rows = [{"churn_rate": 0.092, "refund_rate": 0.071}]
        else:
            rows = [{"inquiry_growth": 0.16, "inquiry_count": 120}]

        return {
            "template": template_name,
            "parameters": params,
            "sql": sql,
            "rows": rows,
            "executor": "mock",
        }


class RealSQLiteExecutor:
    """SQLite-backed executor for MVP sample DB validation."""

    def __init__(self, database_url: str) -> None:
        self.db_path = self._resolve_db_path(database_url)

    @staticmethod
    def _resolve_db_path(database_url: str) -> str:
        prefix = "sqlite:///"
        if database_url.startswith(prefix):
            return database_url[len(prefix) :]
        return database_url

    def execute(self, template_name: str, sql: str, params: dict) -> dict:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            cursor = conn.execute(sql, params)
            rows = [dict(row) for row in cursor.fetchall()]
        finally:
            conn.close()

        return {
            "template": template_name,
            "parameters": params,
            "sql": sql,
            "rows": rows,
            "executor": "sqlite",
            "database": self.db_path,
        }


def build_sql_executor(use_mock_sql: bool, database_url: str) -> SQLExecutor:
    if use_mock_sql:
        return MockSQLExecutor()
    return RealSQLiteExecutor(database_url=database_url)
