"""ToolForge 핵심 기능 단위 테스트."""
import ast
import sys
import os
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))


class TestToolForgeUnit(unittest.TestCase):
    """ToolForge 핵심 기능 테스트."""

    def setUp(self):
        import mellow_link.core.tool_forge as tf
        tf._forge_instance = None
        self.forge = tf.get_tool_forge("NORMAL")

    def test_01_instance_creation(self):
        self.assertEqual(self.forge.security_level, "NORMAL")
        self.assertGreaterEqual(self.forge._MAX_WORKERS, 2)

    def test_02_safe_code_passes_ast(self):
        from mellow_link.core.tool_forge import run_ast_security_check
        ok, err = run_ast_security_check('def hello(name):\n    return f"Hello {name}"')
        self.assertTrue(ok, f"Expected safe code to pass: {err}")

    def test_03_subprocess_blocked(self):
        """subprocess import가 차단됨."""
        from mellow_link.core.tool_forge import ASTSecurityAnalyzer
        analyzer = ASTSecurityAnalyzer("NORMAL")
        code = 'import subprocess\nsubprocess.run(["ls"])'
        tree = ast.parse(code)
        errs = analyzer.analyze(tree, code)
        self.assertTrue(len(errs) > 0, f"Expected subprocess to be blocked: {errs}")

    def test_04_eval_blocked(self):
        from mellow_link.core.tool_forge import run_ast_security_check
        ok, err = run_ast_security_check('result = eval("1+1")')
        self.assertFalse(ok)

    def test_05_dunder_blocked(self):
        from mellow_link.core.tool_forge import run_ast_security_check
        ok, err = run_ast_security_check('x = obj.__class__')
        self.assertFalse(ok)

    def test_06_sandbox_execute(self):
        code = "def add_numbers(a, b):\n    return int(a) + int(b)"
        result = self.forge._sandbox_execute(code, "add_numbers")
        self.assertIn("add_numbers", result)

    def test_07_need_detection_missing_tool(self):
        nd = self.forge.detect_tool_need(
            failed_tool_name="xyz",
            error_message="'xyz' 도구를 찾을 수 없습니다",
        )
        self.assertTrue(nd.needs_new_tool)

    def test_08_hard_mode_blocks_unwhitelisted(self):
        from mellow_link.core.tool_forge import ASTSecurityAnalyzer
        analyzer = ASTSecurityAnalyzer("HARD")
        code = "import numpy\ndef f(): pass"
        tree = ast.parse(code)
        errs = analyzer.analyze(tree, code)
        self.assertTrue(len(errs) > 0)

    def test_09_hard_mode_allows_whitelisted(self):
        from mellow_link.core.tool_forge import ASTSecurityAnalyzer
        analyzer = ASTSecurityAnalyzer("HARD")
        code = 'import json\ndef f(): return json.dumps({"a": 1})'
        tree = ast.parse(code)
        errs = analyzer.analyze(tree, code)
        self.assertEqual(len(errs), 0, f"Should allow json: {errs}")

    def test_10_forge_status(self):
        status = self.forge.get_forge_status()
        self.assertIn("security_level", status)
        self.assertEqual(status["security_level"], "NORMAL")

    def test_11_os_listdir_allowed(self):
        from mellow_link.core.tool_forge import run_ast_security_check
        ok, _ = run_ast_security_check("import os\nprint(os.listdir('.'))")
        self.assertTrue(ok)

    def test_12_os_system_blocked(self):
        from mellow_link.core.tool_forge import run_ast_security_check
        ok, _ = run_ast_security_check("import os\nos.system('ls')")
        self.assertFalse(ok)

    def test_13_temp_tools_staging(self):
        import uuid
        path = self.forge._stage_to_temp("test_tool", "def test_tool(): pass", str(uuid.uuid4()))
        self.assertTrue(path.exists())
        content = path.read_text(encoding="utf-8")
        self.assertIn("test_tool", content)
        path.unlink(missing_ok=True)

    def test_14_check_ast_security_compat(self):
        """하위 호환 메서드 _check_ast_security 테스트."""
        tree = ast.parse("def safe_func(): pass")
        errs = self.forge._check_ast_security(tree)
        self.assertEqual(len(errs), 0)

    def test_15_os_remove_via_attr_blocked(self):
        """os.remove가 OS_FORBIDDEN_ATTRS로 차단."""
        from mellow_link.core.tool_forge import run_ast_security_check
        ok, _ = run_ast_security_check("import os\nos.remove('x')")
        self.assertFalse(ok)


if __name__ == "__main__":
    unittest.main(verbosity=2)
