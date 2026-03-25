"""
Mellow-Link - Folders & Sessions Router

Endpoints: /folders (CRUD), /folders/{id}/sessions, /folders/{id}/documents, /folders/{id}/upload
"""

import logging
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request, UploadFile, File, Form
from sqlalchemy.orm import Session

from mellow_link import app_state
from mellow_link.infra import (
    get_db, User, UserRole, AgentFolder, ChatSession,
    ensure_user_has_folders,
)
from mellow_link.services import get_rag_service

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Folders"])


# =============================================================================
# Endpoints
# =============================================================================

@router.get("/folders")
async def get_folders(
    authorization: str = Header(None),
    db: Session = Depends(get_db)
):
    """
    Get user's folders.

    - Auto-creates default folders if none exist (404 protection)
    - Admin users get Secretary folder at top
    - Returns session_count for each folder
    - Returns empty list for guests
    """
    if not authorization:
        return []

    token = authorization.replace("Bearer ", "").strip()

    if token.startswith("guest_"):
        return []

    try:
        from jose import jwt, JWTError
        from mellow_link.infra.database import SECRET_KEY, ALGORITHM

        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username = payload.get("sub")
        role = payload.get("role", UserRole.USER.value)

        if not username:
            return []

        user = db.query(User).filter(User.username == username).first()
        if not user:
            return []

        folders = ensure_user_has_folders(db, user.id, role=user.role)

        result = []
        for f in folders:
            session_count = db.query(ChatSession).filter(
                ChatSession.folder_id == f.id,
                ChatSession.is_active == True
            ).count()

            result.append({
                "id": f.id,
                "name": f.name,
                "icon": f.icon,
                "system_prompt": f.system_prompt,
                "use_rag": f.use_rag,
                "is_creative": f.is_creative,
                "session_count": session_count
            })

        return result
    except Exception as e:
        logger.error(f"[Folders] Error loading folders: {e}")
        return []


@router.post("/folders")
async def create_folder(
    request: Request,
    authorization: str = Header(None),
    db: Session = Depends(get_db)
):
    """Create a new folder for the user."""
    if not authorization:
        raise HTTPException(status_code=401, detail="Authorization required")

    token = authorization.replace("Bearer ", "").strip()
    if token.startswith("guest_"):
        raise HTTPException(status_code=403, detail="Guests cannot create folders")

    try:
        from jose import jwt
        from mellow_link.infra.database import SECRET_KEY, ALGORITHM

        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username = payload.get("sub")

        user = db.query(User).filter(User.username == username).first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        body = await request.json()
        folder = AgentFolder(
            user_id=user.id,
            name=body.get("name", "New Folder"),
            icon=body.get("icon", "📁"),
            system_prompt=body.get("system_prompt", ""),
            use_rag=body.get("use_rag", False),
            is_creative=body.get("is_creative", False),
            rag_collection_name=body.get("rag_collection_name") or f"user_{user.id}_{body.get('name', 'folder').lower().replace(' ', '_')}"
        )
        db.add(folder)
        db.commit()
        db.refresh(folder)

        logger.info(f"[Folders] Created folder '{folder.name}' for user {user.id}")
        return {"id": folder.id, "name": folder.name, "icon": folder.icon}

    except Exception as e:
        logger.error(f"[Folders] Create error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/folders/{folder_id}")
async def update_folder(
    folder_id: int,
    request: Request,
    authorization: str = Header(None),
    db: Session = Depends(get_db)
):
    """Update a folder's settings."""
    if not authorization:
        raise HTTPException(status_code=401, detail="Authorization required")

    token = authorization.replace("Bearer ", "").strip()
    if token.startswith("guest_"):
        raise HTTPException(status_code=403, detail="Guests cannot edit folders")

    try:
        from jose import jwt
        from mellow_link.infra.database import SECRET_KEY, ALGORITHM

        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username = payload.get("sub")

        user = db.query(User).filter(User.username == username).first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        folder = db.query(AgentFolder).filter(
            AgentFolder.id == folder_id,
            AgentFolder.user_id == user.id,
            AgentFolder.is_active == True
        ).first()

        if not folder:
            raise HTTPException(status_code=404, detail="Folder not found")

        body = await request.json()

        if "name" in body:
            folder.name = body["name"]
        if "icon" in body:
            folder.icon = body["icon"]
        if "system_prompt" in body:
            folder.system_prompt = body["system_prompt"]
        if "use_rag" in body:
            folder.use_rag = body["use_rag"]
        if "is_creative" in body:
            folder.is_creative = body["is_creative"]

        db.commit()
        db.refresh(folder)

        logger.info(f"[Folders] Updated folder {folder.id}: {folder.name}")
        return {"id": folder.id, "name": folder.name, "icon": folder.icon}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[Folders] Update error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/folders/{folder_id}")
