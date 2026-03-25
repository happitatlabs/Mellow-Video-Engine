"""
Notification Service - VIP 모바일 알림 (Telegram)

결재 보고서 생성 시 Admin(Mellow)의 휴대폰으로 즉시 알림 전송.
Mellow_Link_Spec.md Step 4.

인터페이스 보장: 승인 대기 알림은 어떤 경우에도 승인/거부 버튼을 반드시 포함.
"""

import html
import json
import logging
from typing import Optional, Dict, Any, List

logger = logging.getLogger(__name__)


def _build_evolution_approval_keyboard(proposal_id: str) -> Dict[str, Any]:
    """자가발전 승인/거부 인라인 키보드. 절대 누락 방지."""
    return {
        "inline_keyboard": [
            [
                {"text": "✅ 승인 (Apply)", "callback_data": f"evolve_approve:{proposal_id}"},
                {"text": "❌ 거부", "callback_data": f"evolve_reject:{proposal_id}"},
            ]
        ]
    }


def _build_autonomous_approval_keyboard(record_id: str) -> Dict[str, Any]:
    """자율 작업 승인/거부 인라인 키보드. 절대 누락 방지."""
    return {
        "inline_keyboard": [
            [
                {"text": "✅ 승인 (Approve)", "callback_data": f"approve:{record_id}"},
                {"text": "❌ 거부 (Reject)", "callback_data": f"reject:{record_id}"},
            ]
        ]
    }


def _build_plan_proceed_keyboard(proposal_id: str) -> Dict[str, Any]:
    """계획 진행 승인/취소 인라인 키보드."""
    return {
        "inline_keyboard": [
            [
                {"text": "✅ 진행 승인", "callback_data": f"evolve_plan_approve:{proposal_id}"},
                {"text": "❌ 취소", "callback_data": f"evolve_plan_reject:{proposal_id}"},
            ]
        ]
    }


def _safe_html(s: str, max_len: int = 500) -> str:
    """HTML 모드에서 안전하게 표시하기 위해 이스케이프."""
    if not s:
        return ""
    return html.escape(str(s)[:max_len])


def send_telegram(
    text: str,
    parse_mode: str = "HTML",
    reply_markup: Optional[Dict[str, Any]] = None,
    chat_id_override: Optional[str] = None,
) -> bool:
    """
    Telegram으로 메시지 전송.

    Args:
        text: 메시지 내용
        parse_mode: HTML 또는 Markdown
        reply_markup: InlineKeyboardMarkup 등 (dict)
        chat_id_override: 지정 시 해당 채팅에 전송 (미지정 시 TELEGRAM_CHAT_ID 사용)
    Returns:
        True if sent successfully, False otherwise.
    """
    from mellow_link.adapters.notify import get_notifier
    return get_notifier().send_telegram(
        text=text,
        parse_mode=parse_mode,
        reply_markup=reply_markup,
        chat_id_override=chat_id_override,
    )


def send_telegram_and_get_message_id(
    text: str,
    parse_mode: str = "HTML",
    reply_markup: Optional[Dict[str, Any]] = None,
    chat_id_override: Optional[str] = None,
) -> Optional[int]:
    """
    Telegram으로 메시지 전송 후 message_id 반환 (플레이스홀더 수정용).

    Args:
        text: 메시지 내용
        parse_mode: HTML 또는 Markdown
        reply_markup: InlineKeyboardMarkup 등 (dict)
        chat_id_override: 지정 시 해당 채팅에 전송 (미지정 시 TELEGRAM_CHAT_ID 사용)
    Returns:
        message_id if sent successfully, None otherwise.
    """
    from mellow_link.adapters.notify import get_notifier
    return get_notifier().send_telegram_and_get_message_id(
        text=text,
        parse_mode=parse_mode,
        reply_markup=reply_markup,
        chat_id_override=chat_id_override,
    )


def send_telegram_long(
    text: str,
    chat_id_override: Optional[str] = None,
    max_len: int = 4096,
    parse_mode: str = "HTML",
) -> bool:
    """
    4096자 초과 시 자동 분할 전송.

    Args:
        text: 메시지 내용
        chat_id_override: 지정 시 해당 채팅에 전송 (미지정 시 TELEGRAM_CHAT_ID 사용)
        max_len: 최대 길이 (기본 4096)
        parse_mode: HTML 또는 Markdown
    Returns:
        True if all parts sent successfully, False otherwise.
    """
    if len(text) <= max_len:
        return send_telegram(text, parse_mode=parse_mode, chat_id_override=chat_id_override)

    # 긴 메시지 분할
    parts = []
    current = ""
    for line in text.split("\n"):
        if len(current) + len(line) + 1 <= max_len:
            current += (line + "\n") if current else line
        else:
            if current:
                parts.append(current.rstrip())
            current = line
    if current:
        parts.append(current.rstrip())

    success = True
    for i, part in enumerate(parts):
        prefix = f"[{i+1}/{len(parts)}]\n\n" if len(parts) > 1 else ""
        if not send_telegram(prefix + part, parse_mode=parse_mode, chat_id_override=chat_id_override):
            success = False
    return success


