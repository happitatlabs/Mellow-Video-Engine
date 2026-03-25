"""
Agent Tools - 파일 시스템 도구: read, write, list, report, cleanup, delete, move, mkdir.

모든 파일 접근은 SecurityManager/PathManager를 통해 sandbox 내부만 허용된다.
"""
import json
import logging
import shutil
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional, Dict, Any, List, Tuple

from mellow_link.core.tool_registry import tool
from mellow_link.core.security_manager import SecurityBlocked
from mellow_link.core.workspace_sandbox import get_workspace_root
from mellow_link.utils.report_masking import (
    mask_report_content,
    is_too_sensitive,
)
from mellow_link.core.agent_tools_base import (
    _get_security,
    _pm,
    _normalize_workspace_path,
    _normalize_read_path,
    _ensure_path_inside_workspace,
    _ensure_path_inside_sandbox_for_read,
    truncate_list,
    format_truncation_footer,
)

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════
# 1. 파일 시스템 (sandbox 내부만)
# ═══════════════════════════════════════════════

@tool(category="filesystem")
def read_file(file_path: str) -> str:
    """
    sandbox(mellow_link/) 내부의 텍스트 파일을 읽습니다.
    상대 경로를 입력하면 mellow_link/ 기준으로 해석됩니다.
    예: workspace 스크립트 읽기 → read_file("workspace/list_files.py")
    sandbox 외부(Open-LLM-VTuber, launcher.py 등) 접근은 차단됩니다.
    
    [cite: 2026-02-09] 경로 정규화: 상대 경로는 mellow_link 루트 기준으로 해석됩니다.
    ".", "workspace" 등은 기존 호환을 위해 workspace 루트로 해석됩니다.
    """
    # 읽기 전용 정규화: 상대 경로를 mellow_link(sandbox) 기준으로 변환
    normalized_path = _normalize_read_path(file_path)
    
    try:
        # 정규화된 경로로 보안 검증 수행
        safe_path = _get_security().resolve_for_read(normalized_path)
    except SecurityBlocked as e:
        return str(e)
    except PermissionError as e:
        return f"[차단] 접근 불가: {e}"

    path_err = _ensure_path_inside_sandbox_for_read(safe_path, file_path)
    if path_err:
        logger.critical(
            "\033[91m[PATH_GATE_BLOCKED] read_file path outside sandbox: '%s' (normalized: '%s')\033[0m",
            file_path,
            normalized_path,
        )
        return path_err

    if not safe_path.exists():
        return f"[Error] 파일이 존재하지 않습니다: {file_path}"
    if not safe_path.is_file():
        return f"[Error] 파일이 아닙니다: {file_path}"

    content = safe_path.read_text(encoding="utf-8")
    if len(content) > 3000:
        return content[:3000] + f"\n...(총 {len(content)}자 중 3000자까지 표시)"
    return content


def _get_docs_root() -> Path:
    """mellow_link/docs/ 절대 경로."""
    return Path(__file__).resolve().parents[1] / "docs"


@tool(category="filesystem")
def list_docs() -> str:
    """
    mellow_link/docs/ 내 사용 가능한 문서 목록을 반환합니다.
    읽기 전용. 어떤 문서를 read_docs_file로 읽을 수 있는지 확인용.
    """
    docs_root = _get_docs_root()
    if not docs_root.exists() or not docs_root.is_dir():
        return "[Error] docs 디렉토리를 찾을 수 없습니다."
    lines: List[str] = []
    for p in sorted(docs_root.rglob("*"), key=lambda x: (x.is_file(), str(x))):
        if p.is_file() and p.suffix.lower() in (".md", ".txt", ".json"):
            rel = p.relative_to(docs_root)
            lines.append(str(rel).replace("\\", "/"))
    if not lines:
        return "(docs 내 문서 없음)"
    return "docs/\n  " + "\n  ".join(lines)


