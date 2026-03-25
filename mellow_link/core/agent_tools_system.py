"""
Agent Tools - 시스템/명령 도구: 자기 진단, 터미널 명령, 보안, KPI 등.
"""
import json
import logging
import re
import subprocess
from pathlib import Path
from typing import List

from mellow_link.core.tool_registry import tool, registry as _tool_registry
from mellow_link.core.security_manager import SecurityBlocked
from mellow_link.core.agent_tools_base import (
    _get_security,
    _pm,
    _load_dotenv_once,
    _require_requests,
    _compute_sandbox_root_for_security,
)

logger = logging.getLogger(__name__)


# ─── 자기 진단 도구 (자기 참조) ───
_MEMORY_DB_TABLES = [
    "experience_ledger",
    "tool_stats",
    "session_checkpoints",
    "goals",
    "behavior_insights",
    "scheduled_tasks",
    "performance_metrics",
    "dynamic_tools",
    "evolution_logs",
    "autonomous_work_results",
    "api_usage_logs",
]
_EVOLUTION_LEDGER_TABLES = ["evolution_history"]


def _workspace_tree_virtual(root: Path, workspace_dir: Path, max_entries: int = 80) -> List[str]:
    """workspace 디렉터리 트리를 sandbox 기준 가상 경로(~/)만 반환. 절대 경로 노출 없음."""
    lines: List[str] = []
    try:
        if not workspace_dir.exists() or not workspace_dir.is_dir():
            return ["(workspace 없음)"]
        count = [0]

        def _walk(current: Path, prefix: str) -> None:
            if count[0] >= max_entries:
                return
            try:
                entries = sorted(current.iterdir(), key=lambda p: (p.is_file(), p.name))
            except (PermissionError, OSError):
                return
            for i, entry in enumerate(entries):
                if count[0] >= max_entries:
                    lines.append(prefix + "... (생략)")
                    return
                try:
                    rel = entry.relative_to(root)
                    virtual = "~/" + str(rel.as_posix())
                except ValueError:
                    virtual = "~/" + entry.name
                connector = "└── " if i == len(entries) - 1 else "├── "
                lines.append(prefix + connector + virtual)
                count[0] += 1
                if entry.is_dir():
                    ext = "    " if i == len(entries) - 1 else "│   "
                    _walk(entry, prefix + ext)
            return
        _walk(workspace_dir, "")
    except Exception as e:
        lines.append(f"(트리 조회 오류: {e})")
    return lines


@tool(category="general")
def list_tools() -> str:
    """
    등록된 모든 도구의 전체 스펙을 반환합니다 (읽기 전용).
    thinking 모드에서 compact summary만 제공되므로, 상세 스펙이 필요할 때 호출하세요.
    보안: 파일시스템 접근 불가, 도구 메타데이터만 반환.
    """
    try:
        from mellow_link.core.dynamic_registry import get_dynamic_registry
        registry = get_dynamic_registry()
        tools_json = registry.get_tools_prompt()
        return f"[도구 전체 스펙]\n{tools_json}"
    except Exception as e:
        logger.warning(f"[list_tools] Failed: {e}")
        return f"[Error] 도구 목록 조회 실패: {e}"


@tool(category="general")
def inspect_system_status() -> str:
    """
    에이전트가 실시간으로 자신의 상태를 확인하는 자기 진단 도구.
    현재 활성화된 도구 목록, 연결된 DB 테이블 리스트, workspace 파일 트리 요약을 반환한다.
    보안: 실제 시스템 절대 경로는 노출하지 않고, sandbox 기준 가상 경로(~/)만 반환한다.
    """
    parts: List[str] = []

    # 1. 활성화된 도구 목록
    try:
        tool_names = _tool_registry.get_tool_names()
        parts.append("## 활성화된 도구 목록\n" + ", ".join(sorted(tool_names)))
    except Exception as e:
        parts.append("## 활성화된 도구 목록\n(조회 실패: " + str(e) + ")")

    # 2. 연결된 DB 테이블 (docs/system_map.md와 일치하는 고정 목록)
    parts.append("\n## DB 테이블 (메모리 DB)\n" + ", ".join(_MEMORY_DB_TABLES))
    parts.append("\n## DB 테이블 (Evolution 원장)\n" + ", ".join(_EVOLUTION_LEDGER_TABLES))

    # 3. workspace 파일 트리 요약 (가상 경로만)
    try:
        root = _pm().root
        workspace_dir = root / "workspace"
        tree_lines = _workspace_tree_virtual(root, workspace_dir)
        parts.append("\n## workspace 트리 (~/ 기준)\n" + "\n".join(tree_lines))
    except Exception as e:
        parts.append("\n## workspace 트리\n(조회 실패: " + str(e) + ")")

    return "\n".join(parts)