def _telegram_api_call(method: str, payload: Dict[str, Any]) -> bool:
    """Telegram Bot API 호출 (answerCallbackQuery, editMessageText 등)."""
    try:
        from mellow_link.config.settings import get_settings
        s = get_settings()
        token = (s.telegram_bot_token or "").strip()
        if not token:
            return False
        import urllib.request
        import urllib.parse
        import json
        url = f"https://api.telegram.org/bot{token}/{method}"
        data = urllib.parse.urlencode({k: v if not isinstance(v, (dict, list)) else json.dumps(v) for k, v in payload.items()}).encode("utf-8")
        req = urllib.request.Request(url, data=data, method="POST")
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status == 200
    except Exception as e:
        logger.warning("[Notification] Telegram API %s failed: %s", method, e)
        return False


def telegram_answer_callback(callback_query_id: str, text: str = "") -> bool:
    """callback_query 응답 (로딩 해제)."""
    return _telegram_api_call("answerCallbackQuery", {"callback_query_id": callback_query_id, "text": text[:200]})


def telegram_edit_message(chat_id: str, message_id: int, text: str, parse_mode: str = "HTML") -> bool:
    """메시지 텍스트 수정 (버튼 제거됨)."""
    return _telegram_api_call("editMessageText", {
        "chat_id": chat_id, "message_id": message_id, "text": text[:4096], "parse_mode": parse_mode,
    })


def notify_evolution_proposal_ready(
    proposal_id: str,
    user_request: str,
    audit_approved: bool = True,
    target_file: str = "",
    cost_efficiency_briefing: str = "",
) -> bool:
    """
    결재 대기: 검수 통과한 제안만 Admin에게 Telegram 알림.
    인라인 버튼(승인/거부)은 어떤 경우에도 반드시 포함. (거부 시에는 자동 재시도하므로 알림하지 않음)
    """
    try:
        from mellow_link.config.settings import get_settings
        s = get_settings()
        if not getattr(s, "enable_mobile_notify", False):
            return False
        if not (getattr(s, "telegram_bot_token", "") or "").strip():
            return False
        if not (getattr(s, "telegram_chat_id", "") or "").strip():
            return False
    except Exception:
        return False

    if not proposal_id or not proposal_id.strip():
        logger.warning("[Notification] notify_evolution_proposal_ready: proposal_id 없음, 전송 생략")
        return False

    target_block = f"\n<b>대상 파일:</b> <code>{_safe_html(target_file[:80], 80)}</code>\n" if target_file else ""
    cost_block = f"\n💰 {_safe_html((cost_efficiency_briefing or '').strip()[:200], 200)}\n" if cost_efficiency_briefing else ""
    footer = "👇 버튼을 눌러 승인/거부 하세요"
    reply_markup = _build_evolution_approval_keyboard(proposal_id.strip())
    text = (
        f"🏛️ <b>Mellow-Link 결재 보고서 (자가발전)</b>\n\n"
        f"ID: <code>{proposal_id[:8]}</code>\n"
        f"요청: {_safe_html(user_request[:300], 300)}\n"
        f"{target_block}"
        f"{cost_block}"
        f"검수: ✅ 승인 (자동 재시도로 통과)\n\n"
        f"{footer}"
    )
    ok = send_telegram(text, reply_markup=reply_markup)
    if not ok:
        logger.warning("[Notification] Evolution 알림 전송 실패 (버튼 포함)")
    return ok