@tool(category="filesystem")
def write_file(file_path: str, content: str, overwrite_confirmed: bool = False) -> str:
    """
    sandbox(mellow_link/) 내부에 텍스트 파일을 저장합니다.
    예: workspace에 새 스크립트 → write_file("workspace/새도구.py", "...")
    하위 디렉토리가 없으면 자동으로 생성됩니다.

    [cite: 2026-02-09] 경로 정규화: 상대 경로는 mellow_link 루트 기준으로 해석됩니다.
    ".", "workspace" 등은 기존 호환을 위해 workspace 루트로 해석됩니다.

    주의: 위험한 파일 패턴(.env, .gitignore 등)은 workspace 내부에서도 생성할 수 없습니다.

    Args:
        file_path: 저장할 파일 경로 (workspace 내부)
        content: 파일에 쓸 내용
        overwrite_confirmed: 기존 파일 덮어쓰기 확인 여부 (기본값: False)
            - False: 파일이 존재하면 확인 요청 메시지 반환
            - True: 기존 파일을 .backup/에 백업 후 덮어쓰기
    """
    # [cite: 2026-02-09] 경로 정규화: 상대 경로를 workspace 기준으로 변환
    normalized_path = _normalize_workspace_path(file_path)

    # 위험한 파일 패턴 확인 (workspace 내부라도 차단)
    is_dangerous, reason = _is_dangerous_file(file_path)
    if is_dangerous:
        return f"[차단] 위험한 파일 패턴: {reason}\n이 파일은 보안상 생성할 수 없습니다."

    try:
        safe_path = _get_security().resolve_for_write(normalized_path, content=content)
    except SecurityBlocked as e:
        if getattr(e, "proposal_path", None):
            try:
                rel = _pm().get_relative(e.proposal_path)
                return f"{e}\n[제안서 생성됨] {rel}"
            except Exception:
                return f"{e}\n[제안서 생성됨] {e.proposal_path}"
        return str(e)
    except PermissionError as e:
        return f"[차단] 접근 불가: {e}"

    path_err = _ensure_path_inside_workspace(safe_path, file_path)
    if path_err:
        logger.critical("\033[91m[PATH_GATE_BLOCKED] write_file path outside workspace: '%s'\033[0m", file_path)
        return path_err

    # [OVERWRITE_PROTECTION] 기존 파일 존재 확인 및 백업
    workspace_root = get_workspace_root()
    if safe_path.exists() and safe_path.is_file():
        if not overwrite_confirmed:
            # 기존 파일 정보 제공하고 확인 요청
            try:
                existing_size = safe_path.stat().st_size
                existing_content = safe_path.read_text(encoding="utf-8")
                preview = existing_content[:300] if len(existing_content) > 300 else existing_content
                preview_suffix = "..." if len(existing_content) > 300 else ""
            except Exception as e:
                existing_size = 0
                preview = f"(읽기 실패: {e})"
                preview_suffix = ""

            return (
                f"[확인 필요] 파일이 이미 존재합니다: {file_path}\n"
                f"- 크기: {existing_size} bytes\n"
                f"- 기존 내용 미리보기:\n{preview}{preview_suffix}\n\n"
                f"덮어쓰려면 overwrite_confirmed=True로 다시 호출하세요.\n"
                f'{{"tool":"write_file","args":{{"file_path":"{file_path}","content":"...","overwrite_confirmed":true}}}}'
            )

        # 백업 생성 (.backup/ 폴더에 타임스탬프와 함께 저장)
        backup_dir = workspace_root / ".backup"
        backup_dir.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        # 원본 경로 구조 유지
        try:
            rel_to_workspace = safe_path.relative_to(workspace_root)
            rel_dir = rel_to_workspace.parent
            if str(rel_dir) != ".":
                backup_subdir = backup_dir / rel_dir
                backup_subdir.mkdir(parents=True, exist_ok=True)
                backup_path = backup_subdir / f"{safe_path.stem}_{timestamp}{safe_path.suffix}"
            else:
                backup_path = backup_dir / f"{safe_path.stem}_{timestamp}{safe_path.suffix}"
        except ValueError:
            backup_path = backup_dir / f"{safe_path.stem}_{timestamp}{safe_path.suffix}"

        try:
            shutil.copy2(str(safe_path), str(backup_path))
            logger.info("[write_file] 백업 생성: %s", backup_path.relative_to(workspace_root))
        except Exception as e:
            logger.warning("[write_file] 백업 생성 실패 (계속 진행): %s", e)

    safe_path.parent.mkdir(parents=True, exist_ok=True)
    safe_path.write_text(content, encoding="utf-8")
    try:
        return f"[완료] 저장됨: {_pm().get_relative(safe_path)}"
    except Exception:
        return f"[완료] 저장됨: {safe_path}"