# ═══════════════════════════════════════════════
# 2. 터미널 명령 (토큰 단위 allowlist)
# ═══════════════════════════════════════════════

# 실행 가능한 명령어 (첫 번째 토큰만 허용)
_ALLOWED_COMMANDS = frozenset({
    "curl", "ping", "nslookup", "ipconfig", "whoami",
})

# 셸 메타 문자 패턴: 파이프, 체인, 리다이렉트, 서브셸 등
_SHELL_METACHAR = re.compile(r'[;&|`$><\n]|&&|\|\|')


@tool(category="system")
def run_command(command: str) -> str:
    """
    제한된 네트워크 명령어를 실행합니다.
    허용: curl, ping, nslookup, ipconfig, whoami.
    셸 체인(&&, ||, ;), 파이프(|), 리다이렉트(>, <) 는 모두 차단됩니다.
    """
    try:
        tokens = list(_get_security().parse_and_validate_command(command))
    except SecurityBlocked as e:
        return str(e)

    # Network hygiene: curl outbound restriction (V-07)
    try:
        cmd_name = Path(tokens[0]).stem.lower() if tokens else ""
    except Exception:
        cmd_name = ""

    if cmd_name == "curl" and _get_security().level != "EASY":
        # Block advanced/override flags (V-12)
        banned = {"--connect-to", "--resolve", "--proxy", "-x"}
        for t in tokens[1:]:
            if t in banned:
                raise SecurityBlocked(f"[차단] curl 위험 플래그 금지: {t}")
            # --proxy=..., --resolve=..., --connect-to=...
            if any(t.startswith(flag + "=") for flag in ("--connect-to", "--resolve", "--proxy")):
                raise SecurityBlocked(f"[차단] curl 위험 플래그 금지: {t.split('=')[0]}")
            # -x<proxy> 형태도 차단
            if t.startswith("-x") and t != "-X":
                raise SecurityBlocked("[차단] curl 위험 플래그 금지: -x")

        # 1) Extract explicit URLs from raw command (prefer https?://)
        urls = re.findall(r"(https?://[^\s'\"<>]+)", command or "")
        # 2) If curl is used without any explicit URL, allow only benign introspection.
        #    This prevents "curl example.com" style bypasses in stricter modes.
        benign = any(t in {"--version", "-V", "--help", "-h"} for t in tokens[1:])
        if not urls and not benign:
            return "[차단] curl 사용 시 대상 URL(https://...)이 필요합니다. 허용된 도메인만 접근 가능합니다."

        for url in urls:
            if not _get_security().is_outbound_http_allowed(url):
                return f"[차단] curl 아웃바운드 차단: {url}"

    # 3단계: shell=False로 실행 (OS가 토큰을 해석하지 않음)
    try:
        result = subprocess.run(
            tokens,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
            shell=False,  # 핵심: 셸 해석 비활성화
        )

        output = result.stdout.strip()
        if result.returncode != 0 and result.stderr.strip():
            output += f"\n[stderr] {result.stderr.strip()}"

        if len(output) > 2000:
            return output[:2000] + "\n...(2000자까지 표시)"
        return output if output else "[완료] (출력 없음)"

    except subprocess.TimeoutExpired:
        return "[Timeout] 30초 제한 시간 초과."
    except FileNotFoundError:
        return f"[Error] '{tokens[0]}' 명령어를 찾을 수 없습니다."
    except Exception as e:
        return f"[Error] 실행 실패: {e}"


@tool(category="system")
def get_evolution_proposals_summary(limit: int = 10) -> str:
    """
    삼권분립(자가발전) 결재 보고서 요약을 반환합니다.
    승인 대기 중인 제안과 최근 제안 목록을 확인할 때 사용하세요.
    """
    try:
        from mellow_link.config.settings import get_settings
        from mellow_link.core.agent_tools_base import format_truncation_footer
        cap = get_settings().fs_recent_max_items
        effective_limit = min(limit, cap) if limit else cap
        base = Path(__file__).resolve().parent.parent
        ledger_dir = base / "logs" / "evolution_proposals"
        if not ledger_dir.exists():
            return "[Evolution] 제안서 폴더가 없습니다."
        all_files = sorted(ledger_dir.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True)
        total_count = len(all_files)
        proposals = []
        for p in all_files[:effective_limit]:
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                proposals.append({
                    "id": data.get("id", "")[:8],
                    "user_request": (data.get("user_request") or "")[:150],
                    "target_file": data.get("verdict_target_file") or "(미지정)",
                    "audit_approved": bool(data.get("audit_approved")),
                    "verdict_reason": (data.get("verdict_reason") or "")[:200],
                    "created_at": data.get("created_at", "")[:19],
                })
            except Exception:
                continue
        if not proposals:
            return "[Evolution] 등록된 제안서가 없습니다."
        lines = ["# 삼권분립 결재 보고서 요약\n"]
        pending = [x for x in proposals if x["audit_approved"]]
        if pending:
            lines.append("## 승인 대기 중")
            for x in pending[:5]:
                lines.append(f"- ID:{x['id']} 대상:{x['target_file']} 요청:{x['user_request'][:80]}...")
        lines.append("\n## 최근 제안")
        for x in proposals[:7]:
            status = "✅승인대기" if x["audit_approved"] else "⚠️검수거부"
            lines.append(f"- [{status}] ID:{x['id']} {x['target_file']} | {x['user_request'][:60]}...")
        result = "\n".join(lines)
        if total_count > effective_limit:
            result += "\n\n" + format_truncation_footer(
                total_count, len(proposals), effective_limit,
                message="Results truncated to N items. Narrow your query for more.",
            )
        return result
    except Exception as e:
        logger.debug("[get_evolution_proposals_summary] %s", e)
        return f"[Error] 결재 요약 조회 실패: {e}"


