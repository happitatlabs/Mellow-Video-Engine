"""
Mellow-Link - Chat Router

Endpoints:
  - /chat/upload-temp, /chat/temp/{session_id}, /chat/temp/{session_id}/stats
  - /chat/sessions, /chat/sessions/uncategorized, /chat/sessions/{session_id}/messages
  - /chat/messages/{message_id}/feedback
  - /chat/sessions/{session_id} (DELETE)
  - /chat/ask  (Session-Aware, SSE streaming)
  - /chat      (Legacy, simple streaming)
"""

import asyncio
import json
import logging
import os
import re
import time
import traceback
import uuid
from pathlib import Path
from typing import Optional, Dict, Any, List

from fastapi import APIRouter, Depends, Header, HTTPException, Request, BackgroundTasks, UploadFile, File, Form
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import desc
from sqlalchemy.orm import Session

from mellow_link import app_state
from mellow_link.core import SystemState, TransitionResult
from mellow_link.infra import (
    get_db, User, UserRole, AgentFolder, ChatSession,
)
from mellow_link.services import get_vtuber_relay, get_rag_service

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Chat"])


# =============================================================================
# Request Models
# =============================================================================

class ChatRequest(BaseModel):
    """Chat request model."""
    question: str = Field(..., description="User message")
    system_prompt: str = Field("", description="System prompt")
    mode: str = Field("fast", description="Mode: fast (quick), thinking (deep), research (web+deep), auto")
    session_id: Optional[str] = Field(None, description="Session ID for context")
    folder_id: Optional[int] = Field(None, description="Folder ID for RAG")
    temp_session_id: Optional[str] = Field(None, description="Temp upload session key for TEMP_CONTEXT_STORE")
    stream: bool = Field(True, description="Enable streaming")


# =============================================================================
# Temporary Upload Endpoints
# =============================================================================

@router.post("/chat/upload-temp")
async def upload_temp_document(
    file: UploadFile = File(...),
    session_id: str = Form(...),
):
    """
    Upload a document for temporary/ephemeral chat context.

    - RAG(벡터DB)를 사용하지 않음
    - 텍스트를 추출하여 TEMP_CONTEXT_STORE(메모리)에 저장
    - 서버 재시작 시 소멸
    """
    from mellow_link.services.rag_service import extract_text_from_file

    if file is None or not file.filename:
        raise HTTPException(status_code=400, detail="No file uploaded")

    if not session_id or not session_id.strip():
        raise HTTPException(status_code=400, detail="session_id is required")

    try:
        content_bytes = await file.read()
        filename = file.filename

        if not content_bytes or len(content_bytes) == 0:
            raise HTTPException(status_code=400, detail="Empty file")

        logger.info(f"[TempUpload] Received: {filename} ({len(content_bytes)} bytes) for session {session_id}")

        extracted_text = extract_text_from_file(Path(filename), content_bytes)

        if not extracted_text or len(extracted_text.strip()) < 5:
            raise HTTPException(status_code=400, detail="텍스트를 추출할 수 없는 파일입니다.")

        if session_id in app_state.TEMP_CONTEXT_STORE:
            app_state.TEMP_CONTEXT_STORE[session_id] += f"\n\n--- [{filename}] ---\n{extracted_text}"
        else:
            app_state.TEMP_CONTEXT_STORE[session_id] = f"--- [{filename}] ---\n{extracted_text}"

        stored_length = len(app_state.TEMP_CONTEXT_STORE[session_id])
        logger.info(f"[TempUpload] Stored {len(extracted_text)} chars for session {session_id} (total: {stored_length} chars)")

        return {
            "success": True,
            "message": f"'{filename}' 업로드 완료. 텍스트 {len(extracted_text)}자 추출됨.",
            "session_id": session_id,
            "filename": filename,
            "extracted_chars": len(extracted_text),
            "total_chars": stored_length,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[TempUpload] Error: {e}")
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"Temp upload failed: {str(e)}")


@router.delete("/chat/temp/{session_id}")
async def clear_temp_session(session_id: str):
    """Clear temporary upload context for a session."""
    try:
        had_data = session_id in app_state.TEMP_CONTEXT_STORE
        chars_cleared = len(app_state.TEMP_CONTEXT_STORE.pop(session_id, ""))

        return {
            "success": True,
            "message": f"Cleared temp session {session_id}",
            "had_data": had_data,
            "chars_cleared": chars_cleared,
        }
    except Exception as e:
        logger.error(f"[TempUpload] Clear error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/chat/temp/{session_id}/stats")
async def get_temp_session_stats(session_id: str):
    """Get statistics about temporary upload context for a session."""
    text = app_state.TEMP_CONTEXT_STORE.get(session_id, "")
    return {
        "session_id": session_id,
        "has_data": bool(text),
        "total_chars": len(text),
    }


# =============================================================================
# Session Listing Endpoints
# =============================================================================

@router.get("/chat/sessions")
async def get_chat_sessions(
    folder_id: Optional[int] = None,
    authorization: str = Header(None),
    db: Session = Depends(get_db)
):
    """Get user's chat sessions. Optionally filter by folder_id."""
    from mellow_link.infra.database import ChatSession as _ChatSession

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

        if not username:
            return []

        user = db.query(User).filter(User.username == username).first()
        if not user:
            return []

        query = db.query(_ChatSession).filter(
            _ChatSession.user_id == user.id,
            _ChatSession.is_active == True
        )

        if folder_id is not None:
            query = query.filter(_ChatSession.folder_id == folder_id)

        sessions = query.order_by(_ChatSession.created_at.desc()).limit(50).all()

        return [
            {
                "id": s.id,
                "title": s.title,
                "folder_id": s.folder_id,
                "created_at": s.created_at.isoformat() if s.created_at else None
            }
            for s in sessions
        ]
    except Exception:
        return []


@router.get("/chat/sessions/uncategorized")
async def get_uncategorized_sessions(
    authorization: str = Header(None),
    db: Session = Depends(get_db)
):
    """Get user's uncategorized chat sessions (no folder)."""
    from mellow_link.infra.database import ChatSession as _ChatSession

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

        if not username:
            return []

        user = db.query(User).filter(User.username == username).first()
        if not user:
            return []

        sessions = db.query(_ChatSession).filter(
            _ChatSession.user_id == user.id,
            _ChatSession.folder_id == None,
            _ChatSession.is_active == True
        ).order_by(_ChatSession.created_at.desc()).limit(50).all()

        return [
            {
                "id": s.id,
                "title": s.title,
                "folder_id": None,
                "created_at": s.created_at.isoformat() if s.created_at else None
            }
            for s in sessions
        ]
    except Exception as e:
        logger.error(f"[Chat] Error getting uncategorized sessions: {e}")
        return []


