from app.engines.rule_engine import RuleEngine


def test_rule_engine_matches_refund_and_inquiry_rules() -> None:
    engine = RuleEngine()
    sql_results = {
        "rows": [
            {
                "refund_rate": 0.081,
                "inquiry_growth": 0.17,
                "churn_rate": 0.05,
            }
        ]
    }

    rule_results, final_score, decision = engine.evaluate(sql_results)

    assert any(r["rule_id"] == "R001" and r["matched"] for r in rule_results)
    assert any(r["rule_id"] == "R002" and r["matched"] for r in rule_results)
    assert final_score == 50
    assert decision == "high_risk"
