"""
민감정보 마스킹 검증: 로그/이벤트 파이프라인에서 KEY/SECRET/TOKEN 등이 노출되지 않는지 테스트.
"""
import logging
import unittest

from mellow_link.utils.sensitive_redact import (
    SensitiveRedactingFormatter,
    redact_sensitive_data,
    redact_dict_recursive,
)


# 테스트에 쓸 가짜 키 (저장/출력 결과에 원문이 남으면 안 됨)
FAKE_OPENAI_KEY = "sk-fake-0123456789abcdef0123456789abcdef"
FAKE_OPENAI_ENV_LINE = f"OPENAI_API_KEY={FAKE_OPENAI_KEY}"
FAKE_ANTHROPIC_LINE = "ANTHROPIC_API_KEY=sk-ant-fake-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
FAKE_AUTH_HEADER = "Authorization: Bearer sk-secret-token-xxxxxxxxxxxxxxxxxxxxxxxx"


class TestSensitiveRedactString(unittest.TestCase):
    """redact_sensitive_data: OPENAI/ANTHROPIC/Authorization 등 포함 문자열 마스킹."""

    def test_openai_api_key_env_style_redacted(self):
        text = FAKE_OPENAI_ENV_LINE
        out = redact_sensitive_data(text)
        self.assertNotIn(FAKE_OPENAI_KEY, out, "원문 API 키가 출력에 남으면 안 됨")
        self.assertIn("[REDACTED]", out, "마스킹 표시가 있어야 함")

    def test_openai_key_alone_redacted(self):
        out = redact_sensitive_data(f"key is {FAKE_OPENAI_KEY} end")
        self.assertNotIn("sk-fake-", out)
        self.assertTrue("[REDACTED" in out, "마스킹 표시([REDACTED] 또는 [REDACTED_API_KEY])가 있어야 함")

    def test_anthropic_env_style_redacted(self):
        out = redact_sensitive_data(FAKE_ANTHROPIC_LINE)
        self.assertNotIn("sk-ant-fake-", out)
        self.assertIn("[REDACTED]", out)

    def test_authorization_bearer_redacted(self):
        out = redact_sensitive_data(FAKE_AUTH_HEADER)
        self.assertNotIn("sk-secret-token-", out)
        self.assertIn("[REDACTED]", out)

    def test_log_message_with_openai_key_redacted(self):
        msg = f"Connection failed: {FAKE_OPENAI_ENV_LINE} and retry"
        out = redact_sensitive_data(msg)
        self.assertNotIn(FAKE_OPENAI_KEY, out)
        self.assertIn("[REDACTED]", out)


class TestSensitiveRedactDict(unittest.TestCase):
    """이벤트 페이로드 등 dict 재귀 리다렉션: 저장/출력 결과에 원문이 없어야 함."""

    def test_event_payload_message_redacted(self):
        payload = {"message": FAKE_OPENAI_ENV_LINE, "level": "error"}
        out = redact_dict_recursive(payload)
        self.assertNotIn(FAKE_OPENAI_KEY, str(out))
        self.assertIn("[REDACTED]", out["message"])

    def test_nested_payload_redacted(self):
        payload = {"data": {"log": f"Error: {FAKE_OPENAI_ENV_LINE}"}}
        out = redact_dict_recursive(payload)
        self.assertNotIn(FAKE_OPENAI_KEY, str(out))
        self.assertIn("[REDACTED]", out["data"]["log"])


class TestSensitiveRedactingFormatter(unittest.TestCase):
    """로거 포맷 결과(메시지 + traceback)에 민감정보가 노출되지 않음."""

    def test_formatter_redacts_log_message(self):
        formatter = SensitiveRedactingFormatter(
            fmt="%(name)s | %(message)s",
            datefmt="%Y-%m-%d",
        )
        record = logging.LogRecord(
            name="test",
            level=logging.ERROR,
            pathname="",
            lineno=0,
            msg=FAKE_OPENAI_ENV_LINE,
            args=(),
            exc_info=None,
        )
        formatted = formatter.format(record)
        self.assertNotIn(FAKE_OPENAI_KEY, formatted)
        self.assertIn("[REDACTED]", formatted)

    def test_formatter_redacts_exception_message_in_traceback(self):
        """예외 메시지에 키가 들어가도 포맷 결과에는 원문이 없어야 함."""
        formatter = SensitiveRedactingFormatter(
            fmt="%(name)s | %(message)s",
            datefmt="%Y-%m-%d",
        )
        try:
            raise ValueError(FAKE_OPENAI_ENV_LINE)
        except ValueError:
            import sys
            record = logging.LogRecord(
                name="test",
                level=logging.ERROR,
                pathname="",
                lineno=0,
                msg="Error occurred",
                args=(),
                exc_info=sys.exc_info(),
            )
        formatted = formatter.format(record)
        self.assertNotIn(FAKE_OPENAI_KEY, formatted, "traceback/exception 메시지에 원문이 남으면 안 됨")
        self.assertIn("[REDACTED]", formatted)


class TestRunEventsRedactionIntegration(unittest.TestCase):
    """run_events 경로: emit_event에 넘기는 페이로드가 리다렉션되는지 (함수 단위)."""

    def test_run_events_redact_sensitive_data_removes_openai_key(self):
        from mellow_link.infra.run_events import redact_sensitive_data as run_events_redact
        out = run_events_redact(FAKE_OPENAI_ENV_LINE)
        self.assertNotIn(FAKE_OPENAI_KEY, out)
        self.assertIn("[REDACTED]", out)

    def test_run_events_redact_dict_recursive_on_payload(self):
        from mellow_link.infra.run_events import _redact_dict_recursive
        payload = {"message": f"Config: {FAKE_OPENAI_ENV_LINE}", "type": "log"}
        out = _redact_dict_recursive(payload)
        self.assertNotIn(FAKE_OPENAI_KEY, str(out))
        self.assertIn("[REDACTED]", out["message"])