@router.get("/chat/sessions/{session_id}/messages")
async def get_session_messages(
    session_id: int,
    authorization: str = Header(None),
    db: Session = Depends(get_db)
):
    """Get all messages for a specific chat session."""
    from mellow_link.infra.database import ChatSession as _ChatSession, ChatMessage, MessageFeedback

    if not authorization:
        raise HTTPException(status_code=401, detail="Authorization required")

    token = authorization.replace("Bearer ", "").strip()
    if token.startswith("guest_"):
        raise HTTPException(status_code=403, detail="Guests cannot access messages")

    try:
        from jose import jwt
        from mellow_link.infra.database import SECRET_KEY, ALGORITHM

        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username = payload.get("sub")

        if not username:
            raise HTTPException(status_code=401, detail="Invalid token")

        user = db.query(User).filter(User.username == username).first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        session = db.query(_ChatSession).filter(
            _ChatSession.id == session_id,
            _ChatSession.user_id == user.id
        ).first()

        if not session:
            raise HTTPException(status_code=404, detail="Session not found")

        messages = db.query(ChatMessage).filter(
            ChatMessage.session_id == session_id
        ).order_by(ChatMessage.timestamp.asc()).all()

        msg_ids = [msg.id for msg in messages]
        feedback_map: Dict[int, bool] = {}
        if msg_ids:
            feedbacks = (
                db.query(MessageFeedback)
                .filter(MessageFeedback.message_id.in_(msg_ids))
                .order_by(desc(MessageFeedback.created_at))
                .all()
            )
            for fb in feedbacks:
                if fb.message_id not in feedback_map:
                    feedback_map[fb.message_id] = fb.is_positive

        return [
            {
                "id": msg.id,
                "role": msg.role,
                "content": msg.content,
                "rag_used": msg.rag_used if msg.rag_used is not None else False,
                "auto_selected": msg.auto_selected if msg.auto_selected is not None else False,
                "selected_mode": msg.selected_mode,
                "processing_time": msg.processing_time,
                "feedback_positive": feedback_map.get(msg.id),
                "created_at": msg.timestamp.isoformat() if msg.timestamp else None,
                "state_info": msg.state_info,
                "evolution_payload": getattr(msg, "evolution_payload", None),
            }
            for msg in messages
        ]

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[Chat] Error getting session messages: {e}")
        logger.exception(f"[Chat] Full error traceback:")
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


# =============================================================================
# Feedback
# =============================================================================

@router.post("/chat/messages/{message_id}/feedback")
async def submit_message_feedback(
    message_id: int,
    request: Request,
    authorization: str = Header(None),
    db: Session = Depends(get_db)
):
    """Submit positive/negative feedback for a specific message."""
    from mellow_link.infra.database import ChatMessage, ChatSession as _ChatSession, MessageFeedback

    if not authorization:
        raise HTTPException(status_code=401, detail="Authorization required")

    token = authorization.replace("Bearer ", "").strip()
    if token.startswith("guest_"):
        raise HTTPException(status_code=403, detail="Guests cannot submit feedback")

    try:
        from jose import jwt
        from mellow_link.infra.database import SECRET_KEY, ALGORITHM

        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username = payload.get("sub")
        if not username:
            raise HTTPException(status_code=401, detail="Invalid token")

        user = db.query(User).filter(User.username == username).first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        msg = db.query(ChatMessage).filter(ChatMessage.id == message_id).first()
        if not msg:
            raise HTTPException(status_code=404, detail="Message not found")

        session = db.query(_ChatSession).filter(
            _ChatSession.id == msg.session_id,
            _ChatSession.user_id == user.id
        ).first()
        if not session:
            raise HTTPException(status_code=403, detail="Not your message")

        body = await request.json()
        is_positive = body.get("is_positive", body.get("positive"))
        if is_positive is None:
            raise HTTPException(status_code=400, detail="'is_positive' or 'positive' field required")

        db.query(MessageFeedback).filter(
            MessageFeedback.message_id == message_id
        ).delete()

        new_fb = MessageFeedback(
            message_id=message_id,
            is_positive=bool(is_positive),
            comment=body.get("comment"),
        )
        db.add(new_fb)
        db.commit()

        logger.info(f"[Feedback] Message {message_id} rated {'positive' if is_positive else 'negative'} by user {user.id}")
        return {"success": True, "message_id": message_id, "positive": bool(is_positive)}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[Feedback] Error submitting feedback: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# =============================================================================
# Session Delete
# =============================================================================

@router.delete("/chat/sessions/{session_id}")
async def delete_chat_session(
    session_id: int,
    authorization: str = Header(None),
    db: Session = Depends(get_db)
):
    """Delete a chat session (soft delete)."""
    from mellow_link.infra.database import ChatSession as _ChatSession

    if not authorization:
        raise HTTPException(status_code=401, detail="Authorization required")

    token = authorization.replace("Bearer ", "").strip()
    if token.startswith("guest_"):
        raise HTTPException(status_code=403, detail="Guests cannot delete sessions")

    try:
        from jose import jwt
        from mellow_link.infra.database import SECRET_KEY, ALGORITHM

        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username = payload.get("sub")

        if not username:
            raise HTTPException(status_code=401, detail="Invalid token")

        user = db.query(User).filter(User.username == username).first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        session = db.query(_ChatSession).filter(
            _ChatSession.id == session_id,
            _ChatSession.user_id == user.id
        ).first()

        if not session:
            raise HTTPException(status_code=404, detail="Session not found")

        session.is_active = False
        db.commit()

        logger.info(f"[Chat] Session {session_id} deleted by user {user.id}")
        return {"success": True, "deleted_id": session_id}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[Chat] Error deleting session: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# =============================================================================
# /chat/ask - Session-Aware Chat (SSE streaming, AgentBrain)
# =============================================================================

