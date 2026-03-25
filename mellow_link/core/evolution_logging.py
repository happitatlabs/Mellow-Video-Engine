"""
Evolution 전용 로깅: logs/evolution.log 기록.

진화 이벤트 및 보안 차단 이벤트를 별도 로그 파일에 기록한다.
"""
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_evolution_logger: Optional[logging.Logger] = None


def _get_evolution_logger() -> logging.Logger:
    """logs/evolution.log에 기록하는 전용 로거."""
    global _evolution_logger
    if _evolution_logger is not None:
        return _evolution_logger
    _evolution_logger = logging.getLogger("mellow_link.evolution")
    _evolution_logger.setLevel(logging.INFO)
    if not _evolution_logger.handlers:
        base = Path(__file__).resolve().parent.parent
        log_dir = base / "logs"
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
            fh = logging.FileHandler(log_dir / "evolution.log", encoding="utf-8")
            fh.setFormatter(logging.Formatter(
                "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
            ))
            _evolution_logger.addHandler(fh)
        except Exception as e:
            logger.warning("[EvolutionManager] evolution.log handler setup failed: %s", e)
    return _evolution_logger


def _log_evolution(event: str, detail: str = "") -> None:
    """진화 이벤트를 logs/evolution.log에 기록."""
    try:
        msg = f"[Evolution] {event} {detail}".strip()
        _get_evolution_logger().info(msg)
    except Exception:
        pass


def _log_security_alert(event: str, detail: str = "") -> None:
    """보안 차단 이벤트를 logs/evolution.log에 [SECURITY_ALERT] 접두사로 기록."""
    try:
        msg = f"[SECURITY_ALERT] {event} {detail}".strip()
        _get_evolution_logger().warning(msg)
    except Exception:
        pass