@tool(category="filesystem")
def list_directory(dir_path: str = ".", recursive: bool = False, max_depth: int = 3) -> str:
    """
    [UPGRADE_LIST_DIRECTORY_RECURSIVE] sandbox 내부 디렉토리의 파일/폴더 목록을 반환합니다.
    
    재귀 탐색 지원: recursive=True 시 하위 디렉토리까지 탐색하며, max_depth로 깊이 제한.
    
    주의: .admin_trash, .backup 폴더는 관리자 전용이므로 목록에서 제외됩니다.
    Tree 구조로 시각적 출력 제공.
    
    Args:
        dir_path: 탐색할 디렉토리 경로 (기본값: ".")
        recursive: 하위 디렉토리까지 재귀 탐색 여부 (기본값: False)
        max_depth: 재귀 탐색 시 최대 깊이 (기본값: 3, 범위: 1-5)
    
    예:
        - workspace 도구 목록 → list_directory("workspace")
        - 전체 구조 파악 → list_directory("workspace", recursive=True, max_depth=4)
    
    [cite: 2026-02-09] 경로 정규화: 상대 경로는 mellow_link 루트 기준으로 해석됩니다.
    ".", "workspace" 등은 기존 호환을 위해 workspace 루트로 해석됩니다.
    """
    # [UPGRADE_LIST_DIRECTORY_RECURSIVE] max_depth 검증
    if max_depth < 1:
        max_depth = 1
    elif max_depth > 5:
        max_depth = 5
        logger.warning("[list_directory] max_depth가 5를 초과하여 5로 제한됨")
    
    # 읽기 전용 정규화: 상대 경로를 mellow_link(sandbox) 기준으로 변환
    normalized_path = _normalize_read_path(dir_path)
    
    try:
        safe_path = _get_security().resolve_for_read(normalized_path)
    except SecurityBlocked as e:
        return str(e)
    except PermissionError as e:
        return f"[차단] 접근 불가: {e}"

    path_err = _ensure_path_inside_sandbox_for_read(safe_path, dir_path)
    if path_err:
        logger.critical("\033[91m[PATH_GATE_BLOCKED] list_directory path outside sandbox: '%s'\033[0m", dir_path)
        return path_err

    # 읽기 도구는 자동 생성하지 않는다.
    if not safe_path.exists():
        return f"[Error] 디렉터리가 존재하지 않습니다: {dir_path}"
    if not safe_path.is_dir():
        return f"[Error] 디렉토리가 아닙니다: {dir_path}"

    # [UPGRADE_LIST_DIRECTORY_RECURSIVE] Ignore Patterns
    IGNORE_PATTERNS = {
        "__pycache__", ".git", ".venv", "venv", "node_modules",
        ".pytest_cache", ".mypy_cache", ".ruff_cache", ".idea", ".vscode",
        "*.pyc", "*.pyo", "__pycache__", ".DS_Store",
        ".admin_trash",  # 관리자 전용 폴더 (삭제된 파일 보관)
        ".backup"  # 관리자 전용 폴더 (덮어쓰기 백업 보관)
    }
    
    def _should_ignore(name: str) -> bool:
        """파일/디렉토리 이름이 무시 패턴에 해당하는지 확인"""
        name_lower = name.lower()
        for pattern in IGNORE_PATTERNS:
            if pattern.startswith("*"):
                if name_lower.endswith(pattern[1:]):
                    return True
            elif name_lower == pattern.lower():
                return True
        return False
    
    def _build_tree(path: Path, prefix: str = "", is_last: bool = True, current_depth: int = 0) -> List[str]:
        """Tree 구조 문자열 생성 (재귀)"""
        lines = []
        if current_depth == 0:
            # 루트 디렉토리 표시
            rel_path = _pm().get_relative(path) if hasattr(_pm(), 'get_relative') else str(path)
            lines.append(f"{rel_path}/")
        else:
            connector = "└── " if is_last else "├── "
            lines.append(f"{prefix}{connector}{path.name}/" if path.is_dir() else f"{prefix}{connector}{path.name}")
        
        if not recursive or current_depth >= max_depth:
            return lines
        
        if not path.is_dir():
            return lines
        
        try:
            entries = sorted(path.iterdir(), key=lambda p: (p.is_file(), p.name))
            # 무시 패턴 필터링
            entries = [e for e in entries if not _should_ignore(e.name)]
            
            for idx, entry in enumerate(entries):
                is_last_entry = (idx == len(entries) - 1)
                next_prefix = prefix + ("    " if is_last else "│   ")
                
                if entry.is_dir():
                    lines.extend(_build_tree(entry, next_prefix, is_last_entry, current_depth + 1))
                else:
                    connector = "└── " if is_last_entry else "├── "
                    lines.append(f"{next_prefix}{connector}{entry.name}")
        except PermissionError:
            lines.append(f"{prefix}    [권한 없음]")
        except Exception as e:
            lines.append(f"{prefix}    [오류: {e}]")
        
        return lines
    
    # Cap from settings (tool output limit for p95 latency)
    try:
        from mellow_link.config.settings import get_settings
        max_items = get_settings().fs_list_max_items
    except Exception:
        max_items = 50

    # 재귀 모드가 아닌 경우: flat list with truncation metadata
    if not recursive:
        entries = sorted(safe_path.iterdir(), key=lambda p: (p.is_file(), p.name))
        entries = [e for e in entries if not _should_ignore(e.name)]
        sliced, total_count, truncated, next_offset = truncate_list(entries, limit=max_items, offset=0)
        lines = []
        for entry in sliced:
            prefix = "[DIR] " if entry.is_dir() else "      "
            lines.append(f"{prefix}{entry.name}")
        result = "\n".join(lines)
        if truncated:
            result += "\n\n" + format_truncation_footer(total_count, len(sliced), next_offset)
        return result

    # 재귀 모드: Tree 구조 출력 후 라인 수 cap
    tree_lines = _build_tree(safe_path, current_depth=0)
    total_files = sum(1 for line in tree_lines if "└──" in line or "├──" in line)
    total_dirs = sum(1 for line in tree_lines if "/" in line and ("└──" in line or "├──" in line))
    sliced_lines, total_line_count, truncated, next_offset = truncate_list(tree_lines, limit=max_items, offset=0)
    result = "\n".join(sliced_lines)
    result += f"\n\n[요약] 파일: {total_files}개, 디렉토리: {total_dirs}개 (max_depth={max_depth})"
    if truncated:
        result += "\n\n" + format_truncation_footer(total_line_count, len(sliced_lines), next_offset)
    return result


# ─── 보고서 생성 도구 (Security Masking + 공공기관 스타일 Markdown) ───
_VAULT_ANNOUNCE = (
    "보안상 해당 내용은 mellow_link/vault/ 내부의 특정 파일에 기록해 두었습니다. 직접 확인하시기 바랍니다."
)