@router.post("/chat/ask")
async def chat_ask(
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db)
):
    """
    Session-aware chat endpoint.

    - Auto-creates session if session_id is not provided
    - Saves messages to database
    - Streams response via SSE
    - Returns session metadata on completion
    """
    from mellow_link.infra.database import ChatMessage, ChatSession as _ChatSession, AgentFolder as _AgentFolder

    body = await request.json()
    question = body.get("question", "").strip()
    session_id = body.get("session_id")
    folder_id = body.get("folder_id")
    temp_session_id = body.get("temp_session_id")
    mode = (body.get("mode") or "fast").strip().lower()
    if mode not in ("fast", "thinking", "research", "auto"):
        mode = "fast"
    airgap_mode_restriction_reason: Optional[str] = None
    # Optional: caller can send prompt_category for auto mode (e.g. benchmark). If "tool", selected_mode must never be "fast".
    request_prompt_category = body.get("prompt_category")
    if request_prompt_category is not None and request_prompt_category not in ("fast", "tool", "thinking", "research"):
        request_prompt_category = None
    skip_user_message = body.get("skip_user_message", False)
    plan_approved = body.get("plan_approved", False)

    logger.info(f"[/chat/ask] question={question[:50]}..., session_id={session_id}, folder_id={folder_id}, temp_session_id={temp_session_id}, plan_approved={plan_approved}")

    if not question:
        raise HTTPException(status_code=400, detail="Question is required")

    # Get user from authorization header
    auth_header = request.headers.get("Authorization", "")
    user = None
    user_id = None

    if auth_header.startswith("Bearer "):
        token = auth_header.replace("Bearer ", "").strip()
        if not token.startswith("guest_"):
            try:
                from jose import jwt
                from mellow_link.infra.database import SECRET_KEY, ALGORITHM
                payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
                username = payload.get("sub")
                if username:
                    user = db.query(User).filter(User.username == username).first()
                    user_id = user.id if user else None
            except Exception:
                pass

    # Get or create session
    session = None
    system_prompt = "You are a helpful AI assistant."

    if session_id:
        # [Security] IDOR 방지: 미인증 사용자는 session_id 사용 금지. 로그인 사용자는 user_id로만 세션 조회.
        if user_id is None:
            raise HTTPException(
                status_code=403,
                detail="세션을 사용하려면 로그인이 필요합니다. (무인증 session_id 접근 차단)",
            )
        session = db.query(_ChatSession).filter(
            _ChatSession.id == session_id,
            _ChatSession.user_id == user_id,
        ).first()

        # [CRITICAL FIX] Load conversation history from DB when reopening session
        if session:
            logger.info(f"[ChatAsk] Loading conversation history for session {session_id}")
            context_id_str = str(session_id)

            previous_messages = db.query(ChatMessage).filter(
                ChatMessage.session_id == session_id
            ).order_by(ChatMessage.timestamp.asc()).all()

            if previous_messages and app_state.llm_service:
                try:
                    context = app_state.llm_service._get_context(context_id_str)
                    context.messages.clear()

                    if session.folder_id:
                        folder = db.query(_AgentFolder).filter(_AgentFolder.id == session.folder_id).first()
                        if folder and folder.system_prompt:
                            context.system_prompt = folder.system_prompt

                    for msg in previous_messages:
                        context.add_message(msg.role, msg.content)

                    logger.info(f"[ChatAsk] Restored {len(previous_messages)} messages to LLM context")
                except Exception as context_err:
                    logger.warning(f"[ChatAsk] Failed to restore LLM context: {context_err}")

    if not session and user_id:
        if folder_id:
            folder = db.query(_AgentFolder).filter(_AgentFolder.id == folder_id).first()
            if folder:
                system_prompt = folder.system_prompt or system_prompt
                session = _ChatSession(
                    user_id=user_id,
                    folder_id=folder_id,
                    title=question[:50] + "..." if len(question) > 50 else question,
                    is_active=True
                )
        else:
            session = _ChatSession(
                user_id=user_id,
                folder_id=None,
                title=question[:50] + "..." if len(question) > 50 else question,
                is_active=True
            )

        if session:
            db.add(session)
            db.commit()
            db.refresh(session)
            session_id = session.id
            logger.info(f"[ChatAsk] Auto-created session {session_id} for user {user_id}")

    # ---------- Session Lock ----------
    if session_id:
        _session_lock_key = str(session_id)
    elif session:
        _session_lock_key = str(session.id)
    else:
        _session_lock_key = app_state.generate_stable_session_key(request)

    logger.info(f"[SESSION_BUSY] Lock key: {_session_lock_key}")

    async with app_state.SESSION_BUSY_LOCK:
        if _session_lock_key in app_state.SESSION_BUSY:
            client_ip = None
            forwarded_for = request.headers.get("X-Forwarded-For")
            if forwarded_for:
                client_ip = forwarded_for.split(",")[0].strip()
            elif request.client:
                client_ip = request.client.host
            else:
                client_ip = "unknown"

            user_agent = request.headers.get("User-Agent", "unknown")

            logger.warning(
                f"[SESSION_BUSY] 충돌 발생 - Lock key: {_session_lock_key}, "
                f"IP: {client_ip}, User-Agent: {user_agent[:50]}, "
                f"현재 사용 중인 세션: {list(app_state.SESSION_BUSY)}"
            )

            raise HTTPException(
                status_code=409,
                detail="이 세션에서 이미 요청을 처리 중입니다. 응답을 기다린 후 다시 시도해 주세요.",
            )
        app_state.SESSION_BUSY.add(_session_lock_key)

    try:
        logger.debug(f"[SESSION_BUSY] Lock acquired: {_session_lock_key}")
        # Get system prompt from folder
        if session and session.folder_id:
            folder = db.query(_AgentFolder).filter(_AgentFolder.id == session.folder_id).first()
            if folder and folder.system_prompt:
                system_prompt = folder.system_prompt

        # =====================================================================
        # Conditional Persona + Language Guardrail
        # =====================================================================
        relay = None
        is_vtuber_active = False
        if app_state.settings and app_state.settings.vtuber_relay_enabled == 1:
            relay = get_vtuber_relay()
            is_vtuber_active = relay and relay.is_connected

        selected_persona_content = ""

        if is_vtuber_active:
            persona_path = os.path.join("mellow_link", "prompts", "aventurine_persona_v1.txt")
            try:
                if os.path.exists(persona_path):
                    with open(persona_path, "r", encoding="utf-8") as f:
                        selected_persona_content = f.read().strip()
                else:
                    logger.warning(f"[Persona] File not found: {persona_path}")
                    selected_persona_content = "당신은 '어벤츄린'입니다. 반말을 사용하고, 능글맞은 도박사처럼 행동하세요."
            except Exception as e:
                logger.error(f"[Persona] Error loading persona: {e}")
        else:
            if session and session.folder_id:
                folder = db.query(_AgentFolder).filter(_AgentFolder.id == session.folder_id).first()
                if folder and folder.system_prompt:
                    selected_persona_content = folder.system_prompt

            if not selected_persona_content:
                selected_persona_content = "당신은 유능한 AI 조수입니다."

        mandatory_guardrail = (
            "IMPORTANT RULES:\n"
            "1. LANGUAGE: Korean (한글) ONLY. No English, No Chinese.\n"
            "2. NO HANJA: 한자(漢字) 및 중국어 표현을 절대 사용하지 마세요. (예: 確認 -> 확인)\n"
        )

        # =====================================================================
        # RAG Context Injection
        # =====================================================================
        rag_context_section = ""
        rag_used = False
        current_folder = None

        if session and session.folder_id:
            current_folder = db.query(_AgentFolder).filter(_AgentFolder.id == session.folder_id).first()

        if current_folder and current_folder.use_rag:
            logger.info(f"[RAG] Folder {current_folder.id} has RAG enabled")

            rag = get_rag_service()
            if rag and rag.is_available():
                try:
                    search_results = await rag.search(
                        query=question,
                        folder_id=current_folder.id,
                        top_k=3,
                        min_score=0.3,
                    )

                    if search_results:
                        rag_used = True
                        context_parts = []
                        for i, result in enumerate(search_results, 1):
                            context_parts.append(
                                f"[Source {i}: {result.filename}]\n{result.content}"
                            )
                            logger.info(f"[RAG] Found chunk from {result.filename} (score={result.score:.3f})")

                        rag_context_section = (
                            "\n\n=== 참고 문서 (영구 지식베이스) ===\n"
                            + "\n\n".join(context_parts)
                            + "\n=== END ===\n"
                        )
                    else:
                        logger.info(f"[RAG] No relevant documents found")

                except Exception as rag_err:
                    logger.error(f"[RAG] Search error: {rag_err}")
                    logger.error(traceback.format_exc())
            else:
                logger.warning(f"[RAG] RAG service not available")

        # =====================================================================
        # Temp Context Injection
        # =====================================================================
        temp_context_section = ""
        temp_session_key = str(temp_session_id) if temp_session_id else None
        logger.info(f"[TempContext] Lookup: key={temp_session_key}, store_keys={list(app_state.TEMP_CONTEXT_STORE.keys())}")

        if temp_session_key and temp_session_key in app_state.TEMP_CONTEXT_STORE:
            raw_temp = app_state.TEMP_CONTEXT_STORE[temp_session_key]
            temp_text = raw_temp[:3000]
            if len(raw_temp) > 3000:
                temp_text += "\n...(이하 생략됨)..."
            temp_context_section = (
                "\n\n=== 사용자가 방금 업로드한 파일 내용 ===\n"
                + temp_text
                + "\n=== END ===\n"
            )
            logger.info(f"[TempContext] Injected {len(temp_text)} chars")

        # =====================================================================
        # Final System Prompt Assembly
        # =====================================================================
        has_document_context = bool(rag_context_section or temp_context_section)

        if has_document_context:
            system_prompt = (
                f"{mandatory_guardrail}"
                f"{rag_context_section}"
                f"{temp_context_section}"
                "\nCRITICAL INSTRUCTION:\n"
                "1. 위 문서 내용에 기반하여 사실적으로 답변하세요.\n"
                "2. 문서에 없는 내용은 추측하거나 지어내지 마세요.\n"
                "3. 문서에서 찾을 수 없는 정보는 '문서에서 해당 정보를 찾을 수 없습니다'라고 답변하세요.\n"
                "4. 아래 페르소나는 '말투'로만 사용하고, 내용은 반드시 문서 기반으로 답변하세요.\n"
                f"\n[Character/Tone (말투만 참조)]\n{selected_persona_content}"
            )
        else:
            autopilot_memory_section = ""
            system_prompt = (
                f"{mandatory_guardrail}"
                f"{autopilot_memory_section}"
                f"\n\n[Character Context]\n{selected_persona_content}"
            )

        logger.info(f"[System] Persona Active: {'Aventurine (VTuber)' if is_vtuber_active else 'Default (Web)'}, RAG Used: {rag_used}, Temp Context: {bool(temp_context_section)}")

        # Save user message
        user_message_id = None
        if session and not skip_user_message:
            try:
                user_msg = ChatMessage(
                    session_id=session.id,
                    role="user",
                    content=question
                )
                db.add(user_msg)
                db.commit()
                db.refresh(user_msg)
                user_message_id = user_msg.id
                logger.info(f"[ChatAsk] Saved user message {user_message_id}")
            except Exception as db_err:
                logger.error(f"[ChatAsk] Failed to save user message: {db_err}")
                db.rollback()

        # Check LLM availability
        if not app_state.llm_service or not app_state.llm_service.is_available():
            raise HTTPException(status_code=503, detail="LLM Service unavailable")

        if app_state.orchestrator:
            await app_state.orchestrator.request_state_change(
                SystemState.TEXT,
                reason=f"Chat ask (mode: {mode})"
            )

        start_time = time.time()

        async def stream_generator():
            """SSE stream generator with session lock lifecycle."""
            full_response = ""
            try:
                if not app_state.orchestrator:
                    raise HTTPException(status_code=503, detail="Orchestrator not initialized")

                logger.info("[/chat/ask] Using AgentBrain (non-native streaming)")

                # Pre-compute values needed for run_agent (parallelizable)
                is_admin = bool(user and getattr(user, "role", None) == UserRole.ADMIN.value)
                effective_session_id = str(session_id or uuid.uuid4())

                # prompt_category for auto mode: use request body if provided, else detect from query
                prompt_category = request_prompt_category
                if mode == "auto" and prompt_category is None:
                    tool_keywords = [
                        '파일', '폴더', '경로', '읽어', '써', '저장', '삭제', '업로드', '문서', '인덱싱', '검색', 'rag',
                        'file', 'folder', 'path', 'read', 'write', 'save', 'delete', 'upload', 'document', 'index', 'search', 'rag',
                        '도구', 'tool', '실행', 'execute', '호출', 'call'
                    ]
                    question_lower = question.lower()
                    if any(keyword in question_lower for keyword in tool_keywords):
                        prompt_category = "tool"
                        logger.debug(f"[ChatAsk] Detected prompt_category=tool for query: {question[:50]}...")
                
                # Compute effective_mode exactly ONCE per request (fast, no I/O); pass prompt_category to run_agent for single source of truth
                if mode == "auto":
                    if app_state.orchestrator and hasattr(app_state.orchestrator, '_chat_pipeline'):
                        try:
                            effective_mode = app_state.orchestrator._chat_pipeline._select_mode_for_query(
                                question,
                                prompt_category=prompt_category
                            )
                            auto_selected_value = True
                        except Exception as e:
                            logger.warning(f"[ChatAsk] Mode selection failed, defaulting to fast: {e}")
                            effective_mode = "fast"
                            auto_selected_value = True
                    else:
                        effective_mode = "fast"
                        auto_selected_value = True
                else:
                    effective_mode = mode
                    auto_selected_value = False
                logger.debug(f"[ChatAsk] Computed effective_mode={effective_mode}, auto_selected={auto_selected_value} (request_mode={mode})")

                # Load history in parallel with orchestrator checks (if needed)
                # Note: History loading is fast DB query, but we can optimize by limiting to recent messages
                history_messages: List[Dict[str, str]] = []
                if session_id:
                    try:
                        from mellow_link.infra.database import ChatMessage as _ChatMessage
                        # Limit to last 20 messages for performance (most recent context)
                        previous_messages = db.query(_ChatMessage).filter(
                            _ChatMessage.session_id == session_id
                        ).order_by(_ChatMessage.timestamp.desc()).limit(20).all()
                        # Reverse to chronological order
                        previous_messages.reverse()
                        history_messages = [
                            {"role": m.role, "content": m.content}
                            for m in previous_messages
                            if getattr(m, "role", None) and getattr(m, "content", None)
                        ]
                    except Exception as hist_err:
                        logger.warning(f"[/chat/ask] Failed to load history: {hist_err}")

                context_messages = [{"role": "system", "content": system_prompt}] + history_messages

                agent_result = None
                agent_error_message = None
                try:
                    # Minimal validation (orchestrator should be initialized at startup)
                    if app_state.orchestrator is None:
                        raise RuntimeError("Orchestrator is None")
                    
                    # Run ID 생성 (Progress UI용)
                    from mellow_link.infra.run_events import create_run, emit_event
                    from mellow_link.routers.runs import RUN_CONTROL_STATE
                    run_id = create_run(session_id=effective_session_id, db=db, module_id="engine", run_kind="chat")
                    RUN_CONTROL_STATE[run_id] = {"paused": False, "abort_requested": False, "running": True}
                    logger.info(f"[ChatAsk] Created run_id: {run_id} for session {effective_session_id}")
                    
                    # Decision Layer: mode_decision 이벤트 발행 (Dev Console 가시화)
                    from mellow_link.core.output_sanitizer import detect_plan_intent, is_plan_only
                    detected_flags = []
                    if detect_plan_intent(question):
                        detected_flags.append("plan_intent")
                    if prompt_category == "tool":
                        detected_flags.append("tool_keyword")
                    plan_only = is_plan_only(question, detected_flags)
                    escalated = (mode == "fast" and effective_mode != "fast")
                    escalation_reason = None
                    if escalated and "plan_intent" in detected_flags:
                        escalation_reason = "plan_intent_detected"
                    elif escalated and "tool_keyword" in detected_flags:
                        escalation_reason = "tool_keyword_detected"
                    elif escalated:
                        escalation_reason = "auto_escalated"
                    try:
                        emit_event(
                            run_id=run_id,
                            event_type="mode_decision",
                            payload={
                                "initial_mode": mode,
                                "selected_mode": effective_mode,
                                "detected_flags": detected_flags,
                                "escalated": escalated,
                                "escalation_reason": escalation_reason,
                                "plan_only": plan_only,
                            },
                            db=db,
                        )
                    except Exception as emit_err:
                        logger.debug(f"[ChatAsk] mode_decision emit failed: {emit_err}")
                    
                    # session_state에 run_id + run_control + plan_only + plan_approved 추가
                    session_state = {
                        "run_id": run_id,
                        "run_control": RUN_CONTROL_STATE.get(run_id),
                        "plan_only": plan_only,
                        "plan_approved": plan_approved,
                    }
                    
                    # First SSE event: run_meta so main chat UI can subscribe to /runs/{run_id}/events immediately
                    run_meta = {
                        "run_id": run_id,
                        "session_id": effective_session_id,
                        "mode": effective_mode,
                        "plan_only": plan_only,
                        "ts": time.time(),
                    }
                    if airgap_mode_restriction_reason is not None:
                        run_meta["mode_restriction"] = airgap_mode_restriction_reason
                    yield f"event: run_meta\ndata: {json.dumps(run_meta, ensure_ascii=False)}\n\n"
                    logger.info(f"[CHAT_SSE] run_meta sent run_id={run_id} session_id={effective_session_id}")
                    
                    # Pass effective_mode and prompt_category so orchestrator can enforce tool=>thinking
                    agent_result = await app_state.orchestrator.run_agent(
                        question, history=context_messages, is_admin=is_admin, mode=effective_mode, session_id=effective_session_id,
                        prompt_category=prompt_category, session_state=session_state
                    )
                    RUN_CONTROL_STATE[run_id]["running"] = False
                except RuntimeError as runtime_err:
                    logger.error(f"[ChatAsk] RuntimeError: {runtime_err}", exc_info=True)
                    agent_result = None
                    agent_error_message = str(runtime_err)
                except Exception as agent_err:
                    error_type = type(agent_err).__name__
                    error_msg = str(agent_err) if str(agent_err) else repr(agent_err)
                    logger.error(f"[ChatAsk] {error_type}: {error_msg}", exc_info=True)
                    agent_result = None
                    agent_error_message = f"{error_type}: {error_msg}"
                    try:
                        if "run_id" in locals():
                            RUN_CONTROL_STATE[run_id]["running"] = False
                    except Exception:
                        pass

                # Response validation (effective_mode already computed above, reuse it)
                selected_mode_value = effective_mode
                logger.info(f"[ChatAsk] Set selected_mode_value={selected_mode_value}, auto_selected_value={auto_selected_value} (effective_mode={effective_mode})")
                
                if agent_result is None:
                    logger.error(f"[ChatAsk] agent_result is None. Error: {agent_error_message}")
                    if agent_error_message:
                        if "AgentBrain is not initialized" in agent_error_message:
                            full_response = "[오류] 에이전트가 초기화되지 않았습니다."
                        elif "GPU lock is not initialized" in agent_error_message:
                            full_response = "[오류] 시스템이 초기화되지 않았습니다."
                        elif "Orchestrator is None" in agent_error_message:
                            full_response = "[오류] 시스템 오케스트레이터가 초기화되지 않았습니다."
                        else:
                            full_response = f"[오류] 에이전트 실행 중 오류: {agent_error_message}"
                    else:
                        full_response = "[오류] 에이전트 실행 결과를 받지 못했습니다."
                elif not hasattr(agent_result, 'answer'):
                    logger.error(f"[ChatAsk] agent_result has no 'answer' attribute")
                    full_response = "[오류] 에이전트 응답 형식이 올바르지 않습니다."
                else:
                    try:
                        full_response = agent_result.answer if agent_result.answer else ""
                        if isinstance(full_response, str) and full_response.strip().startswith('{'):
                            logger.debug("[ChatAsk] Response appears to be JSON format, using as-is")
                        
                        # Use pre-computed effective_mode (no override needed)
                        # selected_mode_value and auto_selected_value are already set above
                            
                    except Exception as answer_err:
                        logger.error(f"[ChatAsk] Failed to get answer: {answer_err}", exc_info=True)
                        full_response = "[오류] 에이전트 응답을 읽는 중 오류가 발생했습니다."

                    if not full_response or len(full_response.strip()) == 0:
                        logger.warning("[ChatAsk] Empty response from agent")
                        full_response = "[응답 없음] 에이전트가 응답을 생성하지 못했습니다."
                
                # selected_mode_value is already set to effective_mode above

                if not isinstance(full_response, str):
                    logger.warning(f"[ChatAsk] full_response is not string: {type(full_response)}")
                    try:
                        full_response = str(full_response) if full_response is not None else ""
                    except Exception:
                        full_response = "[오류] 응답 변환 실패"

                # SSE chunked streaming
                chunk_size = 160
                if len(full_response) == 0:
                    try:
                        yield f"data: {json.dumps({'chunk': ''}, ensure_ascii=False)}\n\n"
                    except Exception:
                        pass

                for i in range(0, len(full_response), chunk_size):
                    try:
                        if hasattr(request, 'is_disconnected'):
                            disconnected = await request.is_disconnected()
                            if disconnected:
                                logger.warning(f"[SESSION_BUSY] Client disconnected: {_session_lock_key}")
                                break
                    except (RuntimeError, asyncio.CancelledError, Exception):
                        pass
                    chunk = full_response[i:i + chunk_size]
                    try:
                        chunk_json = json.dumps({'chunk': chunk}, ensure_ascii=False)
                        yield f"data: {chunk_json}\n\n"
                    except (TypeError, ValueError, UnicodeEncodeError) as json_err:
                        logger.warning(f"[ChatAsk] JSON encoding failed, sanitizing: {json_err}")
                        sanitized_chunk = ''.join(c for c in chunk if ord(c) < 0x10000)
                        try:
                            yield f"data: {json.dumps({'chunk': sanitized_chunk}, ensure_ascii=False)}\n\n"
                        except Exception:
                            yield f"data: {json.dumps({'chunk': ''})}\n\n"

                processing_time = time.time() - start_time

                # Save assistant message (evolution_report → patch_report conversion for UI)
                assistant_message_id = None
                if session:
                    try:
                        state_info_str = None
                        if plan_only and run_id:
                            try:
                                state_info_str = json.dumps({"run_id": run_id, "plan_only": True})
                            except Exception:
                                pass
                        content_to_save = full_response
                        evolution_payload_str = None
                        try:
                            raw = full_response.strip() if isinstance(full_response, str) else ""
                            if raw.startswith("{") and raw.endswith("}"):
                                parsed = json.loads(raw)
                                if isinstance(parsed, dict) and parsed.get("type") == "evolution_report":
                                    from mellow_link.utils.evolution_to_patch import evolution_report_to_patch_report
                                    patch = evolution_report_to_patch_report(parsed)
                                    content_to_save = json.dumps(patch, ensure_ascii=False)
                                    evolution_payload_str = json.dumps(parsed, ensure_ascii=False)
                        except Exception as conv_err:
                            logger.debug(f"[ChatAsk] evolution_report conversion skip: {conv_err}")
                        assistant_msg = ChatMessage(
                            session_id=session.id,
                            role="assistant",
                            content=content_to_save,
                            rag_used=rag_used,
                            selected_mode=selected_mode_value,
                            auto_selected=auto_selected_value,
                            processing_time=processing_time,
                            state_info=state_info_str,
                            evolution_payload=evolution_payload_str,
                        )
                        db.add(assistant_msg)
                        db.commit()
                        db.refresh(assistant_msg)
                        assistant_message_id = assistant_msg.id
                    except Exception as db_err:
                        logger.error(f"[ChatAsk] Failed to save assistant message: {db_err}")
                        db.rollback()

                # Completion metadata (항상 session_id 반환: 클라이언트가 다음 요청 body에 그대로 붙일 수 있도록)
                try:
                    out_session_id = str(session_id) if session_id is not None else effective_session_id
                    
                    # 성능 메트릭 추출
                    ttft_ms = None
                    ttft_measured = False
                    tps = None
                    tps_approx = None
                    infer_ms = None
                    
                    # agent_result에서 실제 LLM 추론 시간 사용 (AgentResult.total_infer_ms)
                    if agent_result and hasattr(agent_result, 'total_infer_ms') and agent_result.total_infer_ms:
                        infer_ms = agent_result.total_infer_ms
                        logger.debug(f"[ChatAsk] Using AgentResult.total_infer_ms: {infer_ms:.1f}ms")
                    elif processing_time:
                        # 폴백: processing_time 사용 (임시, 실제 추론 시간이 아닐 수 있음)
                        infer_ms = processing_time * 1000  # 초를 밀리초로 변환
                        logger.warning(f"[ChatAsk] Using processing_time as fallback for infer_ms: {infer_ms:.1f}ms (may not be accurate)")
                    
                    # 사용된 도구 목록 추출 (벤치마크 리포트용)
                    used_tools = []
                    if agent_result and hasattr(agent_result, 'steps') and agent_result.steps:
                        for step in agent_result.steps:
                            if step.action and step.action.tool:
                                tool_name = step.action.tool
                                if tool_name not in used_tools:
                                    used_tools.append(tool_name)
                    
                    metadata = {
                        'done': True,
                        'session_id': out_session_id,
                        'message_id': assistant_message_id,
                        'run_id': run_id,
                        'processing_time': processing_time,
                        'rag_used': rag_used,
                        'selected_mode': selected_mode_value,
                        'auto_selected': auto_selected_value,
                        'ttft_ms': ttft_ms,
                        'ttft_measured': ttft_measured,
                        'tps': tps,
                        'tps_approx': tps_approx,
                        'infer_ms': infer_ms,
                        'used_tools': used_tools if used_tools else None
                    }
                    if airgap_mode_restriction_reason is not None:
                        metadata['mode_restriction'] = airgap_mode_restriction_reason
                    if evolution_payload_str is not None:
                        metadata['content_display'] = content_to_save
                        metadata['evolution_payload'] = evolution_payload_str
                    logger.info(f"[ChatAsk] SSE done metadata: selected_mode={selected_mode_value}, auto_selected={auto_selected_value}, message_id={assistant_message_id}")
                    metadata_json = json.dumps(metadata, ensure_ascii=False)
                    yield f"data: {metadata_json}\n\n"
                except Exception as meta_err:
                    logger.warning(f"[ChatAsk] Failed to serialize done metadata: {meta_err}")
                    yield f"data: {json.dumps({'done': True})}\n\n"

                # Send to Avatar for TTS (only if VTuber Relay is enabled)
                relay = None
                if app_state.settings and app_state.settings.vtuber_relay_enabled == 1:
                    relay = get_vtuber_relay()
                if relay and full_response:
                    if not relay.is_connected:
                        logger.debug(f"[Avatar] VTuberRelay 연결 안 됨 (status={relay.status.value}), 메시지 전달 스킵")
                    elif relay.is_connected:
                        try:
                            cleaned_response = full_response
                            prefix_patterns = [
                                r'^(답변은|답변|응답은|응답|다음과 같이|다음과|AI|The answer is|Answer:|답변드리면|답변드리겠습니다|말씀드리면|말씀드리겠습니다)[:：\s]+',
                                r'^(안녕하세요|Hello|Hi)[,，\s]+',
                            ]
                            for pattern in prefix_patterns:
                                cleaned_response = re.sub(pattern, '', cleaned_response, flags=re.IGNORECASE | re.MULTILINE)

                            cleaned_response = re.sub(r'\[.*?\]', '', cleaned_response)
                            cleaned_response = re.sub(r'\s+', ' ', cleaned_response)
                            cleaned_response = cleaned_response.strip()
                            cleaned_response = re.sub(r'\*\*([^*]+)\*\*', r'\1', cleaned_response)
                            cleaned_response = re.sub(r'\*([^*]+)\*', r'\1', cleaned_response)
                            cleaned_response = re.sub(r'__([^_]+)__', r'\1', cleaned_response)
                            cleaned_response = re.sub(r'_([^_]+)_', r'\1', cleaned_response)
                            cleaned_response = cleaned_response.strip('.,;:!?-')
                            cleaned_response = re.sub(r'[^\w\s가-힣.,!?;:()\-\'"]+', '', cleaned_response, flags=re.UNICODE)

                            if not cleaned_response or len(cleaned_response.strip()) == 0:
                                cleaned_response = full_response

                            folder_name = None
                            if session and session.folder_id:
                                folder = db.query(_AgentFolder).filter(_AgentFolder.id == session.folder_id).first()
                                if folder:
                                    folder_name = folder.name

                            logger.info(f"[Avatar] Sending cleaned text: {cleaned_response[:100]}...")
                            success = await relay.relay_llm_response(
                                response_text=cleaned_response,
                                session_id=session_id,
                                folder_name=folder_name
                            )
                            if success:
                                logger.info(f"[Avatar] 전달 완료 (length={len(full_response)})")
                        except Exception as avatar_err:
                            logger.error(f"[Avatar] 아바타 전송 중 에러: {avatar_err}")

            except Exception as e:
                error_type = type(e).__name__
                error_message = str(e) if str(e) else repr(e)
                error_traceback = traceback.format_exc()

                if isinstance(e, json.JSONDecodeError):
                    doc_preview = repr(e.doc[:200] if e.doc else 'None')
                    logger.error(
                        f"[ChatAsk] JSONDecodeError:\n"
                        f"  Error: {error_message}\n"
                        f"  Doc snippet: {doc_preview}\n"
                        f"Traceback:\n{error_traceback}",
                        exc_info=True
                    )
                else:
                    logger.error(
                        f"[ChatAsk] Streaming error: {error_type}: {error_message}\n"
                        f"Traceback:\n{error_traceback}",
                        exc_info=True
                    )

                safe_error_message = f"{error_type}: {error_message}" if error_message else f"{error_type} occurred"
                try:
                    yield f"data: {json.dumps({'error': True, 'message': safe_error_message}, ensure_ascii=False)}\n\n"
                except Exception:
                    yield f"data: {json.dumps({'error': True, 'message': 'An error occurred'})}\n\n"
            finally:
                async with app_state.SESSION_BUSY_LOCK:
                    was_locked = _session_lock_key in app_state.SESSION_BUSY
                    app_state.SESSION_BUSY.discard(_session_lock_key)
                    if was_locked:
                        logger.debug(f"[SESSION_BUSY] Lock released (lifecycle): {_session_lock_key}")
                if app_state.orchestrator:
                    await app_state.orchestrator.request_state_change(
                        SystemState.IDLE, reason="Chat ask complete"
                    )

    except Exception as e:
        async with app_state.SESSION_BUSY_LOCK:
            was_locked = _session_lock_key in app_state.SESSION_BUSY
            app_state.SESSION_BUSY.discard(_session_lock_key)
            if was_locked:
                logger.warning(f"[SESSION_BUSY] Lock released after exception: {_session_lock_key}, error: {e}")
        raise

    try:
        response = StreamingResponse(
            stream_generator(),
            media_type="text/event-stream"
        )
        return response
    except Exception as e:
        async with app_state.SESSION_BUSY_LOCK:
            was_locked = _session_lock_key in app_state.SESSION_BUSY
            app_state.SESSION_BUSY.discard(_session_lock_key)
            if was_locked:
                logger.warning(f"[SESSION_BUSY] Lock released after StreamingResponse exception: {_session_lock_key}")
        raise


