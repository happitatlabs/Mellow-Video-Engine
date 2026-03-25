"""os 모듈 화이트리스트 AST 검사 테스트."""
import unittest
from mellow_link.core.tool_forge import run_ast_security_check


class TestOsWhitelist(unittest.TestCase):
    def test_os_listdir_allowed(self):
        ok, _ = run_ast_security_check("import os\nprint(os.listdir('.'))")
        self.assertTrue(ok)

    def test_os_walk_allowed(self):
        ok, _ = run_ast_security_check("import os\nfrom pathlib import Path\nfor r,d,f in os.walk(Path('.')): print(r)")
        self.assertTrue(ok)

    def test_os_system_blocked(self):
        ok, _ = run_ast_security_check("import os\nos.system('ls')")
        self.assertFalse(ok)

    def test_os_remove_blocked(self):
        ok, _ = run_ast_security_check("import os\nos.remove('x')")
        self.assertFalse(ok)