def notify_evolution_plan_ready(
    proposal_id: str,
    user_request: str,
    plan_summary: str,
    target_hint: str = "",
) -> bool:
    """
    계획 우선 보고: 대규모 수정 전 Proposed Plan을 텍스트로 보고, 진행 승인 버튼 전송.
    승인/취소 버튼은 어떤 경우에도 반드시 포함.
    """
    try:
        from mellow_link.config.settings import get_settings
        s = get_settings()
        if not getattr(s, "enable_mobile_notify", False):
            return False
        if not (getattr(s, "telegram_bot_token", "") or "").strip():
            return False
        if not (getattr(s, "telegram_chat_id", "") or "").strip():
            return False
    except Exception:
        return False

    if not proposal_id or not proposal_id.strip():
        logger.warning("[Notification] notify_evolution_plan_ready: proposal_id 없음, 전송 생략")
        return False

    target_line = f"\n<b>대상 예정:</b> {_safe_html(target_hint[:80], 80)}\n" if target_hint else ""
    plan_preview = _safe_html((plan_summary or "")[:600], 600)
    reply_markup = _build_plan_proceed_keyboard(proposal_id.strip())
    text = (
        f"📋 <b>Mellow-Link Proposed Plan (자가발전 사전 보고)</b>\n\n"
        f"ID: <code>{proposal_id[:8]}</code>\n"
        f"요청: {_safe_html(user_request[:200], 200)}\n"
        f"{target_line}\n"
        f"<b>계획 요약:</b>\n{plan_preview}\n\n"
        f"👇 진행 승인 후에만 코드 생성이 진행됩니다."
    )
    ok = send_telegram(text, reply_markup=reply_markup)
    if not ok:
        logger.warning("[Notification] Plan 보고 알림 전송 실패 (버튼 포함)")
    return ok


def notify_evolution_applied(
    proposal_id: str,
    target_file: str,
    message: str = "",
    upgrade_reason: str = "",
    user_request: str = "",
) -> bool:
    """
    결재 완료: Admin 승인 후 코드 반영 시 Telegram 알림.
    upgrade_reason: 무엇을 업그레이드했는지 (수정 사유)
    user_request: 원본 요청 내용
    """
    try:
        from mellow_link.config.settings import get_settings
        s = get_settings()
        if not getattr(s, "enable_mobile_notify", False):
            return False
    except Exception:
        return False

    reason_block = ""
    if upgrade_reason:
        reason_block = f"\n<b>📋 업그레이드 내용:</b>\n{_safe_html(upgrade_reason, 400)}\n"
    if user_request:
        reason_block += f"\n<b>요청:</b> {_safe_html(user_request, 200)}\n"

    text = (
        f"✅ <b>Mellow-Link 결재 완료 (업그레이드 적용됨)</b>\n\n"
        f"ID: <code>{proposal_id[:8]}</code>\n"
        f"<b>대상 파일:</b> <code>{_safe_html(target_file, 100)}</code>"
        f"{reason_block}\n"
        f"{message[:200] if message else '코드 반영 완료.'}"
    )
    return send_telegram(text)


def notify_autonomous_work_waiting_approval(record: Any, approval_url: Optional[str] = None) -> bool:
    """
    자율 작업 결과가 WAITING_FOR_APPROVAL 상태가 되었을 때 Admin에게 Telegram 알림.
    정보 확인용 알림 (승인 버튼 없음).
    """
    try:
        from mellow_link.config.settings import get_settings
        s = get_settings()
        if not getattr(s, "enable_mobile_notify", False):
            return False
        if not (getattr(s, "telegram_bot_token", "") or "").strip():
            return False
        if not (getattr(s, "telegram_chat_id", "") or "").strip():
            return False
    except Exception:
        return False

    record_id_full = str(getattr(record, "id", "") or "").strip()
    if not record_id_full:
        logger.warning("[Notification] notify_autonomous_work_waiting_approval: record_id 없음, 전송 생략")
        return False

    record_id = record_id_full[:16]
    raw_tools = getattr(record, "tools_created", None) or ""
    tools_created = _safe_html(str(raw_tools).strip(), 300)
    if tools_created in ("", "[]"):
        tools_created = "(없음)"
    info_collected = _safe_html(str(getattr(record, "info_collected", None) or ""), 300)
    ethics_review = _safe_html(str(getattr(record, "ethics_review", None) or ""), 200)

    text = (
        f"🤖 <b>자율 작업 완료 (확인용)</b>\n\n"
        f"ID: <code>{record_id[:8]}</code>\n\n"
        f"<b>제작된 도구:</b>\n{tools_created}\n\n"
        f"<b>수집 정보:</b>\n{info_collected or '(없음)'}\n\n"
        f"<b>윤리 검토:</b>\n{ethics_review or '-'}\n\n"
        f"ℹ️ 자율작업은 승인 없이 자동으로 진행됩니다."
    )
    # 승인 버튼 제거 - 정보 확인용 알림만 전송
    ok = send_telegram(text, reply_markup=None)
    if not ok:
        logger.warning("[Notification] 자율 작업 알림 전송 실패")
    return ok


