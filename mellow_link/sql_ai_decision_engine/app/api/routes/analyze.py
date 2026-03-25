from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.schemas.request import AnalyzeRequest
from app.schemas.response import AnalyzeResponse
from app.services.analysis_pipeline import AnalysisPipeline

router = APIRouter(tags=["analyze"])


@router.post("/analyze", response_model=AnalyzeResponse)
def analyze(request: AnalyzeRequest) -> AnalyzeResponse:
    if request.input_type in {"table", "log"}:
        raise HTTPException(status_code=501, detail="table/log input is planned for Phase 2")

    pipeline = AnalysisPipeline()
    result = pipeline.run(request)
    return AnalyzeResponse(**result)
