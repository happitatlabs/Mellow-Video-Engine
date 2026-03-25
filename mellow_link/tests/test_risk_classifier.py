"""risk_classifier 위험도 분류 테스트."""
import unittest
from mellow_link.core.risk_classifier import classify_code_risk_level


class TestRiskClassifier(unittest.TestCase):
    def test_level1_read_only(self):
        code = 'for f in Path("x").iterdir(): print(f)'
        level, _ = classify_code_risk_level(code)
        self.assertEqual(level, 1)

    def test_level1_simple_print(self):
        level, _ = classify_code_risk_level("print(1+2)")
        self.assertEqual(level, 1)

    def test_level2_file_write(self):
        code = 'x = open("a", "w"); x.write("hi")'
        level, _ = classify_code_risk_level(code)
        self.assertEqual(level, 2)

    def test_level2_write_text(self):
        level, _ = classify_code_risk_level('Path("x").write_text("a")')
        self.assertEqual(level, 2)

    def test_level3_subprocess(self):
        code = 'import subprocess; subprocess.run(["ls"])'
        level, _ = classify_code_risk_level(code)
        self.assertEqual(level, 3)

    def test_level3_requests(self):
        level, _ = classify_code_risk_level('import requests; requests.get("http://x")')
        self.assertEqual(level, 3)