# =============================================================================
# /chat - Legacy Chat Endpoint
# =============================================================================

@router.post("/chat", tags=["LLM"])
async def chat(request: ChatRequest, http_request: Request):
    """
    Chat with the LLM (legacy endpoint).
    Supports both streaming and non-streaming responses.
    """
    if not app_state.llm_service or not app_state.llm_service.is_available():
        raise HTTPException(status_code=503, detail="LLM Service unavailable")
    if not app_state.orchestrator:
        raise HTTPException(status_code=503, detail="Orchestrator not initialized")

    effective_mode = (request.mode or "fast").strip().lower()
    if effective_mode not in ("fast", "thinking", "research", "auto"):
        effective_mode = "fast"
    legacy_airgap_mode_restriction_reason: Optional[str] = None

    # Session lock
    if request.session_id:
        _session_lock_key = str(request.session_id)
    else:
        _session_lock_key = app_state.generate_stable_session_key(http_request)

    logger.info(f"[SESSION_BUSY] /chat Lock key: {_session_lock_key}")

    async with app_state.SESSION_BUSY_LOCK:
        if _session_lock_key in app_state.SESSION_BUSY:
            client_ip = None
            forwarded_for = http_request.headers.get("X-Forwarded-For")
            if forwarded_for:
                client_ip = forwarded_for.split(",")[0].strip()
            elif http_request.client:
                client_ip = http_request.client.host
            else:
                client_ip = "unknown"

            logger.warning(f"[SESSION_BUSY] /chat 충돌 - Lock key: {_session_lock_key}, IP: {client_ip}")

            raise HTTPException(
                status_code=409,
                detail="이 세션에서 이미 요청을 처리 중입니다.",
            )
        app_state.SESSION_BUSY.add(_session_lock_key)

    logger.info(f"[/chat] question={request.question[:50]}..., temp_session_id={request.temp_session_id}")

    # Temp Context Injection
    effective_system_prompt = request.system_prompt
    if request.temp_session_id:
        temp_text = app_state.TEMP_CONTEXT_STORE.get(request.temp_session_id, "")
        logger.info(f"[/chat] TEMP_CONTEXT_STORE lookup: key={request.temp_session_id}, found={bool(temp_text)}")
        if temp_text:
            sliced = temp_text[:3000]
            if len(temp_text) > 3000:
                sliced += "\n...(이하 생략됨)..."
            effective_system_prompt = (
                f"{request.system_prompt}\n\n"
                f"=== 사용자가 업로드한 파일 내용 ===\n{sliced}\n=== END ===\n"
                "위 문서 내용에 기반하여 사실적으로 답변하세요."
            )

    # Request state transition
    result = await app_state.orchestrator.request_state_change(
        SystemState.TEXT,
        reason=f"Chat request (mode: {request.mode})"
    )

    if result == TransitionResult.INVALID_TRANSITION:
        async with app_state.SESSION_BUSY_LOCK:
            app_state.SESSION_BUSY.discard(_session_lock_key)
        raise HTTPException(
            status_code=409,
            detail=f"Cannot chat: system in {app_state.orchestrator.get_state().name} state"
        )

    try:
        if request.stream:
            async def stream_generator():
                """SSE stream generator for legacy /chat."""
                try:
                    logger.info("[/chat] Using AgentBrain (non-native streaming)")
                    if legacy_airgap_mode_restriction_reason is not None:
                        yield f"event: mode_restriction\ndata: {json.dumps({'mode_restriction': legacy_airgap_mode_restriction_reason}, ensure_ascii=False)}\n\n"

                    context_messages = []
                    if effective_system_prompt:
                        context_messages.append({"role": "system", "content": effective_system_prompt})

                    effective_session_id = str(request.session_id or uuid.uuid4())
                    # 폐쇄망 시 research는 이미 effective_mode로 thinking으로 치환됨
                    agent_result = await app_state.orchestrator.run_agent(
                        request.question, history=context_messages, is_admin=False, mode=effective_mode,
                        session_id=effective_session_id,
                    )
                    answer = agent_result.answer or ""

                    answer = answer.replace("\r\n", "\n").replace("\n", " ")
                    chunk_size = 200
                    for i in range(0, len(answer), chunk_size):
                        try:
                            if hasattr(http_request, 'is_disconnected'):
                                disconnected = await http_request.is_disconnected()
                                if disconnected:
                                    logger.warning(f"[SESSION_BUSY] /chat Client disconnected: {_session_lock_key}")
                                    break
                        except (RuntimeError, asyncio.CancelledError, Exception):
                            pass
                        yield f"data: {answer[i:i + chunk_size]}\n\n"
                    yield "data: [DONE]\n\n"
                finally:
                    async with app_state.SESSION_BUSY_LOCK:
                        app_state.SESSION_BUSY.discard(_session_lock_key)
                    await app_state.orchestrator.request_state_change(
                        SystemState.IDLE, reason="Chat stream complete"
                    )

            try:
                return StreamingResponse(stream_generator(), media_type="text/event-stream")
            except Exception as e:
                async with app_state.SESSION_BUSY_LOCK:
                    app_state.SESSION_BUSY.discard(_session_lock_key)
                raise
        else:
            # Non-streaming
            t0 = time.time()
            context_messages = []
            if effective_system_prompt:
                context_messages.append({"role": "system", "content": effective_system_prompt})

            effective_session_id = str(request.session_id or uuid.uuid4())
            agent_result = await app_state.orchestrator.run_agent(
                request.question, history=context_messages, is_admin=False, mode=effective_mode,
                session_id=effective_session_id,
            )
            duration_ms = (time.time() - t0) * 1000

            out = {
                "response": agent_result.answer,
                "model": app_state.llm_service.get_model_for_mode(effective_mode) if app_state.llm_service else "unknown",
                "mode": effective_mode,
                "tokens": 0,
                "duration_ms": duration_ms
            }
            if legacy_airgap_mode_restriction_reason is not None:
                out["mode_restriction"] = legacy_airgap_mode_restriction_reason
            return out
    except Exception as e:
        async with app_state.SESSION_BUSY_LOCK:
            app_state.SESSION_BUSY.discard(_session_lock_key)
        raise
    finally:
        if not request.stream:
            async with app_state.SESSION_BUSY_LOCK:
                app_state.SESSION_BUSY.discard(_session_lock_key)
            await app_state.orchestrator.request_state_change(
                SystemState.IDLE, reason="Chat complete"
            )
