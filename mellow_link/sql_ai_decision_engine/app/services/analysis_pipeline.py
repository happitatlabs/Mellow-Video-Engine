from __future__ import annotations

from app.config.settings import settings
from app.engines.normalization_engine import NormalizationEngine
from app.engines.rule_engine import RuleEngine
from app.repositories.metric_repository import MetricRepository
from app.repositories.sql_template_repository import SQLTemplateRepository
from app.schemas.request import AnalyzeRequest
from app.services.ai_interpreter import interpret
from app.services.sql_executor import build_sql_executor


class AnalysisPipeline:
    def __init__(self) -> None:
        self.normalization_engine = NormalizationEngine()
        self.metric_repository = MetricRepository()
        self.sql_templates = SQLTemplateRepository()
        self.sql_executor = build_sql_executor(
            use_mock_sql=settings.use_mock_sql,
            database_url=settings.database_url,
        )
        self.rule_engine = RuleEngine()

    def run(self, request: AnalyzeRequest) -> dict:
        limitations = [
            "로그 입력 분석은 아직 미구현",
            "테이블 직접 업로드는 향후 지원 예정",
        ]

        normalized = self.normalization_engine.normalize(request)
        normalized.metrics = self.metric_repository.validate(normalized.metrics)

        template_name = self.sql_templates.get_template_name(normalized.target)
        template_sql = self.sql_templates.get_template_sql(template_name)
        segment = str(normalized.filters.get("segment", "all"))

        sql_results = self.sql_executor.execute(
            template_name=template_name,
            sql=template_sql,
            params={
                "start_date": normalized.period.start.isoformat(),
                "end_date": normalized.period.end.isoformat(),
                "segment": segment,
            },
        )

        rule_results, final_score, decision = self.rule_engine.evaluate(sql_results)
        ai_interpretation = interpret(sql_results, rule_results, decision)

        return {
            "status": "success",
            "input_type": request.input_type,
            "normalized_request": normalized,
            "sql_results": sql_results,
            "rule_results": rule_results,
            "final_score": final_score,
            "decision": decision,
            "ai_interpretation": ai_interpretation,
            "limitations": limitations,
        }
