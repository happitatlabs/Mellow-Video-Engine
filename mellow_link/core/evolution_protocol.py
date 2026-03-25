"""
Evolution 프로토콜: EVOLUTION_PROTOCOL.json 로드, 쿨다운, 일일 한도.

retry, cost_guard, auto_apply_scope, evolution_rules 등 설정을 로드하고
일일 진입 한도 및 쿨다운 검사를 수행한다.
"""
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

from mellow_link.core.evolution_logging import _log_evolution

logger = logging.getLogger(__name__)


def _load_evolution_protocol() -> Dict[str, Any]:
    """EVOLUTION_PROTOCOL.json 로드. 없거나 오류 시 빈 dict."""
    try:
        base = Path(__file__).resolve().parent.parent
        path = base / "EVOLUTION_PROTOCOL.json"
        if not path.exists():
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception as e:
        logger.debug("[EvolutionManager] Protocol load skipped: %s", e)
        return {}


def _get_protocol_retry_limit() -> int:
    """프로토콜의 max_retries 반환. 기본 1 (루프 방지)."""
    protocol = _load_evolution_protocol()
    retry_cfg = protocol.get("retry") or {}
    val = retry_cfg.get("max_retries", 1)
    return max(0, min(int(val) if isinstance(val, (int, float)) else 1, 5))


def _get_protocol_cost_cap_usd() -> float:
    """프로토콜의 cost_cap_per_cycle_usd 반환. 기본 0.5"""
    protocol = _load_evolution_protocol()
    cost_cfg = protocol.get("cost_guard") or {}
    val = cost_cfg.get("cost_cap_per_cycle_usd", 0.5)
    return max(0.0, float(val) if isinstance(val, (int, float)) else 0.5)


def _get_protocol_past_failure_limit() -> int:
    """프로토콜의 past_failure_limit 반환. Tower/Verdict 주입 과거 실패 사례 수. 기본 3"""
    protocol = _load_evolution_protocol()
    quality_cfg = protocol.get("quality") or {}
    val = quality_cfg.get("past_failure_limit", 3)
    return max(0, min(int(val) if isinstance(val, (int, float)) else 3, 20))


def _get_protocol_auto_apply_scope() -> List[str]:
    """프로토콜의 auto_apply_scope.path_prefixes. Audit 통과 시 승인 없이 자동 적용할 경로 접두사."""
    protocol = _load_evolution_protocol()
    cfg = protocol.get("auto_apply_scope") or {}
    prefixes = cfg.get("path_prefixes")
    if not isinstance(prefixes, list):
        return []
    return [str(p).strip().strip("/") for p in prefixes if p]


def _get_protocol_post_apply_verify_enabled() -> bool:
    """프로토콜의 post_apply_verify.enabled. 적용 직후 검증 실행 여부."""
    protocol = _load_evolution_protocol()
    cfg = protocol.get("post_apply_verify") or {}
    val = cfg.get("enabled", True)
    if isinstance(val, bool):
        return val
    if isinstance(val, str):
        return val.strip().lower() in ("1", "true", "yes", "y", "on")
    return True


def _get_protocol_path_scope_core() -> str:
    """프로토콜의 evolution_rules.path_scope.core. 'denied'면 core/ 경로 자동 진화 차단."""
    protocol = _load_evolution_protocol()
    rules = protocol.get("evolution_rules") or {}
    path_scope = rules.get("path_scope") or {}
    val = path_scope.get("core")
    return str(val).strip().lower() if val else ""


def _get_protocol_cost_cap_daily_usd() -> float:
    """프로토콜의 cost_guard.cost_cap_daily_usd. 0이면 비활성."""
    protocol = _load_evolution_protocol()
    cost_cfg = protocol.get("cost_guard") or {}
    val = cost_cfg.get("cost_cap_daily_usd", 0)
    return max(0.0, float(val) if isinstance(val, (int, float)) else 0)


