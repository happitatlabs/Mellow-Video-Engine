"""
Mellow-Link - Centralized Application State

All global service instances and shared state live here.
Router modules import from this module instead of using module-level globals.
"""

import asyncio
import hashlib
import logging
from typing import Optional, Any

from cachetools import TTLCache

logger = logging.getLogger(__name__)

# =============================================================================
# Service Instances (set during startup, read by routers)
# =============================================================================

settings = None           # Optional[Settings]
orchestrator = None       # Optional[Orchestrator]
vram_watchdog = None      # Optional[VRAMWatchdog]
llm_service = None        # Optional[LLMService]
image_service = None      # Optional[ImageService]
video_service = None      # Optional[VideoService]
doc_service = None        # Optional[DocumentService]
vtuber_proc = None        # Optional[subprocess.Popen]  VTuber 백엔드 프로세스
rag_service = None        # Optional[RAGService]
autonomous_agent_task = None  # Optional[asyncio.Task]

# =============================================================================
# Shared State (used by multiple routers)
# =============================================================================

# Temp Upload Context Store (RAG 비사용 - 순수 텍스트 메모리)
# Key: session_id (str), Value: extracted text (str)
# TTL 1시간, 최대 1000개 항목 — 감리 권장(P2) 적용
TEMP_CONTEXT_STORE: TTLCache = TTLCache(maxsize=1000, ttl=3600)

# In-Progress Lock: 동일 세션 중복 요청 방지 (세션 키 -> 사용 중)
SESSION_BUSY: set = set()
SESSION_BUSY_LOCK: asyncio.Lock = asyncio.Lock()

shutdown_event: asyncio.Event = asyncio.Event()

# Static/Output directory paths (set during app creation)
static_dir: Optional[str] = None
outputs_dir: Optional[str] = None


# =============================================================================
# Utility Functions (shared across routers)
# =============================================================================

def generate_stable_session_key(request) -> str:
    """
    [SESSION_LOCK_KEY_REFACTOR] 익명 세션의 안정적인 락 키 생성.

    Volatile ID(id(request)) 대신 IP + User-Agent 해시를 사용하여
    동일 클라이언트의 요청을 안정적으로 식별.

    Args:
        request: FastAPI Request 객체

    Returns:
        안정적인 세션 키 문자열 (예: "anon_ip_abc123def456")
    """
    # 1. IP 주소 추출 (Proxy 대응: X-Forwarded-For 우선)
    client_ip = None
    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for:
        client_ip = forwarded_for.split(",")[0].strip()
        logger.debug(f"[SessionKey] Using X-Forwarded-For IP: {client_ip}")
    else:
        if request.client:
            client_ip = request.client.host
        else:
            client_ip = "unknown"
        logger.debug(f"[SessionKey] Using direct client IP: {client_ip}")

    # 2. User-Agent 추출
    user_agent = request.headers.get("User-Agent", "unknown")

    # 3. IP + User-Agent를 조합하여 해시 생성
    combined = f"{client_ip}:{user_agent}"
    hash_obj = hashlib.sha256(combined.encode('utf-8'))
    hash_hex = hash_obj.hexdigest()[:16]

    session_key = f"anon_ip_{hash_hex}"
    logger.debug(f"[SessionKey] Generated stable key: {session_key} (IP: {client_ip[:20]}..., UA: {user_agent[:30]}...)")

    return session_key