def notify_autonomous_work_failed(
    record_id: str,
    stage: str,
    reason: str,
    traceback_preview: str = "",
    stdout_preview: str = "",
) -> bool:
    """
    자율 작업 실패 시 Admin에게 Telegram 알림.
    stage: 실패한 단계 (예: "Verdict 코드 생성", "보안 검색대(AST)", "Guardian 정밀 검수")
    stdout_preview: 타임아웃 등 시 캡처된 stdout 일부
    """
    try:
        from mellow_link.config.settings import get_settings
        s = get_settings()
        if not getattr(s, "enable_mobile_notify", False):
            return False
        if not (getattr(s, "telegram_bot_token", "") or "").strip():
            return False
        if not (getattr(s, "telegram_chat_id", "") or "").strip():
            return False
    except Exception:
        return False

    tb = _safe_html(str(traceback_preview)[:400]) if traceback_preview else ""
    stdout_block = _safe_html(str(stdout_preview)[:400]) if stdout_preview else ""
    text = (
        f"⚠️ <b>작업 실패 보고</b>\n\n"
        f"<b>실패 단계:</b> {_safe_html(stage)}\n"
        f"<b>사유:</b> {_safe_html(str(reason)[:500])}\n\n"
        f"ID: <code>{str(record_id)[:8]}</code>"
    )
    if tb:
        text += f"\n\n<pre>{tb}</pre>"
    if stdout_block:
        text += f"\n\n<b>[stdout 일부]</b>\n<pre>{stdout_block}</pre>"
    return send_telegram(text)


def notify_autonomous_work_completed(
    record_id: str,
    tool_name: str,
    success: bool,
    output_preview: str = "",
    task_summary: str = "",
) -> bool:
    """
    자율 작업 실행 완료 시 Admin에게 Telegram 알림.
    task_summary: 무엇을 했는지 (업그레이드/생성된 작업 내용)
    """
    try:
        from mellow_link.config.settings import get_settings
        s = get_settings()
        if not getattr(s, "enable_mobile_notify", False):
            return False
    except Exception:
        return False

    summary_block = ""
    if task_summary:
        summary_block = f"\n<b>📋 작업 내용:</b>\n{_safe_html(task_summary, 400)}\n"

    if success:
        text = (
            f"✅ <b>작업 완료</b>\n\n"
            f"<b>{tool_name}</b> 실행이 성공했습니다."
            f"{summary_block}\n"
            f"ID: <code>{str(record_id)[:8]}</code>"
        )
    else:
        text = (
            f"⚠️ <b>작업 완료 (실패)</b>\n\n"
            f"<b>{tool_name}</b> 실행 중 오류가 발생했습니다."
            f"{summary_block}\n"
            f"ID: <code>{str(record_id)[:8]}</code>\n"
            f"<pre>{_safe_html(str(output_preview)[:300])}</pre>"
        )
    return send_telegram(text)


def notify_autonomous_goal_registered(goal_title: str) -> bool:
    """
    🔔 자율 목표가 등록되었을 때 Admin에게 Telegram 알림.
    "새로운 자율 목표가 등록되었습니다: [목표명]"
    """
    try:
        from mellow_link.config.settings import get_settings
        s = get_settings()
        if not getattr(s, "enable_mobile_notify", False):
            return False
    except Exception:
        return False
    title = (goal_title or "").strip() or "이름 없음"
    text = f"🎯 <b>새로운 자율 목표가 등록되었습니다</b>\n\n{_safe_html(title, 300)}"
    return send_telegram(text)


# CHAT 이벤트 중 알림에서 제외·요약할 도구 (단순 읽기/목록 조회)
_READ_ONLY_TOOLS = frozenset({"read_file", "list_directory"})


def _is_read_only_chat(ev: Dict[str, Any]) -> bool:
    """CHAT 이벤트가 read_file/list_directory만 사용했는지 여부."""
    tools = ev.get("used_tools") or []
    return bool(tools) and set(tools) <= _READ_ONLY_TOOLS


def _is_important_evolution(ev: Dict[str, Any]) -> bool:
    """시스템 핵심 파일 또는 fs_util.py 수정 여부. True면 상세 보기(Detail) 버튼 포함."""
    target = (ev.get("target_file") or "").strip()
    if not target:
        return False
    t = target.replace("\\", "/").lower()
    return "fs_util.py" in t or "/core/" in t or "core\\" in t


