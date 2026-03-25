"""
Agent Tools - Docs: read-only document access from mellow_link/docs.

Strictly read-only. No write, mutation, dynamic execution, or templating.
"""
import hashlib
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Tuple

from mellow_link.core.tool_registry import tool

logger = logging.getLogger(__name__)

_DOCS_ROOT = Path(__file__).resolve().parents[1] / "docs"
_MAX_FILE_SIZE = 50 * 1024  # 50KB
_MAX_CONTENT_CHARS = 5_000
_FRONT_MATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def _resolve_docs_path(file_path: str) -> Tuple[Path, Optional[str]]:
    """
    Resolve relative path inside mellow_link/docs. Fail closed.
    Rejects: absolute paths, "..", any traversal outside docs root.
    """
    if not file_path or not isinstance(file_path, str):
        return Path(), "[Error] file_path required"
    raw = file_path.strip()
    if ".." in raw:
        return Path(), "[Error] Path traversal (..) not allowed"
    if Path(raw).is_absolute():
        return Path(), "[Error] Absolute paths not allowed"
    clean = raw.replace("\\", "/").lstrip("/")
    if clean.startswith("docs/"):
        clean = clean[5:]
    if not clean:
        return Path(), "[Error] Invalid path"
    resolved = (_DOCS_ROOT / clean).resolve()
    try:
        if not resolved.is_relative_to(_DOCS_ROOT.resolve()):
            return Path(), f"[Error] Path outside docs root: {file_path}"
    except ValueError:
        return Path(), f"[Error] Path outside docs root: {file_path}"
    return resolved, None


def _parse_front_matter_version(content: str) -> Optional[str]:
    """Extract version from YAML front-matter if present."""
    m = _FRONT_MATTER_RE.match(content)
    if not m:
        return None
    block = m.group(1)
    for line in block.splitlines():
        line = line.strip()
        if line.startswith("version:"):
            val = line[8:].strip().strip("'\"").strip()
            if val:
                return val
    return None


def _compute_sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


@tool(category="filesystem")
def read_docs_file(file_path: str) -> str:
    """
    Read document from mellow_link/docs (read-only). Returns structured metadata.
    Example: read_docs_file("system_map.md")
    Only relative paths inside docs/ allowed. Max 50KB file, 5000 chars returned.
    """
    resolved, err = _resolve_docs_path(file_path)
    if err:
        logger.critical("\033[91m[read_docs_file] PATH_GATE: %s\033[0m", err)
        return json.dumps({"error": err}, ensure_ascii=False)

    if not resolved.exists():
        return json.dumps({"error": f"File not found: {file_path}"}, ensure_ascii=False)
    if not resolved.is_file():
        return json.dumps({"error": f"Not a file: {file_path}"}, ensure_ascii=False)

    try:
        raw = resolved.read_bytes()
    except Exception as e:
        return json.dumps({"error": f"Read failed: {e}"}, ensure_ascii=False)

    if len(raw) > _MAX_FILE_SIZE:
        return json.dumps(
            {"error": f"File too large: {len(raw)} bytes (max {_MAX_FILE_SIZE})"},
            ensure_ascii=False,
        )

    try:
        content = raw.decode("utf-8")
    except UnicodeDecodeError:
        return json.dumps({"error": "File is not valid UTF-8"}, ensure_ascii=False)

    # Integrity: SHA-256 of full content
    file_hash = f"sha256:{_compute_sha256(raw)}"

    # Version: front-matter or mtime fallback
    version = _parse_front_matter_version(content)
    if not version:
        mtime = resolved.stat().st_mtime
        version = datetime.fromtimestamp(mtime, tz=timezone.utc).strftime("%Y-%m-%d")
    mtime_iso = datetime.fromtimestamp(resolved.stat().st_mtime, tz=timezone.utc).isoformat()

    # Content truncation
    if len(content) > _MAX_CONTENT_CHARS:
        content_out = content[:_MAX_CONTENT_CHARS] + f"\n...[truncated, total {len(content)} chars]"
    else:
        content_out = content

    rel_source = str(resolved.relative_to(_DOCS_ROOT)).replace("\\", "/")
    source = f"docs/{rel_source}"

    out = {
        "content": content_out,
        "source": source,
        "version": version,
        "hash": file_hash,
        "mtime": mtime_iso,
    }
    return json.dumps(out, ensure_ascii=False)
