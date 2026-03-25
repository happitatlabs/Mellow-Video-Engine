from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_analyze_natural_language() -> None:
    payload = {
        "input_type": "natural_language",
        "query": "최근 환불 증가 원인을 분석해줘",
        "metrics": ["refund_rate", "inquiry_growth"],
        "period": {"start": "2026-03-01", "end": "2026-03-31"},
    }

    response = client.post("/analyze", json=payload)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "success"
    assert body["input_type"] == "natural_language"
    assert "ai_interpretation" in body


def test_analyze_metric_input() -> None:
    payload = {
        "input_type": "metric",
        "metrics": ["churn_rate", "refund_rate"],
        "period": {"start": "2026-03-01", "end": "2026-03-31"},
    }

    response = client.post("/analyze", json=payload)

    assert response.status_code == 200
    assert response.json()["input_type"] == "metric"


def test_analyze_with_segment_passthrough() -> None:
    payload = {
        "input_type": "metric",
        "metrics": ["refund_rate", "inquiry_growth"],
        "segment": "premium",
        "period": {"start": "2026-03-01", "end": "2026-03-31"},
    }

    response = client.post("/analyze", json=payload)

    assert response.status_code == 200
    body = response.json()
    assert body["normalized_request"]["filters"]["segment"] == "premium"
    assert body["sql_results"]["parameters"]["segment"] == "premium"


def test_analyze_table_not_implemented() -> None:
    payload = {
        "input_type": "table",
        "table_name": "customer_metrics",
    }

    response = client.post("/analyze", json=payload)
    assert response.status_code == 501
