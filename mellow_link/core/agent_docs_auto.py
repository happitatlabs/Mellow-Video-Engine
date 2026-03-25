"""
Auto-invocation of read_docs_file under strict control.

Trigger: keywords in user query only.
Allowlist: predefined docs paths only.
Quota: max 3 per session.
Cooldown: 60 seconds between reads.
Cache: same file+hash returns cached.
Fail closed: uncertain → no auto-call.
"""
import json
import logging
import time
from typing import Optional, Dict, Any, Tuple

logger = logging.getLogger(__name__)

# ─── Constants ─────────────────────────────────────────────────────────────
TRIGGER_KEYWORDS = frozenset({
    "policy", "approval", "gate", "architecture", "system map",
    "design rule", "flow", "security boundary",
    "정책", "승인", "게이트", "아키텍처", "시스템맵",
    "설계 규칙", "플로우", "보안 경계",
})
# Block only when query clearly indicates code generation / implementation / file creation.
CODE_GENERATION_INTENT = frozenset({
    "implement", "generate code", "write code", "fix code", "create file",
    "코드 작성", "코드 구현", "파일 생성", "작성해줘",
})
ALLOWLIST = ("system_map.md", "MELLOW_LINK_Approval_Gate_Flow_Map.md")
MAX_QUOTA = 3
COOLDOWN_SEC = 60

# Session state: {session_id: {"count": int, "last_ts": float, "cache": {(path, hash): content}}}
_session_state: Dict[str, Dict[str, Any]] = {}
_state_lock = type("_Lock", (), {"__enter__": lambda s: None, "__exit__": lambda s, *a: None})()


def _get_state(session_id: str) -> Dict[str, Any]:
    if session_id not in _session_state:
        _session_state[session_id] = {
            "count": 0,
            "last_ts": 0.0,
            "cache": {},
        }
    return _session_state[session_id]


def clear_session(session_id: str) -> None:
    """Clear session state (call when run ends)."""
    if session_id in _session_state:
        del _session_state[session_id]


def _should_trigger(user_input: str) -> bool:
    """Trigger = (trigger_keyword) AND NOT (code_generation_intent)."""
    if not user_input or not isinstance(user_input, str):
        return False
    low = user_input.lower().strip()
    for c in CODE_GENERATION_INTENT:
        if c in low:
            return False
    for t in TRIGGER_KEYWORDS:
        if t in low:
            return True
    return False


def _pick_doc(user_input: str) -> Optional[str]:
    """Map query to allowlisted doc. No wildcards. Fail closed."""
    low = user_input.lower()
    if "approval" in low or "gate" in low or "승인" in low or "게이트" in low:
        return "MELLOW_LINK_Approval_Gate_Flow_Map.md"
    if "system map" in low or "architecture" in low or "아키텍처" in low or "시스템맵" in low or "policy" in low or "정책" in low or "flow" in low or "플로우" in low or "design" in low or "security" in low or "보안" in low:
        return "system_map.md"
    return None


def _push_metric(category: str, value: float = 1.0, session_id: Optional[str] = None) -> None:
    try:
        from mellow_link.core.metrics_collector import get_metrics_collector
        coll = get_metrics_collector()
        if coll:
            coll.push(category, value, "count", metric_id=session_id)
    except Exception as e:
        logger.debug("[agent_docs_auto] Metrics push failed: %s", e)


def try_auto_read_docs(session_id: str, user_input: str, mode: str = "fast") -> Optional[str]:
    """
    Auto-invoke read_docs_file if conditions met. Returns Observation string or None.
    Fail closed: any uncertainty → return None.
    
    Args:
        session_id: Session ID
        user_input: User query
        mode: Processing mode ("fast", "thinking", "research") - only allows docs injection in thinking/research
    """
    # Feature flag check
    try:
        from mellow_link.config import get_settings
        settings = get_settings()
        if not getattr(settings, "docs_auto_enabled", True):
            return None
    except Exception:
        pass  # Default to enabled if settings unavailable
    
    # Mode restriction: only allow in thinking/research modes
    if mode not in ("thinking", "research"):
        return None
    
    if not session_id or not user_input:
        return None
    if not _should_trigger(user_input):
        return None
    doc_path = _pick_doc(user_input)
    if not doc_path or doc_path not in ALLOWLIST:
        return None

    state = _get_state(session_id)
    # Quota
    if state["count"] >= MAX_QUOTA:
        _push_metric("DOCS_READ_BLOCKED_QUOTA", 1.0, session_id)
        logger.info("[agent_docs_auto] Quota exceeded (max=%d), skipping", MAX_QUOTA)
        return None
    # Cooldown
    now = time.monotonic()
    if state["last_ts"] > 0 and (now - state["last_ts"]) < COOLDOWN_SEC:
        _push_metric("DOCS_READ_BLOCKED_COOLDOWN", 1.0, session_id)
        logger.info("[agent_docs_auto] Cooldown active, skipping")
        return None

    # Cache check: read to get hash, then lookup
    try:
        from mellow_link.core.agent_tools_docs import read_docs_file
    except ImportError:
        return None
    raw = read_docs_file(doc_path)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if "error" in data:
        return None
    h = data.get("hash", "")
    source = data.get("source", "")
    if not h or not source:
        return None
    cache_key = (doc_path, h)
    if cache_key in state["cache"]:
        _push_metric("DOCS_READ_CACHE_HIT", 1.0, session_id)
        cached_content = state["cache"][cache_key]
        # Apply truncation to cached content if needed
        if len(cached_content) > 1000:
            truncated = cached_content[:1000] + "\n[TRUNCATED]"
            return truncated
        return cached_content

    content = data.get("content", "")
    if not content:
        return None
    
    # Hard cap: max 1000 chars for auto injection
    MAX_AUTO_CHARS = 1000
    if len(content) > MAX_AUTO_CHARS:
        content = content[:MAX_AUTO_CHARS] + "\n[TRUNCATED]"
    
    formatted = f"[DOC_REFERENCE: {source} | hash={h}]\n{content}"
    state["count"] += 1
    state["last_ts"] = now
    state["cache"][cache_key] = formatted
    _push_metric("DOCS_READ", 1.0, session_id)
    return formatted
