"""
PathManager unittest 테스트 스위트.

검증 범위:
  1. 초기화: 유효/무효 sandbox root
  2. validate: 정상 내부 접근
  3. validate: ../ 상위 폴더 탈출 차단
  4. validate: 외부 절대 경로 차단 (Open-LLM-VTuber, launcher.py)
  5. validate: 접두사 유사 경로 공격 차단 (mellow_link_evil)
  6. sanitize_filename: 외부 입력 정제
  7. safe_join: sanitize + validate 통합 파이프라인
  8. 공격 시나리오 시뮬레이션: 실제 공격 시나리오 재현
"""

import unittest
from pathlib import Path

from mellow_link.core.path_manager import PathManager


SANDBOX = Path(r"D:\AI_Project\mellow_link").resolve()


# ═══════════════════════════════════════════════
# 1. 초기화 테스트
# ═══════════════════════════════════════════════

class TestInit(unittest.TestCase):

    def test_valid_root(self):
        pm = PathManager(r"D:\AI_Project\mellow_link")
        self.assertEqual(pm.root, SANDBOX)

    def test_nonexistent_root_raises(self):
        with self.assertRaises((FileNotFoundError, OSError)):
            PathManager(r"Z:\nonexistent\fake_dir")


# ═══════════════════════════════════════════════
# 2. 정상 접근 테스트
# ═══════════════════════════════════════════════

class TestValidAccess(unittest.TestCase):

    def setUp(self):
        self.pm = PathManager()

    def test_simple_relative(self):
        result = self.pm.validate("core/schemas.py")
        self.assertEqual(result, SANDBOX / "core" / "schemas.py")

    def test_nested_relative(self):
        result = self.pm.validate("data/memory/long_term/archive.json")
        self.assertTrue(result.is_relative_to(SANDBOX))

    def test_dot_current_dir(self):
        result = self.pm.validate("./core/path_manager.py")
        self.assertTrue(result.is_relative_to(SANDBOX))

    def test_internal_absolute_path(self):
        internal = str(SANDBOX / "config" / "settings.yaml")
        result = self.pm.validate(internal)
        self.assertTrue(result.is_relative_to(SANDBOX))

    def test_sandbox_root_itself(self):
        result = self.pm.validate(str(SANDBOX))
        self.assertEqual(result, SANDBOX)

    def test_returns_path_object(self):
        self.assertIsInstance(self.pm.validate("core/states.py"), Path)


# ═══════════════════════════════════════════════
# 3. ../ 상위 폴더 탈출 차단
# ═══════════════════════════════════════════════

class TestTraversalBlocked(unittest.TestCase):

    def setUp(self):
        self.pm = PathManager()

    def test_parent_escape_launcher(self):
        """../launcher.py 접근 차단 (요구사항 명시)."""
        with self.assertRaises(PermissionError):
            self.pm.validate("../launcher.py")

    def test_deep_parent_escape(self):
        with self.assertRaises(PermissionError):
            self.pm.validate("../../launcher.py")

    def test_traversal_to_open_llm_vtuber(self):
        """../Open-LLM-VTuber/config.json 접근 차단 (요구사항 명시)."""
        with self.assertRaises(PermissionError):
            self.pm.validate("../Open-LLM-VTuber/config.json")

    def test_mixed_traversal(self):
        with self.assertRaises(PermissionError):
            self.pm.validate("core/../../Open-LLM-VTuber/main.py")

    def test_multiple_dots_chain(self):
        with self.assertRaises(PermissionError):
            self.pm.validate("../../../Windows/System32/cmd.exe")

    def test_backslash_traversal(self):
        with self.assertRaises(PermissionError):
            self.pm.validate("..\\Open-LLM-VTuber\\config.json")


# ═══════════════════════════════════════════════
# 4. 외부 절대 경로 차단
# ═══════════════════════════════════════════════

class TestAbsolutePathBlocked(unittest.TestCase):

    def setUp(self):
        self.pm = PathManager()

    def test_open_llm_vtuber_absolute(self):
        with self.assertRaises(PermissionError):
            self.pm.validate(r"D:\AI_Project\Open-LLM-VTuber\config.json")

    def test_launcher_absolute(self):
        with self.assertRaises(PermissionError):
            self.pm.validate(r"D:\AI_Project\launcher.py")

    def test_system_path(self):
        with self.assertRaises(PermissionError):
            self.pm.validate(r"C:\Windows\System32\cmd.exe")

    def test_drive_root(self):
        with self.assertRaises(PermissionError):
            self.pm.validate("D:\\")

    def test_parent_dir_absolute(self):
        with self.assertRaises(PermissionError):
            self.pm.validate(r"D:\AI_Project")


# ═══════════════════════════════════════════════
# 5. 접두사 유사 경로 공격 차단
# ═══════════════════════════════════════════════

