from app.engines.normalization_engine import NormalizationEngine
from app.schemas.request import AnalyzeRequest


def test_normalization_engine_for_natural_language() -> None:
    engine = NormalizationEngine()
    request = AnalyzeRequest(
        input_type="natural_language",
        query="최근 환불과 문의 증가 원인을 분석해줘",
        period={"start": "2026-03-01", "end": "2026-03-31"},
    )

    normalized = engine.normalize(request)

    assert normalized.analysis_type == "root_cause"
    assert "refund_rate" in normalized.metrics
    assert "inquiry_growth" in normalized.metrics
    assert "natural_language" in normalized.source_types
