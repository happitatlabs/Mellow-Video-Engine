"""
Mellow-Link - Telegram Router

Endpoints: /webhooks/telegram
"""

import logging

from fastapi import APIRouter, Depends, Request, BackgroundTasks
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from mellow_link import app_state
from mellow_link.config import get_settings
from mellow_link.infra import get_db, User, UserRole, AgentFolder, ChatSession

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Webhooks"])


# =============================================================================
# Telegram Chat Session Helper
# =============================================================================

def get_or_create_telegram_session(db: Session, admin_user: User) -> ChatSession:
    """
    Admin 유저의 Telegram 폴더와 오늘 날짜 세션을 조회/생성.
    텔레그램 대화는 날짜별 세션으로 관리 (예: "Telegram 2026-02-12").
    """
    from datetime import datetime

    # 1. Telegram 폴더 조회/생성
    telegram_folder = db.query(AgentFolder).filter(
        AgentFolder.user_id == admin_user.id,
        AgentFolder.name == "Telegram",
        AgentFolder.is_active == True
    ).first()

    if not telegram_folder:
        telegram_folder = AgentFolder(
            user_id=admin_user.id,
            name="Telegram",
            icon="📱",
            system_prompt="친절한 AI 비서입니다.",
            use_rag=True,
            rag_collection_name=None,
            is_active=True
        )
        db.add(telegram_folder)
        db.commit()
        db.refresh(telegram_folder)

    # 2. 오늘 날짜 세션 조회/생성
    today_str = datetime.now().strftime("%Y-%m-%d")
    session_title = f"Telegram {today_str}"

    existing_session = db.query(ChatSession).filter(
        ChatSession.user_id == admin_user.id,
        ChatSession.folder_id == telegram_folder.id,
        ChatSession.title == session_title,
        ChatSession.is_active == True
    ).first()

    if existing_session:
        return existing_session

    new_session = ChatSession(
        user_id=admin_user.id,
        folder_id=telegram_folder.id,
        title=session_title,
        is_active=True
    )
    db.add(new_session)
    db.commit()
    db.refresh(new_session)
    return new_session


async def _handle_telegram_chat(
    chat_id: str,
    question: str,
    session: ChatSession,
    placeholder_message_id: int
):
    """
    텔레그램 채팅 백그라운드 핸들러.
    히스토리 로드 -> run_agent -> DB 저장 -> 텔레그램 응답 전송.
    """
    from mellow_link.infra.database import ChatMessage
    from mellow_link.services.notification_service import telegram_edit_message, send_telegram_long

    db = next(get_db())
    try:
        # 1. 히스토리 로드
        previous_messages = db.query(ChatMessage).filter(
            ChatMessage.session_id == session.id
        ).order_by(ChatMessage.timestamp.asc()).all()

        history_messages = [
            {"role": m.role, "content": m.content}
            for m in previous_messages
            if getattr(m, "role", None) and getattr(m, "content", None)
        ]

        # 2. 사용자 메시지 저장
        user_msg = ChatMessage(
            session_id=session.id,
            role="user",
            content=question
        )
        db.add(user_msg)
        db.commit()

        # 3. 시스템 프롬프트 구성
        system_prompt = "친절한 AI 비서입니다. 한국어로 답변합니다."
        context_messages = [{"role": "system", "content": system_prompt}] + history_messages

        # 4. Agent 실행
        answer = ""
        try:
            if app_state.orchestrator and hasattr(app_state.orchestrator, 'agent') and app_state.orchestrator.agent:
                agent_result = await app_state.orchestrator.run_agent(
                    question, history=context_messages, is_admin=True
                )
                answer = agent_result.answer if agent_result and hasattr(agent_result, 'answer') else ""
            elif app_state.llm_service and app_state.llm_service.is_available():
                answer = await app_state.llm_service.generate(question, context_id="telegram")
            else:
                answer = "[오류] LLM 서비스가 사용 불가능합니다."
        except Exception as agent_err:
            logger.error(f"[TelegramChat] Agent error: {agent_err}", exc_info=True)
            answer = f"[오류] {str(agent_err)[:200]}"

        if not answer:
            answer = "[응답 없음] 에이전트가 응답을 생성하지 못했습니다."

        # 5. 어시스턴트 메시지 저장
        asst_msg = ChatMessage(
            session_id=session.id,
            role="assistant",
            content=answer
        )
        db.add(asst_msg)
        db.commit()

        # 6. 텔레그램 응답 전송 (플레이스홀더 편집)
        try:
            if len(answer) > 4000:
                send_telegram_long(answer, chat_id_override=chat_id)
                telegram_edit_message(chat_id, placeholder_message_id, "✅ 응답 완료 (장문 분할 전송됨)")
            else:
                telegram_edit_message(chat_id, placeholder_message_id, answer)
        except Exception as send_err:
            logger.error(f"[TelegramChat] Send error: {send_err}")
            try:
                telegram_edit_message(chat_id, placeholder_message_id, f"[전송 오류] {str(send_err)[:200]}")
            except Exception:
                pass
    finally:
        db.close()