class TestPrefixSpoofBlocked(unittest.TestCase):

    def setUp(self):
        self.pm = PathManager()

    def test_mellow_link_evil(self):
        with self.assertRaises(PermissionError):
            self.pm.validate(r"D:\AI_Project\mellow_link_evil\exploit.py")

    def test_mellow_link_bak(self):
        with self.assertRaises(PermissionError):
            self.pm.validate(r"D:\AI_Project\mellow_link.bak")


# ═══════════════════════════════════════════════
# 6. sanitize_filename 테스트
# ═══════════════════════════════════════════════

class TestSanitizeFilename(unittest.TestCase):

    def setUp(self):
        self.pm = PathManager()

    # --- 정상 입력 ---

    def test_normal_korean_title(self):
        """한글 게시글 제목은 그대로 유지."""
        result = self.pm.sanitize_filename("오늘의 외부 입력 감상평")
        self.assertEqual(result, "오늘의_외부_입력_감상평")

    def test_normal_english_title(self):
        result = self.pm.sanitize_filename("My First Post")
        self.assertEqual(result, "My_First_Post")

    # --- 경로 구분자 공격 ---

    def test_strips_forward_slash(self):
        """게시글 제목에 / 가 포함된 경우 제거."""
        result = self.pm.sanitize_filename("../../etc/passwd")
        self.assertNotIn("/", result)
        self.assertNotIn("..", result)

    def test_strips_backslash(self):
        result = self.pm.sanitize_filename("..\\..\\launcher.py")
        self.assertNotIn("\\", result)

    # --- 위험 문자 제거 ---

    def test_strips_shell_metacharacters(self):
        result = self.pm.sanitize_filename("post; rm -rf /")
        self.assertNotIn(";", result)
        self.assertNotIn("/", result)

    def test_strips_angle_brackets(self):
        result = self.pm.sanitize_filename("<script>alert(1)</script>")
        self.assertNotIn("<", result)
        self.assertNotIn(">", result)

    def test_strips_null_bytes(self):
        result = self.pm.sanitize_filename("evil\x00file")
        self.assertNotIn("\x00", result)

    # --- Windows 예약어 ---

    def test_reserved_name_con(self):
        """CON은 Windows 예약어 -> 언더스코어 접두사."""
        result = self.pm.sanitize_filename("CON")
        self.assertTrue(result.startswith("_"))

    def test_reserved_name_nul(self):
        result = self.pm.sanitize_filename("NUL")
        self.assertTrue(result.startswith("_"))

    def test_reserved_name_com1(self):
        result = self.pm.sanitize_filename("COM1")
        self.assertTrue(result.startswith("_"))

    def test_reserved_with_extension(self):
        """CON.txt 도 예약어로 간주."""
        result = self.pm.sanitize_filename("CON.txt")
        self.assertTrue(result.startswith("_"))

    # --- Edge cases ---

    def test_empty_string_uses_fallback(self):
        result = self.pm.sanitize_filename("")
        self.assertEqual(result, "untitled")

    def test_only_dots_uses_fallback(self):
        result = self.pm.sanitize_filename("...")
        self.assertEqual(result, "untitled")

    def test_only_special_chars_uses_fallback(self):
        result = self.pm.sanitize_filename('/<>:"|?*')
        self.assertEqual(result, "untitled")

    def test_custom_fallback(self):
        result = self.pm.sanitize_filename("", fallback="default_data")
        self.assertEqual(result, "default_data")

    def test_long_name_truncated(self):
        long_name = "A" * 300
        result = self.pm.sanitize_filename(long_name)
        self.assertLessEqual(len(result), 200)

    def test_trailing_dots_stripped(self):
        """Windows는 후행 마침표를 무시하므로 제거해야 한다."""
        result = self.pm.sanitize_filename("my_post...")
        self.assertFalse(result.endswith("."))

    def test_leading_dots_stripped(self):
        """선행 마침표도 제거 (숨김 파일 방지)."""
        result = self.pm.sanitize_filename("...hidden")
        self.assertFalse(result.startswith("."))

    def test_whitespace_collapsed(self):
        result = self.pm.sanitize_filename("hello    world")
        self.assertEqual(result, "hello_world")


# ═══════════════════════════════════════════════
# 7. safe_join 테스트
# ═══════════════════════════════════════════════

class TestSafeJoin(unittest.TestCase):

    def setUp(self):
        self.pm = PathManager()

    def test_normal_join(self):
        result = self.pm.safe_join("posts", "My External Post")
        expected = SANDBOX / "posts" / "My_External_Post.json"
        self.assertEqual(result, expected)

    def test_custom_extension(self):
        result = self.pm.safe_join("images", "avatar_photo", ".png")
        self.assertTrue(str(result).endswith(".png"))

    def test_extension_without_dot(self):
        result = self.pm.safe_join("logs", "activity", "txt")
        self.assertTrue(str(result).endswith(".txt"))

    def test_malicious_filename_sanitized(self):
        """악성 파일명이 정제된 후 sandbox 내부로 들어간다."""
        result = self.pm.safe_join("posts", "../../launcher")
        self.assertTrue(result.is_relative_to(SANDBOX))
        self.assertNotIn("..", str(result))

    def test_result_inside_sandbox(self):
        result = self.pm.safe_join("data", "게시글 제목 테스트", ".json")
        self.assertTrue(result.is_relative_to(SANDBOX))