# 현재 보안 정책 확인
@tool(category="system")
def security_status() -> str:
    """
    현재 SECURITY_LEVEL과 적용 중인 보안 정책(파일/명령/아웃바운드)을 요약해 보여줍니다.
    """
    snap = _get_security().policy_snapshot()
    return json.dumps(snap, ensure_ascii=False, indent=2)


@tool(category="system")
def get_kpi_dashboard(mode: str = "extended", days: int = 7) -> str:
    """
    시스템 성능 KPI 대시보드를 조회합니다.
    작업 성공률, 치명적 오류율, 검증 커버리지, 오류 재발률을 포함한 8대 KPI를 표시합니다.
    시스템 상태를 객관적으로 파악할 때 사용하세요.

    Args:
        mode: "basic" (기존 4대 KPI만) 또는 "extended" (8대 KPI + 한계 명시, 기본값)
        days: 분석 기간 (일, 기본값: 7)
    """
    try:
        from mellow_link.core.diagnosis_service import get_diagnosis_service
        svc = get_diagnosis_service()

        if mode == "basic":
            report = svc.run_diagnosis()
        else:
            report = svc.run_extended_diagnosis(days=days)

        return svc.generate_dashboard_text(report)
    except Exception as e:
        logger.exception("[get_kpi_dashboard] Failed")
        return f"[Error] KPI 대시보드 조회 실패: {e}"


@tool(category="system")
def check_security_integrity() -> str:
    """
    보안 핵심 상수(FORBIDDEN_NAMES 등)의 무결성을 검증합니다.
    SHA-256 해시를 baseline과 비교하여 변조 여부를 확인합니다.
    보안 상태를 점검하거나 이상 징후를 감지했을 때 사용하세요.
    """
    try:
        from mellow_link.core.tool_forge import IntegrityGuard
        result = IntegrityGuard.verify()
        if result.ok:
            return (
                "[보안 무결성 검증] ✅ 통과\n"
                f"검증 시각: {result.checked_at}\n"
                "FORBIDDEN_NAMES, OS_FORBIDDEN_ATTRS, HARD_IMPORT_WHITELIST "
                "모두 baseline과 일치합니다."
            )
        else:
            violations_text = "\n".join(f"  ⚠️ {v}" for v in result.violations)
            return (
                "[보안 무결성 검증] ❌ 위반 감지!\n"
                f"검증 시각: {result.checked_at}\n"
                f"위반 사항:\n{violations_text}\n\n"
                "관리자에게 즉시 보고해야 합니다. "
                "이 파일의 보안 상수가 변조되었을 수 있습니다."
            )
    except Exception as e:
        logger.exception("[check_security_integrity] Failed")
        return f"[Error] 보안 무결성 검증 실패: {e}"


# ═══════════════════════════════════════════════
# 3. 경량 읽기 전용 도구 (p95 이상치 감소용)
# ═══════════════════════════════════════════════

@tool(category="system")
def get_cwd() -> str:
    """
    현재 작업 디렉토리를 반환합니다 (읽기 전용).
    경량 도구로 빠르게 응답합니다.
    """
    try:
        import os
        cwd = os.getcwd()
        return json.dumps({"cwd": cwd}, ensure_ascii=False)
    except Exception as e:
        logger.warning(f"[get_cwd] Failed: {e}")
        return json.dumps({"error": str(e)}, ensure_ascii=False)