def send_agent_flow_update(
    events: Optional[List[Dict[str, Any]]] = None,
    since_minutes: int = 30,
    limit: int = 20,
) -> bool:
    """
    Guardian 판결 내역, 에이전트 의도(Intention), 자율 목표 생성 이벤트를
    포맷팅하여 텔레그램으로 즉시 전송.
    - 단순 read/list 작업은 상세 항목에서 제외하고 건수만 요약.
    - 시스템 핵심 파일 또는 fs_util.py 수정 시에만 상세 보기(Detail) 버튼 포함.

    Args:
        events: get_monitor_flow_timeline() 형식의 이벤트 리스트.
                None이면 최근 데이터를 DB에서 조회.
        since_minutes: events가 None일 때 조회 기간(분)
        limit: events가 None일 때 조회 최대 건수

    Returns:
        전송 성공 여부
    """
    try:
        from mellow_link.config.settings import get_settings
        s = get_settings()
        if not getattr(s, "enable_mobile_notify", False):
            return False
        if not (getattr(s, "telegram_bot_token", "") or "").strip():
            return False
        if not (getattr(s, "telegram_chat_id", "") or "").strip():
            return False
    except Exception:
        return False

    if events is None:
        try:
            from mellow_link.infra.memory_database import get_memory_db
            db = get_memory_db()
            events = db.get_monitor_flow_timeline(since_minutes=since_minutes, limit=limit)
        except Exception as e:
            logger.warning("[Notification] send_agent_flow_update: DB 조회 실패: %s", e)
            return False

    if not events:
        return True

    lines: List[str] = ["📊 <b>Agent Flow 업데이트</b>\n"]
    read_only_count = 0
    first_important_evolution_id: Optional[str] = None

    for ev in events:
        ev_type = ev.get("type", "")
        raw_time = ev.get("time") or ""
        time_str = raw_time[11:19] if len(raw_time) >= 19 else (raw_time[:8] if raw_time else "")

        if ev_type == "CHAT":
            if _is_read_only_chat(ev):
                read_only_count += 1
                continue
            intent = _safe_html(str(ev.get("task_intent", "") or ""), 200)
            is_ok = ev.get("is_success", False)
            tools = ev.get("used_tools") or []
            tools_str = ", ".join(str(t) for t in tools[:5]) if tools else "(없음)"
            status_icon = "✅" if is_ok else "❌"
            lines.append(
                f"<b>💬 의도</b> {status_icon} [{time_str}]\n"
                f"  {intent}\n"
                f"  도구: <code>{_safe_html(tools_str, 80)}</code>\n"
            )

        elif ev_type == "EVOLUTION":
            if first_important_evolution_id is None and _is_important_evolution(ev):
                first_important_evolution_id = ev.get("id")
            approved = ev.get("is_approved", False)
            target = _safe_html(str(ev.get("target_file", "") or ""), 60)
            reason = _safe_html(str(ev.get("reason", "") or ""), 150)
            critique = (ev.get("critique") or "").strip()
            status_icon = "✅ 승인" if approved else "❌ 반려"
            lines.append(
                f"<b>🏛️ Guardian</b> {status_icon} [{time_str}]\n"
                f"  대상: <code>{target}</code>\n"
                f"  사유: {reason}\n"
            )
            if critique and not approved:
                critique_safe = _safe_html(critique, 400)
                lines.append(
                    f"  ⚠️ <b>반려 사유:</b>\n"
                    f"  <blockquote>{critique_safe}</blockquote>\n"
                )
            elif critique:
                lines.append(f"  검토 의견: {_safe_html(critique, 200)}\n")

        elif ev_type == "GOAL":
            title = _safe_html(str(ev.get("title", "") or ""), 150)
            desc = _safe_html(str(ev.get("description", "") or ""), 100)
            lines.append(
                f"<b>🎯 자율 목표</b> [{time_str}]\n"
                f"  {title}\n"
                f"  {desc}\n"
            )

    if read_only_count > 0:
        lines.append(f"\n📖 읽기/목록 조회 {read_only_count}건 (요약)")

    text = "\n".join(lines)[:4096]
    reply_markup: Optional[Dict[str, Any]] = None
    if first_important_evolution_id:
        try:
            from mellow_link.config.settings import get_settings
            base = (getattr(get_settings(), "public_base_url", "") or "").strip().rstrip("/")
            if base:
                detail_url = f"{base}/monitor/flow/detail/{first_important_evolution_id}"
                reply_markup = {
                    "inline_keyboard": [[{"text": "📋 상세 보기 (Detail)", "url": detail_url}]]
                }
        except Exception:
            pass
    ok = send_telegram(text, reply_markup=reply_markup)
    if not ok:
        logger.warning("[Notification] send_agent_flow_update 전송 실패")
    return ok