# ═══════════════════════════════════════════════
# 8. 공격 시나리오 시뮬레이션
# ═══════════════════════════════════════════════

class TestAttackScenarios(unittest.TestCase):
    """
    외부 API 응답에 악성 데이터가 포함된 경우를 시뮬레이션.
    공격자가 게시글 제목, 이미지 URL, 파일명 필드를 조작하는 상황.
    """

    def setUp(self):
        self.pm = PathManager()

    # --- 시나리오 A: 게시글 제목에 경로 탈출 삽입 ---

    def test_post_title_traversal_attack(self):
        """공격자가 게시글 제목을 '../../launcher' 로 설정한 경우."""
        malicious_title = "../../launcher"
        path = self.pm.safe_join("posts", malicious_title)
        self.assertTrue(path.is_relative_to(SANDBOX))
        # launcher라는 파일명 자체는 남지만 sandbox 안에 있음
        self.assertIn("posts", str(path))

    def test_post_title_absolute_path_attack(self):
        """제목이 절대 경로인 경우 sanitize가 경로 구분자를 제거."""
        malicious_title = r"D:\AI_Project\Open-LLM-VTuber\config"
        path = self.pm.safe_join("posts", malicious_title)
        self.assertTrue(path.is_relative_to(SANDBOX))

    # --- 시나리오 B: 이미지 파일명에 쉘 메타 문자 삽입 ---

    def test_image_filename_shell_injection(self):
        """이미지 파일명: 'avatar; rm -rf /'"""
        malicious_name = "avatar; rm -rf /"
        path = self.pm.safe_join("images", malicious_name, ".png")
        sanitized_name = path.stem
        self.assertNotIn(";", sanitized_name)
        self.assertNotIn("/", sanitized_name)
        self.assertTrue(path.is_relative_to(SANDBOX))

    # --- 시나리오 C: null byte injection ---

    def test_null_byte_filename(self):
        """파일명에 null byte 삽입: 'data\x00.exe'"""
        malicious = "data\x00.exe"
        path = self.pm.safe_join("downloads", malicious, ".json")
        self.assertNotIn("\x00", str(path))
        self.assertTrue(path.is_relative_to(SANDBOX))

    # --- 시나리오 D: Unicode 정규화 공격 ---

    def test_unicode_normalization(self):
        """
        다른 Unicode 표현의 같은 글자가 일관되게 처리되는지 확인.
        'cafe\u0301' (e + combining accent) vs 'caf\u00e9' (precomposed e)
        """
        name_a = self.pm.sanitize_filename("caf\u00e9")       # precomposed
        name_b = self.pm.sanitize_filename("cafe\u0301")       # decomposed
        self.assertEqual(name_a, name_b)

    # --- 시나리오 E: 매우 긴 외부 입력 제목 ---

    def test_extremely_long_title(self):
        """500자짜리 제목이 잘려서 200자 이내로 저장."""
        long_title = "외부_입력_긴_게시글_" * 50
        path = self.pm.safe_join("posts", long_title)
        self.assertLessEqual(len(path.stem), 200)
        self.assertTrue(path.is_relative_to(SANDBOX))

    # --- 시나리오 F: validate 직접 호출로 No-Go Zone 접근 ---

    def test_direct_validate_open_llm_vtuber(self):
        """에이전트가 validate를 직접 호출해서 Open-LLM-VTuber 접근 시도."""
        with self.assertRaises(PermissionError):
            self.pm.validate("../Open-LLM-VTuber/config.json")

    def test_direct_validate_launcher(self):
        """에이전트가 validate를 직접 호출해서 launcher.py 접근 시도."""
        with self.assertRaises(PermissionError):
            self.pm.validate("../launcher.py")

    def test_direct_validate_open_llm_vtuber_absolute(self):
        with self.assertRaises(PermissionError):
            self.pm.validate(r"D:\AI_Project\Open-LLM-VTuber\config.json")

    def test_direct_validate_launcher_absolute(self):
        with self.assertRaises(PermissionError):
            self.pm.validate(r"D:\AI_Project\launcher.py")


# ═══════════════════════════════════════════════
# 9. get_relative 유틸리티
# ═══════════════════════════════════════════════

class TestGetRelative(unittest.TestCase):

    def setUp(self):
        self.pm = PathManager()

    def test_converts_to_relative(self):
        full = SANDBOX / "core" / "path_manager.py"
        self.assertEqual(self.pm.get_relative(full), Path("core/path_manager.py"))

    def test_outside_path_raises(self):
        with self.assertRaises(ValueError):
            self.pm.get_relative(r"D:\AI_Project\launcher.py")


if __name__ == "__main__":
    unittest.main(verbosity=2)