def _get_protocol_cooldown_minutes() -> int:
    """프로토콜의 cooldown_minutes. evolution_rules.cost_cap 또는 cost_guard."""
    protocol = _load_evolution_protocol()
    rules = protocol.get("evolution_rules") or {}
    cap = rules.get("cost_cap") or {}
    val = cap.get("cooldown_minutes")
    if val is not None and isinstance(val, (int, float)):
        return max(0, int(val))
    cost_cfg = protocol.get("cost_guard") or {}
    val = cost_cfg.get("cooldown_minutes")
    if val is not None and isinstance(val, (int, float)):
        return max(0, int(val))
    return 0


def _get_protocol_max_cycles_per_day() -> int:
    """프로토콜의 evolution_rules.cost_cap.max_cycles_per_day. 0이면 비활성."""
    protocol = _load_evolution_protocol()
    rules = protocol.get("evolution_rules") or {}
    cap = rules.get("cost_cap") or {}
    val = cap.get("max_cycles_per_day", 0)
    return max(0, int(val) if isinstance(val, (int, float)) else 0)


def _get_protocol_auto_evolved_tag() -> str:
    """프로토콜의 auto_evolved_tag.tag. 자동 진화 결과에 붙일 태그."""
    protocol = _load_evolution_protocol()
    cfg = protocol.get("auto_evolved_tag") or {}
    val = cfg.get("tag")
    return str(val).strip() if val else "[AUTO_EVOLVED]"


def _get_evolution_cooldown_path() -> Path:
    """쿨다운 타임스탬프 저장 경로."""
    base = Path(__file__).resolve().parent.parent
    return base / "data" / "evolution_cooldown.txt"


def _record_cycle_end() -> None:
    """사이클 종료 시점 기록. 쿨다운 계산용."""
    try:
        p = _get_evolution_cooldown_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(datetime.now().isoformat(), encoding="utf-8")
    except Exception as e:
        logger.debug("[EvolutionManager] _record_cycle_end failed: %s", e)


def _is_in_cooldown() -> bool:
    """쿨다운 구간 여부. 프로토콜 cooldown_minutes 이내면 True."""
    mins = _get_protocol_cooldown_minutes()
    if mins <= 0:
        return False
    try:
        p = _get_evolution_cooldown_path()
        if not p.exists():
            return False
        text = p.read_text(encoding="utf-8").strip()
        if not text:
            return False
        ts = datetime.fromisoformat(text)
        return (datetime.now() - ts).total_seconds() < mins * 60
    except Exception:
        return False


def _check_daily_limits() -> Tuple[bool, str]:
    """
    일일 비용/횟수 상한 체크.
    Returns:
        (can_proceed, reason) - can_proceed가 False면 reason에 사유.
    """
    try:
        from mellow_link.core.database import get_daily_evolution_stats
        daily_cost, daily_cycles = get_daily_evolution_stats()
    except Exception as e:
        logger.debug("[EvolutionManager] get_daily_evolution_stats failed: %s", e)
        return True, ""

    cap_usd = _get_protocol_cost_cap_daily_usd()
    if cap_usd > 0 and daily_cost >= cap_usd:
        msg = f"일일 비용 한도 초과 (금일 {daily_cost:.4f} USD >= {cap_usd} USD)"
        _log_evolution("DAILY_COST_CAP", msg)
        return False, msg

    max_cycles = _get_protocol_max_cycles_per_day()
    if max_cycles > 0 and daily_cycles >= max_cycles:
        msg = f"일일 진화 횟수 한도 초과 (금일 {daily_cycles}회 >= {max_cycles}회)"
        _log_evolution("DAILY_CYCLES_CAP", msg)
        return False, msg

    if _is_in_cooldown():
        msg = f"쿨다운 구간 (cooldown_minutes={_get_protocol_cooldown_minutes()})"
        _log_evolution("COOLDOWN_SKIP", msg)
        return False, msg

    return True, ""