@tool(category="filesystem")
def generate_report(
    title: str,
    overview: str = "",
    detail: str = "",
    result: str = "",
    future_plan: str = "",
    *,
    save_to_vault_if_sensitive: bool = True,
    sensitivity_ratio: float = 0.15,
) -> str:
    """
    현재 대화 맥락이나 특정 작업 결과를 공공기관 스타일의 Markdown 보고서로 요약·저장합니다.

    보안: 개인정보·API키·패스워드는 ***로 치환되고, 시스템 경로는 ~/... 형태로만 표시됩니다.
    내용이 지나치게 민감한 경우(save_to_vault_if_sensitive=True) 원문은 vault/에만 저장하고,
    안내 멘트를 반환합니다.

    Args:
        title: 보고서 제목(파일명으로 사용).
        overview: 개요.
        detail: 상세.
        result: 결과.
        future_plan: 향후 계획.
        save_to_vault_if_sensitive: True면 민감 비율 초과 시 원문을 vault/에 저장하고 안내 멘트 반환.
        sensitivity_ratio: 민감으로 간주하는 치환 비율(0~1, 기본 0.15).
    """
    raw_body = f"{overview}\n\n{detail}\n\n{result}\n\n{future_plan}".strip()
    if not raw_body and not title:
        return "[Error] 제목 또는 본문(개요/상세/결과/향후 계획 중 하나 이상)을 입력하세요."

    root = _pm().root
    masked_overview, _ = mask_report_content(overview, sandbox_root=root)
    masked_detail, _ = mask_report_content(detail, sandbox_root=root)
    masked_result, _ = mask_report_content(result, sandbox_root=root)
    masked_future, _ = mask_report_content(future_plan, sandbox_root=root)
    _, chars_masked = mask_report_content(raw_body, sandbox_root=root)
    original_len = len(raw_body)
    use_vault = (
        save_to_vault_if_sensitive
        and original_len > 0
        and is_too_sensitive(original_len, chars_masked, sensitivity_ratio)
    )

    # 공공기관 스타일 Markdown: 개요, 상세, 결과, 향후 계획
    parts = ["# " + (title or "보고서") + "\n"]
    if overview:
        parts.append("\n## 1. 개요\n\n" + masked_overview)
    if detail:
        parts.append("\n## 2. 상세\n\n" + masked_detail)
    if result:
        parts.append("\n## 3. 결과\n\n" + masked_result)
    if future_plan:
        parts.append("\n## 4. 향후 계획\n\n" + masked_future)
    body_md = "".join(parts).strip()
    if not body_md or body_md == "# " + (title or "보고서"):
        body_md = "# " + (title or "보고서") + "\n\n" + mask_report_content(raw_body, root)[0]

    safe_title = _pm().sanitize_filename(title or "report", fallback="report")
    report_path = f"outputs/reports/{safe_title}.md"
    try:
        safe_path = _get_security().resolve_for_write(report_path, content=body_md)
    except SecurityBlocked as e:
        return str(e)
    except PermissionError as e:
        return f"[차단] 접근 불가: {e}"

    safe_path.parent.mkdir(parents=True, exist_ok=True)
    safe_path.write_text(body_md, encoding="utf-8")
    msg = f"[완료] 보고서 저장: {_pm().get_relative(safe_path)}"

    if use_vault:
        vault_path = _pm().safe_join("vault", f"{safe_title}_raw", ".txt")
        try:
            vault_resolved = _get_security().resolve_for_write(vault_path, content=raw_body)
        except (SecurityBlocked, PermissionError):
            msg += f"\n\n{_VAULT_ANNOUNCE}"
            return msg
        vault_resolved.parent.mkdir(parents=True, exist_ok=True)
        vault_resolved.write_text(raw_body, encoding="utf-8")
        msg += f"\n\n{_VAULT_ANNOUNCE} (원문: {_pm().get_relative(vault_resolved)})"

    return msg


# ─── 정리(cleanup) 도구: workspace → archive, 승인 후 이동 ───
_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_PENDING_CLEANUPS_FILE = _DATA_DIR / "pending_cleanups.json"
_WORKSPACE_ARCHIVE = "workspace/archive"
_PROPOSAL_EXPIRY_HOURS = 24