# =============================================================================
# Webhook Endpoint
# =============================================================================

@router.post("/webhooks/telegram")
async def telegram_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db)
):
    """
    텔레그램 봇 웹훅.
    - X-Telegram-Bot-Api-Secret-Token 헤더 검증 (TELEGRAM_WEBHOOK_SECRET 설정 시 필수)
    - callback_query / message 처리. Admin 채팅(TELEGRAM_CHAT_ID)에서 온 것만 처리.
    """
    s = get_settings()
    secret = (s.telegram_webhook_secret or "").strip()
    if secret:
        received = (request.headers.get("X-Telegram-Bot-Api-Secret-Token") or "").strip()
        if received != secret:
            logger.warning("[Telegram] Webhook secret mismatch or missing header")
            return JSONResponse(content={"ok": False, "error": "Unauthorized"}, status_code=403)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse(content={"ok": True}, status_code=200)

    expected_chat = (s.telegram_chat_id or "").strip()

    # ----- 일반 메시지 (명령어) 처리 -----
    msg = body.get("message")
    if msg:
        chat = msg.get("chat") or {}
        chat_id = str(chat.get("id", ""))
        text = (msg.get("text") or "").strip()
        if expected_chat and chat_id != expected_chat:
            return JSONResponse(content={"ok": True}, status_code=200)
        if not text:
            return JSONResponse(content={"ok": True}, status_code=200)

        from mellow_link.services.notification_service import send_telegram

        # /help
        if text in ("/help", "/도움말", "help"):
            help_text = (
                "📌 <b>Mellow-Link 텔레그램 명령어</b>\n\n"
                "/tick - 자율 틱 1회 실행\n"
                "/evolution &lt;요청&gt; - 자가발전 (코드 수정 요청)\n"
                "  예: /evolution workspace/README.md에 설명 추가\n"
                "  검수 거부 시 자동 재시도 후 통과하면 승인 요청\n"
                "/evolution_proceed &lt;proposal_id&gt; - plan_pending 제안 수동 진행\n"
                "  예: /evolution_proceed abc12345-xxxx-xxxx\n"
                "/help - 이 도움말"
            )
            send_telegram(help_text, chat_id_override=chat_id)
            return JSONResponse(content={"ok": True}, status_code=200)

        # /tick
        if text in ("/tick", "/틱", "틱", "자율 틱"):
            try:
                from mellow_link.core.autonomous_agent import run_autonomous_tick
                record = await run_autonomous_tick(shutdown_event=app_state.shutdown_event)
                if record is None:
                    send_telegram("⏭️ 자율 틱 스킵됨 (plan=skip 또는 조건 미충족)", chat_id_override=chat_id)
                else:
                    st = record.status
                    rid = (record.id or "")[:8]
                    send_telegram(
                        f"✅ 자율 틱 완료\n상태: {st}\nID: <code>{rid}</code>\n"
                        f"승인 대기 시 곧 알림이 옵니다.",
                        chat_id_override=chat_id,
                    )
            except Exception as e:
                send_telegram(f"❌ 자율 틱 실패: {str(e)[:200]}", chat_id_override=chat_id)
            return JSONResponse(content={"ok": True}, status_code=200)

        # /evolution <요청>
        if text.startswith("/evolution ") or text.startswith("/자가발전 "):
            user_request = text.split(maxsplit=1)[1].strip() if " " in text else ""
            if not user_request:
                send_telegram("사용법: /evolution &lt;수정 요청 내용&gt;", chat_id_override=chat_id)
                return JSONResponse(content={"ok": True}, status_code=200)
            try:
                from mellow_link.core.evolution_facade import EvolutionFacade
                resp = await EvolutionFacade.run_cycle(user_request)
                if resp.status == "DISABLED" and resp.disabled_reason:
                    send_telegram(
                        f"🔒 Evolution 비활성화\n{resp.disabled_reason.message[:300]}",
                        chat_id_override=chat_id,
                    )
                elif resp.error and "SKIP_DUPLICATE" in (resp.error or ""):
                    send_telegram(
                        "이미 유사한 제안이 대기 중이어서 다른 작업을 준비합니다.",
                        chat_id_override=chat_id,
                    )
                elif resp.error and "LOW_ROI_SUGGESTION" in (resp.error or ""):
                    msg_text = (resp.error or "").split("LOW_ROI_SUGGESTION:", 1)[-1].strip()
                    send_telegram(msg_text or "이 판은 판돈 대비 수익률이 낮습니다.", chat_id_override=chat_id)
                elif resp.plan_pending:
                    pid = (resp.proposal_id or "")[:8]
                    send_telegram(
                        f"📋 Proposed Plan 보고됨 (ID: <code>{pid}</code>)\n"
                        "진행 승인 버튼을 눌러 코드 생성을 진행하세요.",
                        chat_id_override=chat_id,
                    )
                else:
                    pid = (resp.proposal_id or "")[:8]
                    approved = "✅ 검수 승인" if resp.audit_approved else "⚠️ 검수 미승인"
                    target = (resp.verdict_target_file or "")[:80] if resp.verdict_target_file else "-"
                    send_telegram(
                        f"📋 자가발전 완료\nID: <code>{pid}</code>\n{approved}\n"
                        f"대상: {target}\nUI에서 결재 후 적용하세요.",
                        chat_id_override=chat_id,
                    )
            except Exception as e:
                send_telegram(f"❌ 자가발전 실패: {str(e)[:200]}", chat_id_override=chat_id)
            return JSONResponse(content={"ok": True}, status_code=200)

        # /evolution_proceed <proposal_id>
        if text.startswith("/evolution_proceed ") or text.startswith("/진행 "):
            parts = text.split(maxsplit=1)
            proposal_id = (parts[1].strip() if len(parts) > 1 else "").strip()
            if not proposal_id:
                send_telegram("사용법: /evolution_proceed &lt;proposal_id&gt;", chat_id_override=chat_id)
                return JSONResponse(content={"ok": True}, status_code=200)
            try:
                send_telegram(f"ID: <code>{proposal_id[:8]}</code> 건에 대한 수술(Verdict)을 강제로 시작합니다...", chat_id_override=chat_id)
                from mellow_link.core.evolution_facade import EvolutionFacade
                resp = await EvolutionFacade.proceed_from_plan(proposal_id)
                if resp is None:
                    send_telegram("❌ 제안서를 찾을 수 없거나 계획 대기 상태가 아닙니다.", chat_id_override=chat_id)
                    return JSONResponse(content={"ok": True}, status_code=200)
                if resp.status == "DISABLED" and resp.disabled_reason:
                    send_telegram(f"🔒 Evolution 비활성화\n{resp.disabled_reason.message[:300]}", chat_id_override=chat_id)
                    return JSONResponse(content={"ok": True}, status_code=200)
                if resp.error and "pre_flight_check" in (resp.error or ""):
                    send_telegram(f"⚠️ pre_flight_check 실패\n{(resp.error or '')[:400]}", chat_id_override=chat_id)
                elif resp.audit_approved:
                    target = (resp.verdict_target_file or "")[:80] if resp.verdict_target_file else "-"
                    send_telegram(
                        f"✅ Verdict·Audit 완료\nID: <code>{proposal_id[:8]}</code>\n"
                        f"대상: {target}\n결재 보고서가 곧 도착합니다.",
                        chat_id_override=chat_id,
                    )
                else:
                    send_telegram(
                        f"⚠️ 검수 미승인\nID: <code>{proposal_id[:8]}</code>\n{(resp.error or '')[:300]}",
                        chat_id_override=chat_id,
                    )
            except Exception as e:
                send_telegram(f"❌ 진행 실패: {str(e)[:300]}", chat_id_override=chat_id)
            return JSONResponse(content={"ok": True}, status_code=200)

        # 자유 텍스트 → AI 채팅
        if not text.startswith("/"):
            try:
                from mellow_link.services.notification_service import send_telegram_and_get_message_id

                admin_user = db.query(User).filter(User.role == UserRole.ADMIN.value).first()
                if not admin_user:
                    send_telegram("❌ Admin 유저를 찾을 수 없습니다.", chat_id_override=chat_id)
                    return JSONResponse(content={"ok": True}, status_code=200)

                telegram_session = get_or_create_telegram_session(db, admin_user)

                placeholder_msg_id = send_telegram_and_get_message_id(
                    "🤔 생각 중...",
                    chat_id_override=chat_id
                )

                if placeholder_msg_id:
                    background_tasks.add_task(
                        _handle_telegram_chat,
                        chat_id,
                        text,
                        telegram_session,
                        placeholder_msg_id
                    )
                else:
                    send_telegram("❌ 메시지 전송 실패.", chat_id_override=chat_id)

            except Exception as chat_err:
                logger.error(f"[TelegramWebhook] Chat routing failed: {chat_err}", exc_info=True)
                send_telegram(f"❌ 채팅 처리 중 오류: {str(chat_err)[:200]}", chat_id_override=chat_id)

            return JSONResponse(content={"ok": True}, status_code=200)

        # 알 수 없는 명령
        send_telegram("알 수 없는 명령입니다. /help 로 도움말을 확인하세요.", chat_id_override=chat_id)
        return JSONResponse(content={"ok": True}, status_code=200)

    # ----- 인라인 버튼 (callback_query) 처리 -----
    cq = body.get("callback_query")
    if not cq:
        return JSONResponse(content={"ok": True}, status_code=200)

    callback_id = cq.get("id")
    data = (cq.get("data") or "").strip()
    cq_msg = cq.get("message") or {}
    chat = cq_msg.get("chat") or {}
    chat_id = str(chat.get("id", ""))
    message_id = cq_msg.get("message_id")

    if expected_chat and chat_id != expected_chat:
        logger.warning("[Telegram] Callback from unknown chat_id=%s", chat_id)
        return JSONResponse(content={"ok": True}, status_code=200)

    if not data or ":" not in data:
        from mellow_link.services.notification_service import telegram_answer_callback
        telegram_answer_callback(callback_id, "잘못된 요청입니다.")
        return JSONResponse(content={"ok": True}, status_code=200)

    action, target_id = data.split(":", 1)
    target_id = target_id.strip()
    if not target_id:
        from mellow_link.services.notification_service import telegram_answer_callback
        telegram_answer_callback(callback_id, "ID가 없습니다.")
        return JSONResponse(content={"ok": True}, status_code=200)

    from mellow_link.services.notification_service import (
        telegram_answer_callback,
        telegram_edit_message,
    )

    new_text = ""

    # ----- 자가발전 계획 진행 승인/취소 -----
    if action == "evolve_plan_approve":
        from mellow_link.core.evolution_facade import EvolutionFacade
        resp = await EvolutionFacade.proceed_from_plan(target_id)
        if resp and resp.status == "DISABLED" and resp.disabled_reason:
            telegram_answer_callback(callback_id, "Evolution 비활성화 상태입니다.")
            new_text = f"🔒 <b>Evolution 비활성화</b>\n\n{resp.disabled_reason.message[:200]}"
        elif resp and resp.audit_approved:
            telegram_answer_callback(callback_id, "진행 승인됨.")
            new_text = "✅ <b>계획 진행 승인됨</b>\n\nVerdict·Audit 완료. 결재 보고서를 확인하세요."
        elif resp and resp.error:
            telegram_answer_callback(callback_id, f"실패: {(resp.error or '')[:100]}")
            new_text = f"⚠️ <b>진행 실패</b>\n\n{(resp.error or '')[:300]}"
        else:
            telegram_answer_callback(callback_id, "제안서를 찾을 수 없습니다.")
            new_text = "❌ <b>제안서를 찾을 수 없거나 이미 처리됨</b>"
    elif action == "evolve_plan_reject":
        from mellow_link.core.evolution_facade import EvolutionFacade
        resp = EvolutionFacade.reject_proposal(target_id)
        if resp.status == "DISABLED" and resp.disabled_reason:
            telegram_answer_callback(callback_id, "Evolution 비활성화 상태입니다.")
            new_text = f"🔒 <b>Evolution 비활성화</b>\n\n{resp.disabled_reason.message[:200]}"
        else:
            telegram_answer_callback(callback_id, "계획 취소되었습니다.")
            new_text = "❌ <b>계획 취소됨</b>\n\n적용하지 않았습니다."

    # ----- 자가발전(Evolution) 승인/거부 -----
    elif action == "evolve_approve":
        from mellow_link.core.evolution_facade import EvolutionFacade
        from mellow_link.services.notification_service import notify_evolution_applied
        resp = EvolutionFacade.apply_from_proposal(target_id)
        ok = resp.apply_ok if resp.apply_ok is not None else False
        apply_msg = resp.apply_message or resp.error or ""
        if ok:
            telegram_answer_callback(callback_id, "적용 완료!")
            new_text = "✅ <b>자가발전 적용 완료</b>\n\n코드가 반영되었습니다."
            try:
                from pathlib import Path
                import json
                base = Path(__file__).resolve().parent.parent
                ledger = base / "logs" / "evolution_proposals" / f"{target_id}.json"
                if ledger.exists():
                    ledger_data = json.loads(ledger.read_text(encoding="utf-8"))
                    notify_evolution_applied(
                        target_id, ledger_data.get("verdict_target_file", ""), apply_msg,
                        upgrade_reason=(ledger_data.get("verdict_reason", "") or "")[:500],
                        user_request=(ledger_data.get("user_request", "") or "")[:300],
                    )
            except Exception:
                pass
        else:
            telegram_answer_callback(callback_id, f"적용 실패: {apply_msg[:100]}")
            new_text = f"❌ <b>적용 실패</b>\n\n{apply_msg[:300]}"
    elif action == "evolve_reject":
        from mellow_link.core.evolution_facade import EvolutionFacade
        resp = EvolutionFacade.reject_proposal(target_id)
        if resp.status == "DISABLED" and resp.disabled_reason:
            telegram_answer_callback(callback_id, "Evolution 비활성화 상태입니다.")
            new_text = f"🔒 <b>Evolution 비활성화</b>\n\n{resp.disabled_reason.message[:200]}"
        else:
            telegram_answer_callback(callback_id, "거부되었습니다.")
            new_text = "❌ <b>자가발전 거부됨</b>\n\n적용하지 않았습니다."

    # ----- 자율 작업(Autonomous) 승인/거부 -----
    elif action == "approve":
        from mellow_link.infra.memory_database import get_memory_db
        from mellow_link.core.autonomous_agent import execute_approved_work
        mem_db = get_memory_db()
        record = mem_db.get_autonomous_work_result_by_id(target_id)
        if not record:
            telegram_answer_callback(callback_id, "해당 작업을 찾을 수 없습니다.")
            return JSONResponse(content={"ok": True}, status_code=200)
        if record.status != "WAITING_FOR_APPROVAL":
            telegram_answer_callback(callback_id, f"이미 처리됨: {record.status}")
            return JSONResponse(content={"ok": True}, status_code=200)
        success, _ = await execute_approved_work(target_id)
        telegram_answer_callback(callback_id, "승인되었습니다. 작업을 시작합니다!")
        new_text = "✅ <b>승인되었습니다. 작업을 시작합니다!</b>"
    elif action == "reject":
        from mellow_link.infra.memory_database import get_memory_db
        mem_db = get_memory_db()
        mem_db.update_autonomous_work_status(target_id, "REJECTED")
        telegram_answer_callback(callback_id, "거부되었습니다.")
        new_text = "❌ <b>거부되었습니다.</b>"

    else:
        telegram_answer_callback(callback_id, "알 수 없는 동작입니다.")
        return JSONResponse(content={"ok": True}, status_code=200)

    if chat_id and message_id and new_text:
        telegram_edit_message(chat_id, message_id, new_text)

    return JSONResponse(content={"ok": True}, status_code=200)
