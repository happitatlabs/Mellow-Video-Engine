"""
SecurityManager difficulty tier tests (EASY / NORMAL / HARD).

목표:
  - 설정값(SECURITY_LEVEL)에 따라 파일/명령/아웃바운드 게이트가 정확히 달라지는지 검증.
  - 실제 파일 쓰기/삭제는 환경에 따라 권한 이슈가 있어, 가능한 한 "resolve 단계"에서 검증한다.
"""

import os
import unittest
from pathlib import Path

from mellow_link.core.security_manager import SecurityManager, SecurityBlocked
import importlib
import sys


_SANDBOX_ROOT = Path(__file__).resolve().parents[1]  # .../mellow_link


class TestSecurityManagerTiers(unittest.TestCase):
    def test_easy_allows_write_anywhere_in_sandbox(self):
        sm = SecurityManager(level="EASY", sandbox_root=_SANDBOX_ROOT)
        # protected 영역(core)도 EASY에서는 차단하지 않는다 (개발 편의성)
        p = sm.resolve_for_write("core/_ez_tmp.txt", content="hello")
        self.assertTrue(str(p).lower().endswith("_ez_tmp.txt"))

    def test_normal_blocks_protected_write_with_proposal(self):
        sm = SecurityManager(level="NORMAL", sandbox_root=_SANDBOX_ROOT)
        with self.assertRaises(SecurityBlocked) as ctx:
            sm.resolve_for_write("core/_should_be_blocked.txt", content="x")
        e = ctx.exception
        self.assertIsNotNone(getattr(e, "proposal_path", None))
        self.assertTrue(Path(e.proposal_path).exists())
        self.assertIn("직접 파일 쓰기 불가", str(e))

    def test_normal_blocks_root_critical_assets(self):
        sm = SecurityManager(level="NORMAL", sandbox_root=_SANDBOX_ROOT)
        for p in ("main.py", "__init__.py", ".env"):
            with self.assertRaises(SecurityBlocked) as ctx:
                sm.resolve_for_write(p, content="x")
            e = ctx.exception
            self.assertIsNotNone(getattr(e, "proposal_path", None))
            self.assertTrue(Path(e.proposal_path).exists())

        # .env 변종도 직접 수정 금지
        for p in (".env.local", ".env.prod", ".env.development"):
            with self.assertRaises(SecurityBlocked) as ctx:
                sm.resolve_for_write(p, content="x")
            e = ctx.exception
            self.assertIsNotNone(getattr(e, "proposal_path", None))
            self.assertTrue(Path(e.proposal_path).exists())

        # prompts/ 디렉토리도 직접 수정 금지
        with self.assertRaises(SecurityBlocked) as ctx:
            sm.resolve_for_write("prompts/_should_be_blocked.txt", content="x")
        self.assertIsNotNone(getattr(ctx.exception, "proposal_path", None))

    def test_hard_blocks_protected_write_with_proposal(self):
        sm = SecurityManager(level="HARD", sandbox_root=_SANDBOX_ROOT)
        with self.assertRaises(SecurityBlocked) as ctx:
            sm.resolve_for_write("config/_should_be_blocked.txt", content="x")
        e = ctx.exception
        self.assertIsNotNone(getattr(e, "proposal_path", None))
        self.assertTrue(Path(e.proposal_path).exists())
        self.assertIn("직접 파일 쓰기 불가", str(e))

    def test_normal_allows_write_only_in_allowed_roots(self):
        sm = SecurityManager(level="NORMAL", sandbox_root=_SANDBOX_ROOT)
        ok = sm.resolve_for_write("data/_security_test_ok.json", content='{"ok":1}')
        self.assertTrue(ok.as_posix().endswith("data/_security_test_ok.json"))

        with self.assertRaises(SecurityBlocked):
            sm.resolve_for_write("random_dir/_nope.txt", content="x")

    def test_hard_restricts_extensions(self):
        sm = SecurityManager(level="HARD", sandbox_root=_SANDBOX_ROOT)
        # 허용 확장자
        ok = sm.resolve_for_write("outputs/_security_test_ok.json", content="{}")
        self.assertTrue(ok.suffix.lower() == ".json")

        # 비허용 확장자
        with self.assertRaises(SecurityBlocked):
            sm.resolve_for_write("outputs/_security_test_bad.exe", content="MZ...")

    def test_sensitive_reads_blocked_in_normal_and_hard(self):
        # NOTE: .env가 실제로 존재하더라도 resolve 단계에서 차단되어야 함
        for level in ("NORMAL", "HARD"):
            sm = SecurityManager(level=level, sandbox_root=_SANDBOX_ROOT)
            with self.assertRaises(SecurityBlocked):
                sm.resolve_for_read(".env")

        sm_easy = SecurityManager(level="EASY", sandbox_root=_SANDBOX_ROOT)
        # EASY는 읽기 제한이 없음(편의성)
        p = sm_easy.resolve_for_read(".env")
        self.assertTrue(p.name == ".env")

    def test_command_policy_varies_by_tier(self):
        sm_easy = SecurityManager(level="EASY", sandbox_root=_SANDBOX_ROOT)
        # 메타문자도 차단하지 않음(실행은 shell=False라 체인 효과는 없지만 정책상 "차단"은 안 함)
        tokens = sm_easy.parse_and_validate_command("whoami && whoami")
        self.assertTrue(len(tokens) >= 3)

        sm_normal = SecurityManager(level="NORMAL", sandbox_root=_SANDBOX_ROOT)
        with self.assertRaises(SecurityBlocked):
            sm_normal.parse_and_validate_command("whoami && whoami")

        sm_hard = SecurityManager(level="HARD", sandbox_root=_SANDBOX_ROOT)
        with self.assertRaises(SecurityBlocked):
            sm_hard.parse_and_validate_command("curl http://example.com")
        # HARD allowlist에는 whoami가 포함되어야 함
        self.assertTrue(len(sm_hard.parse_and_validate_command("whoami")) >= 1)

    def test_outbound_http_policy(self):
        sm_normal = SecurityManager(level="NORMAL", sandbox_root=_SANDBOX_ROOT)
        self.assertFalse(sm_normal.is_outbound_http_allowed("https://example.com/api/v1/posts"))
        self.assertFalse(sm_normal.is_outbound_http_allowed("https://example.com/"))

        sm_hard = SecurityManager(level="HARD", sandbox_root=_SANDBOX_ROOT)
        # 기본 deny
        os.environ.pop("MELLOW_ALLOW_OUTBOUND", None)
        self.assertFalse(sm_hard.is_outbound_http_allowed("https://example.com/api/v1/posts"))
        # override: HARD 모드에서 MELLOW_ALLOW_OUTBOUND=true일 때만 허용 (도메인 무관)
        os.environ["MELLOW_ALLOW_OUTBOUND"] = "true"
        self.assertTrue(sm_hard.is_outbound_http_allowed("https://example.com/api/v1/posts"))
        os.environ.pop("MELLOW_ALLOW_OUTBOUND", None)
        self.assertFalse(sm_hard.is_outbound_http_allowed("https://example.com/"))

    def test_agent_tools_security_level_is_immutable_after_import(self):
        """
        V-04/V-05 회귀 테스트:
          - agent_tools는 import 시점에 SECURITY_LEVEL을 동결해야 한다.
          - 런타임에 os.environ을 바꿔도 _get_security() 결과가 바뀌면 안 된다.
        """
        os.environ["SECURITY_LEVEL"] = "NORMAL"
        os.environ.pop("MELLOW_SECURITY_LEVEL", None)
        # agent_tools는 import 시 전역 ToolRegistry를 freeze()하므로,
        # 테스트에서 reload가 필요할 때는 tool_registry부터 reload하여 registry를 재생성한다.
        import mellow_link.core.tool_registry as tool_registry
        importlib.reload(tool_registry)
        if "mellow_link.core.agent_tools" in sys.modules:
            import mellow_link.core.agent_tools as agent_tools
            importlib.reload(agent_tools)
        else:
            import mellow_link.core.agent_tools as agent_tools

        before = agent_tools.security_status()
        self.assertIn('"security_level": "NORMAL"', before)

        # 런타임에서 보안 등급을 바꾸려 해도 동결되어야 함
        os.environ["SECURITY_LEVEL"] = "EASY"
        after = agent_tools.security_status()
        self.assertIn('"security_level": "NORMAL"', after)


if __name__ == "__main__":
    unittest.main()

