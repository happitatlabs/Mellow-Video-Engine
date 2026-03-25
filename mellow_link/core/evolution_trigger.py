"""
Evolution Trigger - 자율 진화 트리거

시스템 상태(과거 실패, 통찰, 진단)를 분석하여 Evolution 파이프라인을 자동으로 트리거합니다.
Scheduler가 주기적으로 실행. 사람 승인은 기존대로 유지(apply_from_proposal).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional, Tuple, Any

logger = logging.getLogger(__name__)


def _load_protocol_trigger_config() -> dict:
    """EVOLUTION_PROTOCOL의 evolution_trigger 설정."""
    try:
        base = Path(__file__).resolve().parent.parent
        path = base / "EVOLUTION_PROTOCOL.json"
        if not path.exists():
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
        cfg = (data or {}).get("evolution_trigger") or {}
        return cfg if isinstance(cfg, dict) else {}
    except Exception as e:
        logger.debug("[EvolutionTrigger] Protocol load failed: %s", e)
        return {}


def is_evolution_trigger_enabled() -> bool:
    """진화 트리거 활성화 여부. 프로토콜 evolution_trigger.enabled 또는 ENABLE_EVOLUTION_TRIGGER."""
    import os
    cfg = _load_protocol_trigger_config()
    if "enabled" in cfg:
        val = cfg.get("enabled")
        if isinstance(val, bool):
            return val
        if isinstance(val, str):
            return val.strip().lower() in ("1", "true", "yes", "y", "on")
    return (os.getenv("ENABLE_EVOLUTION_TRIGGER") or "").strip().lower() in ("1", "true", "yes", "y", "on")


def get_evolution_trigger_schedule_seconds() -> int:
    """진화 트리거 실행 주기(초). 기본 6시간(21600)."""
    cfg = _load_protocol_trigger_config()
    val = cfg.get("schedule_seconds", 21600)
    try:
        return max(3600, int(val))  # 최소 1시간
    except (TypeError, ValueError):
        return 21600


async def run_evolution_tick() -> Tuple[bool, Optional[Any], str]:
    """
    진화 트리거 1회 실행.

    과거 실패·통찰·진단 요약을 Tower에 전달하여, 개선이 필요하면 user_request를 도출하고
    get_evolution_service().run_cycle() 호출. 적용은 기존대로 승인 후에만 수행.

    Returns:
        (success, proposal_or_response, message)
    """
    from mellow_link.infra.env_loader import load_dotenv_early
    from mellow_link.core.provider_factory import get_client, generate_async
    from mellow_link.core.evolution_factory import get_evolution_service
    from mellow_link.infra.memory_database import get_memory_db

    load_dotenv_early()

    # TRIGGER_DISABLED: 자동 틱만 비활성. 수동 /evolution/cycle 은 가능.
    if not is_evolution_trigger_enabled():
        logger.info("[EvolutionTrigger] TRIGGER_DISABLED: 자동 트리거 비활성화 (ENABLE_EVOLUTION_TRIGGER=0). 수동 /evolution/cycle 은 가능.")
        return False, None, "TRIGGER_DISABLED: 자동 트리거 비활성화. 수동 /evolution/cycle 은 가능."

    # 게이트 판정: ENABLE_GUARDIAN_APIS=0 또는 ENABLE_EVOLUTION_ADAPTER=0 이면 Tower/Verdict/Audit 호출 없이 즉시 반환
    svc = get_evolution_service()
    gate_resp = await svc.run_cycle("")
    if gate_resp.status == "DISABLED" and gate_resp.disabled_reason:
        logger.info("[EvolutionTrigger] Gate block before Tower: %s", gate_resp.disabled_reason.code)
        msg = gate_resp.disabled_reason.message or gate_resp.error or gate_resp.disabled_reason.code
        return False, gate_resp.to_dict(), msg[:300]

    try:
        db = get_memory_db()
        base = Path(__file__).resolve().parent.parent
        log_dir = base / "logs"

        # 1. 컨텍스트 수집
        past_failure_block = ""
        try:
            from mellow_link.core.agent_tools import get_past_failure_context
            limit = 5
            cfg = _load_protocol_trigger_config()
            if "past_failure_limit" in cfg:
                try:
                    limit = max(1, min(int(cfg["past_failure_limit"]), 10))
                except (TypeError, ValueError):
                    pass
            past_failure_block = get_past_failure_context(target_file=None, limit=limit)
            if past_failure_block:
                past_failure_block = f"\n## 과거 Evolution 실패 사례\n{past_failure_block}\n"
        except Exception as e:
            logger.debug("[EvolutionTrigger] get_past_failure_context skipped: %s", e)

        insights_block = ""
        try:
            insights = db.get_recent_insights(limit=5, min_confidence=0.65, days_threshold=14)
            if insights:
                lines = []
                for i, ins in enumerate(insights[:5], 1):
                    rec = (ins.recommendation or "").strip()
                    if rec:
                        lines.append(f"  {i}. {rec[:300]}")
                if lines:
                    insights_block = "\n## 행동 로그 분석 권고 (미적용 통찰)\n" + "\n".join(lines) + "\n"
        except Exception as e:
            logger.debug("[EvolutionTrigger] get_recent_insights skipped: %s", e)

        diagnosis_block = ""
        try:
            from mellow_link.core.diagnosis_service import get_diagnosis_service
            svc = get_diagnosis_service()
            report = svc.run_diagnosis()
            txt = svc.generate_dashboard_text(report)
            if txt:
                diagnosis_block = f"\n## 최근 성능 진단\n{txt[:1500]}\n"
        except Exception as e:
            logger.debug("[EvolutionTrigger] diagnosis skipped: %s", e)

        # ✅ verified: Goal-Trigger 연동 — 활성 목표를 Tower에 주입, 목표 달성에 필요한 수정 모듈 특정
        active_goals_block = ""
        root_goal_id_for_proposal: Optional[str] = None
        try:
            from mellow_link.core.goal_manager import get_goal_manager
            gm = get_goal_manager()
            active_goals = gm.get_active_goals(limit=10)
            if active_goals:
                lines = []
                for g in active_goals:
                    lines.append(f"  - id={g.id} priority={g.priority} title={g.title[:80]} desc={g.description[:120]}")
                active_goals_block = "\n## 활성 목표 (우선순위·미완료)\n" + "\n".join(lines) + "\n"
        except Exception as e:
            logger.debug("[EvolutionTrigger] get_active_goals skipped: %s", e)

        system_logs = ""
        for name in ("evolution.log", "system.log"):
            p = log_dir / name
            if p.exists():
                try:
                    system_logs += f"\n--- {name} ---\n{p.read_text(encoding='utf-8', errors='replace')[-8000:]}\n"
                except Exception:
                    pass
        if not system_logs.strip():
            system_logs = "(로그 없음)"

        # 2. Tower에 판단 요청 (활성 목표·인사이트 대조 → 목표 달성을 위해 수정이 필요한 모듈 특정)
        tower_cfg = get_client("google", role="tower")
        prompt = f"""너는 시스템 관제탑(Tower)이다.