async def delete_folder(
    folder_id: int,
    authorization: str = Header(None),
    db: Session = Depends(get_db)
):
    """Delete a folder (soft delete - marks as inactive)."""
    if not authorization:
        raise HTTPException(status_code=401, detail="Authorization required")

    token = authorization.replace("Bearer ", "").strip()
    if token.startswith("guest_"):
        raise HTTPException(status_code=403, detail="Guests cannot delete folders")

    try:
        from jose import jwt
        from mellow_link.infra.database import SECRET_KEY, ALGORITHM

        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username = payload.get("sub")

        user = db.query(User).filter(User.username == username).first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        folder = db.query(AgentFolder).filter(
            AgentFolder.id == folder_id,
            AgentFolder.user_id == user.id,
            AgentFolder.is_active == True
        ).first()

        if not folder:
            raise HTTPException(status_code=404, detail="Folder not found")

        folder.is_active = False
        db.commit()

        logger.info(f"[Folders] Deleted folder {folder_id} by user {user.id}")
        return {"success": True, "deleted_id": folder_id}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[Folders] Delete error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/folders/{folder_id}/sessions")
async def get_folder_sessions(
    folder_id: int,
    authorization: str = Header(None),
    db: Session = Depends(get_db)
):
    """Get sessions for a specific folder."""
    if not authorization:
        raise HTTPException(status_code=401, detail="Authorization required")

    token = authorization.replace("Bearer ", "").strip()
    if token.startswith("guest_"):
        return []

    try:
        from jose import jwt
        from mellow_link.infra.database import SECRET_KEY, ALGORITHM

        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username = payload.get("sub")

        user = db.query(User).filter(User.username == username).first()
        if not user:
            return []

        sessions = db.query(ChatSession).filter(
            ChatSession.folder_id == folder_id,
            ChatSession.user_id == user.id,
            ChatSession.is_active == True
        ).order_by(ChatSession.created_at.desc()).all()

        return [
            {
                "id": s.id,
                "title": s.title,
                "created_at": s.created_at.isoformat() if s.created_at else None,
                "updated_at": s.created_at.isoformat() if s.created_at else None  # ChatSession has no updated_at; use created_at
            }
            for s in sessions
        ]
    except Exception as e:
        logger.error(f"[Folders] Error loading sessions: {e}")
        return []


@router.get("/folders/{folder_id}/documents")
async def get_folder_documents(
    folder_id: int,
    authorization: str = Header(None),
    db: Session = Depends(get_db)
):
    """Get documents uploaded to a folder's RAG collection."""
    from mellow_link.infra import FolderDocument

    if not authorization:
        raise HTTPException(status_code=401, detail="Authorization required")

    try:
        from jose import jwt
        from mellow_link.infra.database import SECRET_KEY, ALGORITHM

        token = authorization.replace("Bearer ", "").strip()
        if token.startswith("guest_"):
            raise HTTPException(status_code=403, detail="Guests cannot list folder documents")

        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username = payload.get("sub")
        if not username:
            raise HTTPException(status_code=401, detail="Invalid token")

        user = db.query(User).filter(User.username == username).first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        folder = db.query(AgentFolder).filter(
            AgentFolder.id == folder_id,
            AgentFolder.user_id == user.id,
        ).first()
        if not folder:
            raise HTTPException(status_code=404, detail="Folder not found")

        docs = db.query(FolderDocument).filter(
            FolderDocument.folder_id == folder_id
        ).order_by(FolderDocument.uploaded_at.desc()).all()

        return [
            {
                "id": d.id,
                "filename": d.filename,
                "file_size": d.file_size,
                "status": d.status,
                "uploaded_at": d.uploaded_at.isoformat() if d.uploaded_at else None
            }
            for d in docs
        ]
    except Exception as e:
        logger.error(f"[Folders] Error loading documents: {e}")
        return []


