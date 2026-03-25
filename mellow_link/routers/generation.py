"""
Mellow-Link - Document Generation Router

Endpoints: /generate-document
"""

import logging

from fastapi import APIRouter, HTTPException, Depends

from mellow_link import app_state
from mellow_link.dependencies import get_current_user_optional
from mellow_link.services import DocumentRequest, DocumentType

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/generate-document", tags=["Document"])
async def generate_document(
    content: str,
    output_type: str = "docx",
    title: str = "Document",
    user=Depends(get_current_user_optional),
):
    """Generate a document (runs on CPU, does not affect GPU)."""
    if not app_state.doc_service or not app_state.doc_service.is_available():
        raise HTTPException(status_code=503, detail="Document Service unavailable")

    try:
        doc_type_map = {
            "pdf": DocumentType.PDF,
            "docx": DocumentType.DOCX,
            "html": DocumentType.HTML,
            "md": DocumentType.MARKDOWN,
        }

        doc_request = DocumentRequest(
            content=content,
            output_type=doc_type_map.get(output_type.lower(), DocumentType.DOCX),
            title=title
        )

        result = await app_state.doc_service.generate(doc_request)

        return {
            "success": True,
            "path": str(result.output_path),
            "type": result.output_type.value,
            "size_bytes": result.file_size_bytes,
            "duration_ms": result.generation_time_ms
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