## 자율 진화 트리거 판단
아래 시스템 상태와 **활성 목표**를 분석하여, 목표 달성을 위해 코드 수정이 필요한 구체적 개선이 있으면 user_request를 출력하라.
없으면 skip을 출력하라.
behavior_insights 권고와 active_goals를 대조하여, "목표 달성을 위해 수정이 필요한 모듈"을 특정하라.

{active_goals_block}
{past_failure_block}
{insights_block}
{diagnosis_block}
## 최근 로그 (일부)
{system_logs[:12000]}

## 제약
- 수정 가능 구역: services/, custom_tools/, workspace/ 만
- core/, main.py, .env 수정 금지
- 이미 반복 실패한 유사 제안은 하지 말 것
- 추상적 권고보다는 "특정 파일에 X 추가/수정" 형태로 구체적으로

## 요청
JSON만 출력:
{{"action": "evolve" | "skip", "reason": "판단 근거", "user_request": "evolve 시에만: 구체적 수정 요청", "goal_id": "evolve 시 해당 진화가 연결되는 활성 목표 id (위 목록 중 하나, 없으면 빈 문자열)"}}
action이 skip이면 user_request와 goal_id는 빈 문자열."""

        tower_text = await generate_async(
            tower_cfg.provider, tower_cfg.model, prompt, tower_cfg.api_key
        )
        plan_data = {}
        try:
            raw = tower_text or ""
            if "```json" in raw:
                raw = raw.split("```json")[1].split("```")[0].strip()
            elif "```" in raw:
                raw = raw.split("```")[1].split("```")[0].strip()
            plan_data = json.loads(raw)
        except json.JSONDecodeError:
            plan_data = {"action": "skip", "reason": "Tower 응답 파싱 실패"}

        action = (plan_data.get("action") or "skip").strip().lower()
        if action != "evolve":
            reason = (plan_data.get("reason") or "").strip() or "진화 불필요"
            logger.info("[EvolutionTrigger] Skip: %s", reason)
            return True, None, f"진화 스킵: {reason}"

        user_request = (plan_data.get("user_request") or "").strip()
        if not user_request or len(user_request) < 10:
            logger.warning("[EvolutionTrigger] evolve이지만 user_request 부족")
            return True, None, "진화 스킵: user_request 부족"

        root_goal_id = (plan_data.get("goal_id") or "").strip() or None

        # 3. Evolution 파이프라인 실행 (Facade 경유)
        svc = get_evolution_service()
        resp = await svc.run_cycle(user_request, root_goal_id=root_goal_id)

        if resp.status == "DISABLED" and resp.disabled_reason:
            logger.warning("[EvolutionTrigger] Evolution disabled: %s", resp.disabled_reason.message[:200])
            return False, resp.to_dict(), resp.disabled_reason.message[:300]
        if resp.error:
            logger.warning("[EvolutionTrigger] Evolution cycle error: %s", (resp.error or "")[:200])
            return False, resp.to_dict(), (resp.error or "")[:300]

        pid = resp.proposal_id or ""
        approved = resp.audit_approved if resp.audit_approved is not None else False
        msg = f"제안 생성 id={pid[:8] if pid else ''} audit_approved={approved}"
        logger.info("[EvolutionTrigger] %s", msg)
        return True, resp.to_dict(), msg

    except Exception as e:
        logger.exception("[EvolutionTrigger] run_evolution_tick failed")
        return False, None, str(e)[:300]