@tool(category="system")
def get_time() -> str:
    """
    현재 시간을 ISO 형식으로 반환합니다 (읽기 전용).
    Asia/Seoul 타임존을 사용하며, 실패 시 시스템 로컬 시간을 사용합니다.
    경량 도구로 빠르게 응답합니다.
    """
    try:
        from datetime import datetime
        try:
            import pytz
            tz = pytz.timezone("Asia/Seoul")
            now = datetime.now(tz)
            time_str = now.isoformat()
            timezone_str = "Asia/Seoul"
        except ImportError:
            # pytz가 없으면 시스템 로컬 시간 사용
            now = datetime.now()
            time_str = now.isoformat()
            timezone_str = "local"
        except Exception:
            # 타임존 설정 실패 시 시스템 로컬 시간 사용
            now = datetime.now()
            time_str = now.isoformat()
            timezone_str = "local"
        
        return json.dumps({"time": time_str, "timezone": timezone_str}, ensure_ascii=False)
    except Exception as e:
        logger.warning(f"[get_time] Failed: {e}")
        return json.dumps({"error": str(e)}, ensure_ascii=False)


@tool(category="system")
def get_system_snapshot() -> str:
    """
    시스템 정보 요약을 반환합니다 (읽기 전용).
    RAM 사용률, 디스크 사용률, CPU 사용률을 포함합니다.
    출력은 최대 800자로 제한됩니다.
    """
    try:
        import psutil
        import os
        
        # RAM 정보
        ram = psutil.virtual_memory()
        ram_used_percent = ram.percent
        ram_used_gb = ram.used / (1024 ** 3)
        ram_total_gb = ram.total / (1024 ** 3)
        
        # 디스크 정보 (프로젝트 드라이브 또는 루트)
        try:
            project_path = Path(__file__).resolve().parent.parent.parent
            disk = psutil.disk_usage(str(project_path))
            disk_used_percent = disk.percent
        except Exception:
            # 프로젝트 경로 실패 시 루트 드라이브 사용
            disk = psutil.disk_usage(os.path.abspath(os.sep))
            disk_used_percent = disk.percent
        
        # CPU 정보 (비용이 저렴한 경우에만)
        try:
            cpu_percent = psutil.cpu_percent(interval=0.1)
        except Exception:
            cpu_percent = None
        
        result = {
            "ram_used_percent": round(ram_used_percent, 1),
            "ram_used_gb": round(ram_used_gb, 2),
            "ram_total_gb": round(ram_total_gb, 2),
            "disk_used_percent": round(disk_used_percent, 1),
        }
        
        if cpu_percent is not None:
            result["cpu_percent"] = round(cpu_percent, 1)
        
        result_str = json.dumps(result, ensure_ascii=False)
        
        # 최대 800자 제한
        if len(result_str) > 800:
            result_str = result_str[:797] + "..."
        
        return result_str
    except Exception as e:
        logger.warning(f"[get_system_snapshot] Failed: {e}")
        return json.dumps({"error": str(e)}, ensure_ascii=False)


@tool(category="system")
def list_processes(limit: int = 20, offset: int = 0) -> str:
    """
    실행 중인 프로세스 목록을 반환합니다 (읽기 전용).
    최대 limit개(기본 20개)의 프로세스 정보를 반환합니다.
    
    Args:
        limit: 반환할 최대 프로세스 개수 (기본 20, 최대 50)
        offset: 건너뛸 프로세스 개수 (기본 0)
    
    Returns:
        JSON 문자열: 프로세스 목록 및 메타데이터
    """
    try:
        import psutil
        
        # limit 최대값 제한
        limit = min(max(limit, 1), 50)
        offset = max(offset, 0)
        
        processes = []
        total_count = 0
        
        # 프로세스 목록 수집
        for proc in psutil.process_iter(['pid', 'name', 'memory_info']):
            try:
                total_count += 1
                if total_count <= offset:
                    continue
                if len(processes) >= limit:
                    break
                
                pinfo = proc.info
                mem_info = pinfo.get('memory_info')
                mem_mb = round(mem_info.rss / (1024 ** 2), 1) if mem_info else 0.0
                
                processes.append({
                    "pid": pinfo.get('pid'),
                    "name": pinfo.get('name', 'unknown')[:50],  # 이름 길이 제한
                    "mem_mb": mem_mb
                })
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
        
        # 메타데이터 구성
        returned_count = len(processes)
        truncated = total_count > (offset + returned_count)
        next_offset = offset + returned_count if truncated else None
        
        result = {
            "processes": processes,
            "total_count": total_count,
            "returned_count": returned_count,
            "truncated": truncated,
            "next_offset": next_offset
        }
        
        return json.dumps(result, ensure_ascii=False)
    except Exception as e:
        logger.warning(f"[list_processes] Failed: {e}")
        return json.dumps({"error": str(e), "processes": [], "total_count": 0, "returned_count": 0, "truncated": False}, ensure_ascii=False)