def _load_pending_cleanups() -> Dict[str, Any]:
    """pending_cleanups.json 로드. 없으면 빈 dict."""
    if not _PENDING_CLEANUPS_FILE.exists():
        return {}
    try:
        with open(_PENDING_CLEANUPS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("[cleanup_file] pending_cleanups 로드 실패: %s", e)
        return {}


def _save_pending_cleanups(data: Dict[str, Any]) -> None:
    """pending_cleanups.json 저장."""
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(_PENDING_CLEANUPS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _is_proposal_expired(created_at_iso: str, expiry_hours: float = _PROPOSAL_EXPIRY_HOURS) -> bool:
    """created_at(ISO)이 expiry_hours를 초과했으면 True."""
    try:
        created = datetime.fromisoformat(created_at_iso.replace("Z", "+00:00"))
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - created) > timedelta(hours=expiry_hours)
    except (ValueError, TypeError):
        return True


@tool(category="filesystem")
def cleanup_file(
    mode: str,
    file_paths: Optional[List[str]] = None,
    reason: Optional[str] = None,
    proposal_id: Optional[str] = None,
) -> str:
    """
    workspace 내 중복·불필요 파일을 영구 삭제하지 않고 workspace/archive/로 이동시키는 도구.
    이동 전 반드시 사용자에게 대상 파일과 이유를 설명하고 승인을 받아야 한다.

    - mode='propose': 정리 대상 파일 목록과 이유를 제안하고, 사용자 승인 대기 상태로 둔다.
      사용자에게 어떤 파일을 왜 정리하는지 설명한 뒤, 승인을 받으면 mode='execute'를 호출한다.
    - mode='execute': 사용자가 승인한 제안(proposal_id)에 대해 실제로 workspace/archive/로 이동을 수행한다.

    Args:
        mode: 'propose' 또는 'execute'
        file_paths: (propose 시 필수) 정리 대상 sandbox 상대 경로 목록. 예: ['workspace/dup.py', 'workspace/old/script.py']
        reason: (propose 시 필수) 정리 이유(사용자에게 설명할 내용).
        proposal_id: (execute 시 필수) propose 시 반환된 제안 ID.
    """
    root = _pm().root
    workspace_root = root / "workspace"
    archive_root = root / "workspace" / "archive"

    if mode == "propose":
        if not file_paths or not reason or not reason.strip():
            return "[Error] propose 시 file_paths와 reason을 모두 입력하세요."
        validated: List[Path] = []
        errors: List[str] = []
        for p_str in file_paths:
            p_str = (p_str or "").strip()
            if not p_str:
                continue
            # [cite: 2026-02-09] 경로 정규화: 상대 경로를 workspace 기준으로 변환
            normalized_path = _normalize_workspace_path(p_str)
            try:
                safe = _get_security().resolve_for_read(normalized_path)
            except SecurityBlocked as e:
                errors.append(f"{p_str}: {e}")
                continue
            except Exception as e:
                errors.append(f"{p_str}: {e}")
                continue
            if not safe.exists():
                errors.append(f"{p_str}: 파일/디렉터리가 존재하지 않습니다.")
                continue
            if not safe.is_file():
                errors.append(f"{p_str}: 파일만 이동 가능합니다(디렉터리 제외).")
                continue
            rel_to_sandbox = _pm().get_relative(safe)
            rel_posix = str(rel_to_sandbox.as_posix())
            if not rel_posix.startswith("workspace/"):
                errors.append(f"{p_str}: workspace 내부 파일만 정리할 수 있습니다.")
                continue
            if rel_posix.startswith("workspace/archive/"):
                errors.append(f"{p_str}: 이미 archive에 있는 파일은 제외됩니다.")
                continue
            validated.append(rel_to_sandbox)

        if errors:
            return "[Error] 검증 실패:\n" + "\n".join(errors)
        if not validated:
            return "[Error] 이동할 유효한 파일이 없습니다."

        pid = uuid.uuid4().hex[:12]
        paths_str = [str(p) for p in validated]
        pending = _load_pending_cleanups()
        created_at_iso = datetime.now(timezone.utc).isoformat()
        pending[pid] = {
            "file_paths": paths_str,
            "reason": reason.strip(),
            "created_at": created_at_iso,
        }
        _save_pending_cleanups(pending)
        logger.info("[cleanup_file] ✅ verified: 제안 생성 및 저장 완료 proposal_id=%s", pid)

        lines = [
            "✅ verified: 제안이 등록되었습니다(24시간 내 실행 필요).",
            "",
            "다음 파일들을 workspace/archive/로 이동하려고 합니다(영구 삭제 아님):",
            "",
        ]
        for fp in paths_str:
            lines.append(f"  - {fp}")
        lines.extend(["", "이유: " + reason.strip(), ""])
        lines.append("사용자에게 위 내용을 설명하고 승인을 받은 뒤, 다음을 호출하세요:")
        lines.append(f"  cleanup_file(mode='execute', proposal_id='{pid}')")
        return "\n".join(lines)

    if mode == "execute":
        if not proposal_id or not proposal_id.strip():
            return "[Error] execute 시 proposal_id를 입력하세요."
        pid = proposal_id.strip()
        pending = _load_pending_cleanups()
        if pid not in pending:
            return f"[Error] 제안 ID '{pid}'를 찾을 수 없습니다. 이미 처리되었거나 잘못된 ID입니다."
        entry = pending.pop(pid)
        created_at_iso = entry.get("created_at") or ""
        if _is_proposal_expired(created_at_iso):
            _save_pending_cleanups(pending)
            logger.info("[cleanup_file] ✅ verified: 제안 만료로 무효화 및 삭제 proposal_id=%s", pid)
            return "[Error] ✅ verified: 제안이 만료되었습니다(24시간 초과). 무효화되었으며 삭제되었습니다. 다시 propose 후 승인해 주세요."

        _save_pending_cleanups(pending)
        logger.info("[cleanup_file] ✅ verified: 제안 로드 및 만료 검사 통과 proposal_id=%s", pid)

        paths_str = entry.get("file_paths") or []
        moved_pairs: List[Tuple[Path, Path]] = []
        result_lines: List[str] = ["✅ verified: execute 시작(원자성 보장: 실패 시 롤백)."]
        failed_msg: Optional[str] = None

        archive_root.mkdir(parents=True, exist_ok=True)
        for idx, p_str in enumerate(paths_str):
            # [cite: 2026-02-09] 경로 정규화: 상대 경로를 workspace 기준으로 변환
            normalized_path = _normalize_workspace_path(p_str)
            try:
                src = _get_security().resolve_for_read(normalized_path)
            except (SecurityBlocked, Exception) as e:
                failed_msg = f"{p_str}: {e}"
                logger.warning("[cleanup_file] execute 검증 실패: %s", failed_msg)
                break
            if not src.exists() or not src.is_file():
                failed_msg = f"{p_str}: 파일이 없거나 디렉터리입니다."
                break
            try:
                rel_ws = src.relative_to(workspace_root)
            except ValueError:
                failed_msg = f"{p_str}: workspace 경로가 아님."
                break
            dest_dir = archive_root / rel_ws.parent
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest = dest_dir / src.name
            if dest.exists():
                stem, suffix = src.stem, src.suffix
                dest = dest_dir / f"{stem}_{datetime.now().strftime('%Y%m%d_%H%M%S')}{suffix}"
            try:
                shutil.move(str(src), str(dest))
                moved_pairs.append((src, dest))
                result_lines.append(f"  ✅ verified: 이동 완료 ({idx + 1}/{len(paths_str)}) {p_str} → {_pm().get_relative(dest)}")
                logger.info("[cleanup_file] ✅ verified: 파일 이동 완료 %s -> %s", p_str, dest)
            except OSError as e:
                failed_msg = f"{p_str}: 이동 실패 - {e}"
                logger.warning("[cleanup_file] 이동 실패, 롤백 시작: %s", failed_msg)
                break

        if failed_msg is not None and moved_pairs:
            rollback_ok = True
            for src, dest in reversed(moved_pairs):
                try:
                    shutil.move(str(dest), str(src))
                    logger.info("[cleanup_file] ✅ verified: 롤백 원복 %s -> %s", dest, src)
                except OSError as re:
                    rollback_ok = False
                    logger.exception("[cleanup_file] 롤백 실패: %s -> %s: %s", dest, src, re)
            result_lines.append("")
            result_lines.append("✅ verified: 롤백 완료(이미 이동된 파일 전부 원복).")
            if not rollback_ok:
                result_lines.append("⚠️ possible: 일부 파일 롤백에 실패했을 수 있습니다. 상태를 확인하세요.")
            result_lines.append("")
            result_lines.append(f"[Error] 원자성 보장으로 작업 중단. {failed_msg}")
            return "\n".join(result_lines)

        if failed_msg is not None:
            result_lines.append("")
            result_lines.append(f"[Error] {failed_msg}")
            return "\n".join(result_lines)

        result_lines.append("")
        result_lines.append("[완료] ✅ verified: workspace/archive/로 이동 완료(원자적 적용).")
        for src, dest in moved_pairs:
            result_lines.append(f"  - {_pm().get_relative(src)} → {_pm().get_relative(dest)}")
        logger.info("[cleanup_file] ✅ verified: execute 전체 완료 proposal_id=%s moved=%s", pid, len(moved_pairs))
        return "\n".join(result_lines)

    return "[Error] mode는 'propose' 또는 'execute'만 가능합니다."


# ─── 파일 삭제 도구: workspace 내부 파일을 관리자 전용 폴더로 이동 ───
_ADMIN_TRASH_DIR = "workspace/.admin_trash"


@tool(category="filesystem")
def delete_file(file_path: str) -> str:
    """
    workspace 내부 파일을 삭제합니다 (실제로는 관리자 전용 폴더로 이동).
    
    이 도구는 workspace 내부 파일만 대상으로 하며, 파일을 완전히 삭제하지 않고
    workspace/.admin_trash/ 폴더로 이동시킵니다. 이 폴더는 관리자만 접근할 수 있습니다.
    
    Args:
        file_path: 삭제할 파일 경로 (workspace 내부 파일만 가능)
    
    Returns:
        작업 결과 메시지
    """
    workspace_root = get_workspace_root()
    admin_trash_root = workspace_root / ".admin_trash"
    
    # [cite: 2026-02-09] 경로 정규화: 상대 경로를 workspace 기준으로 변환
    normalized_path = _normalize_workspace_path(file_path)
    
    try:
        # 보안 검증: 읽기 권한 확인
        safe_path = _get_security().resolve_for_read(normalized_path)
    except SecurityBlocked as e:
        return f"[차단] 접근 불가: {e}"
    except Exception as e:
        return f"[오류] 경로 확인 실패: {e}"
    
    # workspace 내부인지 확인
    path_err = _ensure_path_inside_workspace(safe_path, file_path)
    if path_err:
        logger.critical("\033[91m[PATH_GATE_BLOCKED] delete_file path outside workspace: '%s'\033[0m", file_path)
        return path_err
    
    # 파일 존재 확인
    if not safe_path.exists():
        return f"[오류] 파일이 존재하지 않습니다: {file_path}"
    
    if not safe_path.is_file():
        return f"[오류] 파일만 삭제할 수 있습니다 (디렉터리 제외): {file_path}"
    
    # admin_trash 폴더로의 이동은 workspace 내부이므로 허용
    # 하지만 .admin_trash 자체는 특별 처리
    try:
        rel_to_workspace = safe_path.relative_to(workspace_root)
    except ValueError:
        return f"[오류] workspace 경로가 아닙니다: {file_path}"
    
    # .admin_trash 내부 파일은 삭제하지 않음 (이미 삭제된 것으로 간주)
    if str(rel_to_workspace).startswith(".admin_trash/"):
        return "[오류] 이미 삭제된 파일입니다 (.admin_trash 내부 파일은 삭제할 수 없습니다)"
    
    # 관리자 전용 폴더 생성
    admin_trash_root.mkdir(parents=True, exist_ok=True)
    
    # 타임스탬프와 함께 이동 (중복 방지)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    file_name = safe_path.name
    # 원본 경로 구조 유지 (하위 디렉터리 구조 보존)
    relative_dir = rel_to_workspace.parent
    if str(relative_dir) != ".":
        dest_dir = admin_trash_root / relative_dir
        dest_dir.mkdir(parents=True, exist_ok=True)
        # 파일명에 타임스탬프 추가
        name_parts = file_name.rsplit(".", 1)
        if len(name_parts) == 2:
            new_name = f"{name_parts[0]}_{timestamp}.{name_parts[1]}"
        else:
            new_name = f"{file_name}_{timestamp}"
        dest_path = dest_dir / new_name
    else:
        # 루트 파일
        name_parts = file_name.rsplit(".", 1)
        if len(name_parts) == 2:
            new_name = f"{name_parts[0]}_{timestamp}.{name_parts[1]}"
        else:
            new_name = f"{file_name}_{timestamp}"
        dest_path = admin_trash_root / new_name
    
    try:
        # 파일 이동
        shutil.move(str(safe_path), str(dest_path))
        logger.info("[delete_file] 파일 이동 완료: %s -> %s", file_path, dest_path.relative_to(workspace_root))
        
        return (
            f"[완료] 파일이 관리자 전용 폴더로 이동되었습니다.\n"
            f"원본: {file_path}\n"
            f"이동 위치: {dest_path.relative_to(workspace_root)}\n"
            f"⚠️ 이 파일은 관리자만 접근할 수 있습니다."
        )
    except OSError as e:
        logger.error("[delete_file] 파일 이동 실패: %s", e)
        return f"[오류] 파일 이동 실패: {e}"


# ─── 파일 이동/이름 변경 도구 ───
_DANGEROUS_FILE_PATTERNS = {
    # 환경 변수 파일 (보안 위험)
    ".env", ".env.local", ".env.production", ".env.development",
    # Git 설정 파일 (버전 관리 영향)
    ".gitignore", ".gitattributes", ".gitmodules",
    # Python 패키지 구조 (workspace 내부라면 괜찮지만 일단 차단)
    "__init__.py",
    # 의존성 관리 (프로젝트 전체 영향 가능)
    "requirements.txt", "requirements-dev.txt", "setup.py", "pyproject.toml",
    # 설정 파일
    "config.py", "settings.py",
}


def _is_dangerous_file(file_path: str) -> Tuple[bool, Optional[str]]:
    """
    파일이 위험한 패턴인지 확인합니다.
    
    Returns:
        (위험 여부, 이유)
    """
    path_obj = Path(file_path)
    file_name = path_obj.name
    
    # 정확한 파일명 매칭
    if file_name in _DANGEROUS_FILE_PATTERNS:
        return True, f"보호된 파일 패턴: {file_name}"
    
    # .env.* 패턴
    if file_name.startswith(".env."):
        return True, f"환경 변수 파일: {file_name}"
    
    # workspace 루트의 특정 파일들만 차단 (하위 디렉터리는 허용)
    # 예: workspace/.env는 차단, workspace/subdir/.env는 허용 (하지만 일단 모두 차단)
    if file_name.startswith(".env"):
        return True, f"환경 변수 파일: {file_name}"
    
    return False, None


@tool(category="filesystem")
def move_file(source_path: str, dest_path: str) -> str:
    """
    workspace 내부 파일을 이동하거나 이름을 변경합니다.
    
    이 도구는 workspace 내부 파일만 대상으로 하며, 파일을 다른 위치로 이동하거나
    이름을 변경할 수 있습니다. 디렉터리 이동은 지원하지 않습니다.
    
    Args:
        source_path: 이동할 원본 파일 경로 (workspace 내부 파일만 가능)
        dest_path: 이동할 대상 경로 (workspace 내부만 가능)
    
    Returns:
        작업 결과 메시지
    """
    workspace_root = get_workspace_root()
    
    # [cite: 2026-02-09] 경로 정규화: 상대 경로를 workspace 기준으로 변환
    normalized_source = _normalize_workspace_path(source_path)
    normalized_dest = _normalize_workspace_path(dest_path)
    
    try:
        # 보안 검증: 읽기 권한 확인
        safe_source = _get_security().resolve_for_read(normalized_source)
    except SecurityBlocked as e:
        return f"[차단] 원본 파일 접근 불가: {e}"
    except Exception as e:
        return f"[오류] 원본 경로 확인 실패: {e}"
    
    # workspace 내부인지 확인
    path_err = _ensure_path_inside_workspace(safe_source, source_path)
    if path_err:
        logger.critical("\033[91m[PATH_GATE_BLOCKED] move_file source path outside workspace: '%s'\033[0m", source_path)
        return path_err
    
    # 파일 존재 확인
    if not safe_source.exists():
        return f"[오류] 원본 파일이 존재하지 않습니다: {source_path}"
    
    if not safe_source.is_file():
        return f"[오류] 파일만 이동할 수 있습니다 (디렉터리 제외): {source_path}"
    
    # 위험한 파일 패턴 확인
    is_dangerous, reason = _is_dangerous_file(dest_path)
    if is_dangerous:
        return f"[차단] 위험한 파일 패턴: {reason}\n이 파일은 보안상 이동할 수 없습니다."
    
    # 대상 경로 보안 검증
    try:
        # 대상 경로는 쓰기 권한으로 확인
        safe_dest = _get_security().resolve_for_write(normalized_dest, content="")
    except SecurityBlocked as e:
        return f"[차단] 대상 경로 접근 불가: {e}"
    except Exception as e:
        return f"[오류] 대상 경로 확인 실패: {e}"
    
    # 대상 경로도 workspace 내부인지 확인
    path_err = _ensure_path_inside_workspace(safe_dest, dest_path)
    if path_err:
        logger.critical("\033[91m[PATH_GATE_BLOCKED] move_file dest path outside workspace: '%s'\033[0m", dest_path)
        return path_err
    
    # .admin_trash로의 이동은 delete_file을 사용하도록 안내
    try:
        rel_dest = safe_dest.relative_to(workspace_root)
        if str(rel_dest).startswith(".admin_trash/"):
            return "[안내] 파일 삭제는 delete_file 도구를 사용하세요."
    except ValueError:
        pass
    
    # 대상 디렉터리 생성
    safe_dest.parent.mkdir(parents=True, exist_ok=True)
    
    # 대상 파일이 이미 존재하는지 확인
    if safe_dest.exists():
        return f"[오류] 대상 파일이 이미 존재합니다: {dest_path}\n덮어쓰려면 먼저 기존 파일을 삭제하세요."
    
    try:
        # 파일 이동
        shutil.move(str(safe_source), str(safe_dest))
        logger.info("[move_file] 파일 이동 완료: %s -> %s", source_path, dest_path)
        
        return (
            f"[완료] 파일이 이동되었습니다.\n"
            f"원본: {source_path}\n"
            f"대상: {dest_path}"
        )
    except OSError as e:
        logger.error("[move_file] 파일 이동 실패: %s", e)
        return f"[오류] 파일 이동 실패: {e}"


# ─── 디렉터리 생성 도구 ───
@tool(category="filesystem")
def create_directory(dir_path: str) -> str:
    """
    workspace 내부에 디렉터리를 생성합니다.
    
    이 도구는 workspace 내부에만 디렉터리를 생성할 수 있으며,
    상위 디렉터리가 없으면 자동으로 생성됩니다.
    
    Args:
        dir_path: 생성할 디렉터리 경로 (workspace 내부만 가능)
    
    Returns:
        작업 결과 메시지
    """
    workspace_root = get_workspace_root()
    
    # [cite: 2026-02-09] 경로 정규화: 상대 경로를 workspace 기준으로 변환
    normalized_path = _normalize_workspace_path(dir_path)
    
    try:
        # 보안 검증: 쓰기 권한 확인
        safe_path = _get_security().resolve_for_write(normalized_path, content="")
    except SecurityBlocked as e:
        return f"[차단] 접근 불가: {e}"
    except Exception as e:
        return f"[오류] 경로 확인 실패: {e}"
    
    # workspace 내부인지 확인
    path_err = _ensure_path_inside_workspace(safe_path, dir_path)
    if path_err:
        logger.critical("\033[91m[PATH_GATE_BLOCKED] create_directory path outside workspace: '%s'\033[0m", dir_path)
        return path_err
    
    # 이미 존재하는지 확인
    if safe_path.exists():
        if safe_path.is_dir():
            return f"[정보] 디렉터리가 이미 존재합니다: {dir_path}"
        else:
            return f"[오류] 같은 이름의 파일이 존재합니다: {dir_path}"
    
    # 위험한 디렉터리 이름 확인 (특정 이름의 디렉터리는 차단)
    dangerous_dir_names = {".env", ".git", "__pycache__", "node_modules"}
    if safe_path.name in dangerous_dir_names:
        return f"[차단] 보호된 디렉터리 이름: {safe_path.name}"
    
    try:
        # 디렉터리 생성
        safe_path.mkdir(parents=True, exist_ok=True)
        logger.info("[create_directory] 디렉터리 생성 완료: %s", dir_path)
        
        return f"[완료] 디렉터리가 생성되었습니다: {dir_path}"
    except OSError as e:
        logger.error("[create_directory] 디렉터리 생성 실패: %s", e)
        return f"[오류] 디렉터리 생성 실패: {e}"
