from __future__ import annotations

from pathlib import Path


class SQLTemplateRepository:
    def __init__(self) -> None:
        self.template_dir = Path(__file__).resolve().parents[2] / "sql_templates"
        self.template_map = {
            "customer_refund": "refund_analysis.sql",
            "customer_churn": "churn_analysis.sql",
            "customer_inquiry": "inquiry_analysis.sql",
        }

    def get_template_name(self, target: str) -> str:
        return self.template_map.get(target, "refund_analysis.sql")

    def get_template_sql(self, template_name: str) -> str:
        template_path = self.template_dir / template_name
        if not template_path.exists():
            raise FileNotFoundError(f"SQL template not found: {template_path}")
        return template_path.read_text(encoding="utf-8")
