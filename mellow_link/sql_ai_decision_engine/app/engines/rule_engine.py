from __future__ import annotations

from pathlib import Path

import yaml


class RuleEngine:
    def __init__(self, rule_path: Path | None = None) -> None:
        self.rule_path = rule_path or (Path(__file__).resolve().parents[2] / "rules" / "rule_definitions.yaml")
        self.rule_definitions = yaml.safe_load(self.rule_path.read_text(encoding="utf-8"))

    def evaluate(self, sql_results: dict) -> tuple[list[dict], int, str]:
        row = (sql_results.get("rows") or [{}])[0]
        results: list[dict] = []
        total_score = 0

        for rule in self.rule_definitions.get("rules", []):
            metric_value = row.get(rule["metric"])
            matched = False
            if metric_value is not None:
                matched = self._compare(metric_value, rule["operator"], rule["threshold"])

            score = int(rule["score"]) if matched else 0
            total_score += score
            results.append(
                {
                    "rule_id": rule["id"],
                    "matched": matched,
                    "score": score,
                    "severity": rule["severity"],
                    "message": rule["message"],
                }
            )

        decision = self._decision(total_score)
        return results, total_score, decision

    @staticmethod
    def _decision(total_score: int) -> str:
        if total_score >= 50:
            return "high_risk"
        if total_score >= 30:
            return "warning"
        return "normal"

    @staticmethod
    def _compare(value: float, operator: str, threshold: float) -> bool:
        if operator == ">":
            return value > threshold
        if operator == ">=":
            return value >= threshold
        if operator == "<":
            return value < threshold
        if operator == "<=":
            return value <= threshold
        if operator == "==":
            return value == threshold
        raise ValueError(f"Unsupported operator: {operator}")