@router.post("/folders/{folder_id}/upload")
async def upload_folder_document(
    folder_id: int,
    file: UploadFile = File(...),
    authorization: str = Header(None),
    db: Session = Depends(get_db)
):
    """
    Upload a document to a folder's RAG collection (permanent knowledge base).
    Supports: PDF, DOCX, TXT, MD, HTML
    """
    from mellow_link.infra import FolderDocument

    if not authorization:
        raise HTTPException(status_code=401, detail="Authorization required")

    token = authorization.replace("Bearer ", "").strip()
    if token.startswith("guest_"):
        raise HTTPException(status_code=403, detail="Guests cannot upload documents")

    try:
        from jose import jwt
        from mellow_link.infra.database import SECRET_KEY, ALGORITHM

        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username = payload.get("sub")

        user = db.query(User).filter(User.username == username).first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        folder = db.query(AgentFolder).filter(
            AgentFolder.id == folder_id,
            AgentFolder.user_id == user.id,
        ).first()

        if not folder:
            raise HTTPException(status_code=404, detail="Folder not found")

        if not folder.use_rag:
            raise HTTPException(status_code=400, detail="This folder does not have RAG enabled")

        # Read file content
        content_bytes = await file.read()
        filename = file.filename or "unknown"

        if not content_bytes:
            raise HTTPException(status_code=400, detail="Empty file")

        # Create document record
        doc = FolderDocument(
            folder_id=folder_id,
            filename=filename,
            file_path="",
            file_size=len(content_bytes),
            status="processing"
        )
        db.add(doc)
        db.commit()
        db.refresh(doc)

        logger.info(f"[RAG Upload] Document {doc.id}: {filename} ({len(content_bytes)} bytes) for folder {folder_id}")

        # Process document in background
        import asyncio

        def process_document_background_sync():
            """동기 래퍼: 새 이벤트 루프에서 비동기 처리 실행."""
            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(_process())
            finally:
                loop.close()

        async def _process():
            from mellow_link.infra import get_db as _get_db
            _db = next(_get_db())
            try:
                rag = get_rag_service()
                if rag and rag.is_available():
                    success = await rag.ingest_document(
                        folder_id=folder_id,
                        document_id=doc.id,
                        filename=filename,
                        content_bytes=content_bytes,
                    )
                    _doc = _db.query(FolderDocument).filter(FolderDocument.id == doc.id).first()
                    if _doc:
                        _doc.status = "ready" if success else "error"
                        _db.commit()
                        logger.info(f"[RAG Upload] Document {doc.id} processing {'succeeded' if success else 'failed'}")
                else:
                    _doc = _db.query(FolderDocument).filter(FolderDocument.id == doc.id).first()
                    if _doc:
                        _doc.status = "error"
                        _db.commit()
                    logger.warning(f"[RAG Upload] RAG service not available for document {doc.id}")
            except Exception as e:
                logger.error(f"[RAG Upload] Processing error for document {doc.id}: {e}")
                try:
                    _doc = _db.query(FolderDocument).filter(FolderDocument.id == doc.id).first()
                    if _doc:
                        _doc.status = "error"
                        _db.commit()
                except Exception:
                    pass
            finally:
                _db.close()

        import threading
        thread = threading.Thread(target=process_document_background_sync, daemon=True)
        thread.start()

        return {
            "success": True,
            "document_id": doc.id,
            "filename": filename,
            "status": "processing",
            "message": f"'{filename}' 업로드 완료. RAG 인덱싱 진행 중..."
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[RAG Upload] Upload error: {e}")
        import traceback
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"Upload failed: {str(e)}")


@router.delete("/folders/{folder_id}/documents/{doc_id}")
async def delete_folder_document(
    folder_id: int,
    doc_id: int,
    authorization: str = Header(None),
    db: Session = Depends(get_db)
):
    """Delete a document from a folder's RAG collection."""
    from mellow_link.infra import FolderDocument, DocumentChunk

    if not authorization:
        raise HTTPException(status_code=401, detail="Authorization required")

    try:
        from jose import jwt
        from mellow_link.infra.database import SECRET_KEY, ALGORITHM

        token = authorization.replace("Bearer ", "").strip()
        if token.startswith("guest_"):
            raise HTTPException(status_code=403, detail="Guests cannot delete documents")

        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username = payload.get("sub")
        if not username:
            raise HTTPException(status_code=401, detail="Invalid token")

        user = db.query(User).filter(User.username == username).first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        folder = db.query(AgentFolder).filter(
            AgentFolder.id == folder_id,
            AgentFolder.user_id == user.id,
        ).first()
        if not folder:
            raise HTTPException(status_code=404, detail="Folder not found")

        doc = db.query(FolderDocument).filter(
            FolderDocument.id == doc_id,
            FolderDocument.folder_id == folder_id
        ).first()

        if doc:
            deleted_chunks = db.query(DocumentChunk).filter(
                DocumentChunk.document_id == doc_id
            ).delete()
            logger.info(f"[RAG Delete] Deleted {deleted_chunks} chunks from database")

            db.delete(doc)
            db.commit()

            rag = get_rag_service()
            if rag:
                rag.clear_document_from_cache(folder_id, doc_id)

            logger.info(f"[RAG Delete] Deleted document {doc_id} from folder {folder_id}")
            return {"success": True, "message": "Document deleted"}
        else:
            return {"success": False, "message": "Document not found"}

    except Exception as e:
        logger.error(f"[RAG Delete] Delete error: {e}")
        return {"success": False, "message": str(e)}
