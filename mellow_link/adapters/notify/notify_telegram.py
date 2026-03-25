"""Telegram API 기반 알림 어댑터 (ENABLE_TELEGRAM=1일 때)."""
import json
import logging
import urllib.parse
import urllib.request
from typing import Any, Dict, Optional

from mellow_link.adapters.notify.base import NotifyAdapter

logger = logging.getLogger(__name__)


class TelegramNotifyAdapter(NotifyAdapter):
    """api.telegram.org를 사용한 실제 Telegram 전송."""

    def _get_token_and_chat_id(
        self,
        chat_id_override: Optional[str] = None,
    ) -> tuple:
        from mellow_link.config.settings import get_settings
        s = get_settings()
        token = (s.telegram_bot_token or "").strip()
        chat_id = (chat_id_override or (s.telegram_chat_id or "")).strip()
        return token, chat_id

    def send_telegram(
        self,
        text: str,
        parse_mode: str = "HTML",
        reply_markup: Optional[Dict[str, Any]] = None,
        chat_id_override: Optional[str] = None,
    ) -> bool:
        token, chat_id = self._get_token_and_chat_id(chat_id_override)
        if not token or not chat_id:
            logger.debug("[Notify] Telegram not configured (TELEGRAM_BOT_TOKEN/CHAT_ID)")
            return False
        try:
            url = f"https://api.telegram.org/bot{token}/sendMessage"
            body = {
                "chat_id": chat_id,
                "text": text[:4096],
                "parse_mode": parse_mode,
                "disable_web_page_preview": True,
            }
            if reply_markup:
                body["reply_markup"] = json.dumps(reply_markup)
            data = urllib.parse.urlencode(body).encode("utf-8")
            req = urllib.request.Request(url, data=data, method="POST")
            req.add_header("Content-Type", "application/x-www-form-urlencoded")
            with urllib.request.urlopen(req, timeout=10) as resp:
                if resp.status == 200:
                    logger.info("[Notify] Telegram sent successfully")
                    return True
                return False
        except Exception as e:
            logger.warning("[Notify] Telegram send failed: %s", e)
            return False

    def send_telegram_and_get_message_id(
        self,
        text: str,
        parse_mode: str = "HTML",
        reply_markup: Optional[Dict[str, Any]] = None,
        chat_id_override: Optional[str] = None,
    ) -> Optional[int]:
        token, chat_id = self._get_token_and_chat_id(chat_id_override)
        if not token or not chat_id:
            logger.debug("[Notify] Telegram not configured (TELEGRAM_BOT_TOKEN/CHAT_ID)")
            return None
        try:
            url = f"https://api.telegram.org/bot{token}/sendMessage"
            body = {
                "chat_id": chat_id,
                "text": text[:4096],
                "parse_mode": parse_mode,
                "disable_web_page_preview": True,
            }
            if reply_markup:
                body["reply_markup"] = json.dumps(reply_markup)
            data = urllib.parse.urlencode(body).encode("utf-8")
            req = urllib.request.Request(url, data=data, method="POST")
            req.add_header("Content-Type", "application/x-www-form-urlencoded")
            with urllib.request.urlopen(req, timeout=10) as resp:
                if resp.status == 200:
                    response_data = json.loads(resp.read().decode("utf-8"))
                    if response_data.get("ok") and "result" in response_data:
                        message_id = response_data["result"].get("message_id")
                        logger.info("[Notify] Telegram sent successfully, message_id=%s", message_id)
                        return message_id
                return None
        except Exception as e:
            logger.warning("[Notify] Telegram send failed: %s", e)
            return None
