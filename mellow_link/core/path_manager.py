"""
PathManager: 에이전트의 파일 접근을 sandbox(mellow_link) 내부로 강제하는 보안 게이트.

위협 모델:
  에이전트가 외부 소스에서 수집한 데이터(제목, URL, 사용자 입력 등)를 로컬에 저장할 때,
  공격자가 데이터 속에 악성 경로를 삽입하여 sandbox 밖의 파일을 읽거나 덮어쓰는 시나리오를 방어한다.

방어 대상:
  - ../  상대 경로를 이용한 상위 디렉토리 탈출
  - 절대 경로를 직접 지정하여 외부 폴더 접근
  - 심볼릭 링크를 통한 우회 (resolve()로 실제 경로 확인)
  - sandbox_root와 접두사만 공유하는 형제 디렉토리 (예: mellow_link_evil)
  - 외부 입력값을 파일명으로 쓸 때 위험 문자 제거
"""

import json
import re
import unicodedata
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Union


class PathManager:
    """에이전트의 파일 접근을 sandbox_root 내부로 제한하는 보안 관리자."""

    # sanitize_filename에서 제거할 문자 패턴.
    # Windows 금지 문자 + 셸 메타 문자 + 경로 구분자 일괄 제거.
    _UNSAFE_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f\x7f;`$&!#{}()\[\]^~\'%]')

    # Windows 예약 디바이스 이름 (확장자 붙여도 예약어로 인식됨)
    _RESERVED_NAMES = frozenset({
        "CON", "PRN", "AUX", "NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    })

    # 허용할 파일명 최대 길이 (NTFS 255자 제한 준수)
    _MAX_FILENAME_LEN = 200

    def __init__(self, sandbox_root: Union[str, Path] = r"D:\AI_Project\mellow_link"):
        """
        Args:
            sandbox_root: 에이전트가 접근 가능한 최상위 디렉토리.
                          해당 경로가 존재하지 않으면 FileNotFoundError(OSError) 발생.
        """
        self._root = Path(sandbox_root).resolve(strict=True)

    @property
    def root(self) -> Path:
        """sandbox 루트 경로 (읽기 전용)."""
        return self._root

    # ──────────────────────────────────────────
    # Core: 경로 검증
    # ──────────────────────────────────────────

    def validate(self, target_path: Union[str, Path]) -> Path:
        """
        입력 경로가 sandbox 내부에 있는지 검증.

        처리 흐름:
          1. 절대 경로 여부 판별 -> 절대 경로면 그대로, 상대 경로면 root 기준 결합
          2. resolve()로 .., symlink 등을 모두 정규화
          3. is_relative_to()로 sandbox 포함 여부 판정 (parts 단위 비교)

        Returns:
            정규화된 안전한 Path 객체.

        Raises:
            PermissionError: sandbox 외부 접근 시도가 감지된 경우.
        """
        candidate = Path(target_path)

        if candidate.is_absolute():
            resolved = candidate.resolve()
        else:
            resolved = (self._root / candidate).resolve()

        if not resolved.is_relative_to(self._root):
            raise PermissionError(
                f"Access denied: path escapes sandbox. "
                f"requested={target_path!s}, resolved={resolved}"
            )

        return resolved

    # ──────────────────────────────────────────
    # Sanitization: 외부 입력 -> 안전한 파일명
    # ──────────────────────────────────────────

    def sanitize_filename(self, raw_name: str, fallback: str = "untitled") -> str:
        """
        외부 입력값(게시글 제목, 사용자 이름 등)을 안전한 파일명 문자열로 변환.

        처리 순서:
          1. Unicode 정규화 (NFC) -> 같은 글자의 다른 표현 통일
          2. 경로 구분자 및 위험 문자 제거
          3. 앞뒤 공백/마침표 제거 (Windows: "file." -> "file" 으로 해석)
          4. Windows 예약어 검사 (CON, NUL 등)
          5. 길이 제한 (200자)
          6. 빈 문자열이면 fallback 이름 사용

        Args:
            raw_name: 외부에서 가져온 원본 문자열 (게시글 제목, 사용자 이름 등).
            fallback: 정제 후 빈 문자열이 될 경우 사용할 기본 이름.

        Returns:
            파일명으로 안전하게 사용 가능한 문자열 (확장자 미포함).
        """
        # 1. Unicode NFC 정규화
        name = unicodedata.normalize("NFC", raw_name)

        # 2. 위험 문자 제거
        name = self._UNSAFE_CHARS.sub("", name)

        # 3. 앞뒤 공백 및 마침표 제거
        name = name.strip(" .")

        # 4. 연속 공백을 언더스코어로 치환
        name = re.sub(r'\s+', '_', name)

        # 5. Windows 예약어 검사 (확장자 제거 후 비교)
        stem = name.split(".")[0].upper()
        if stem in self._RESERVED_NAMES:
            name = f"_{name}"

        # 6. 길이 제한
        if len(name) > self._MAX_FILENAME_LEN:
            name = name[:self._MAX_FILENAME_LEN]

        # 7. 빈 문자열 방어
        if not name:
            name = fallback

        return name

    def safe_join(
        self,
        subdir: str,
        raw_filename: str,
        extension: str = ".json",
    ) -> Path:
        """
        외부 입력 파일명을 정제 후, sandbox 내 하위 디렉토리에 안전하게 결합.

        전형적 사용 패턴:
            pm = PathManager()
            path = pm.safe_join("posts", post_title, ".json")
            path.write_text(json.dumps(data))

        Args:
            subdir:       sandbox 내 하위 디렉토리 (예: "posts", "images").
            raw_filename: 외부 입력 원본 (예: 게시글 제목).
            extension:    파일 확장자 (기본 ".json").

        Returns:
            검증 완료된 절대 Path.
        """
        clean_name = self.sanitize_filename(raw_filename)
        # 확장자에 점이 없으면 붙여줌
        if extension and not extension.startswith("."):
            extension = f".{extension}"
        relative = Path(subdir) / f"{clean_name}{extension}"
        return self.validate(relative)

    # ──────────────────────────────────────────
    # Convenience: 읽기/쓰기 래퍼
    # ──────────────────────────────────────────

    def safe_read(self, target_path: Union[str, Path]) -> str:
        """validate 후 텍스트 파일을 읽어 반환하는 편의 메서드."""
        safe = self.validate(target_path)
        return safe.read_text(encoding="utf-8")

    def safe_write(self, target_path: Union[str, Path], content: str) -> Path:
        """validate 후 텍스트 파일을 쓰는 편의 메서드. 부모 디렉토리 자동 생성."""
        safe = self.validate(target_path)
        safe.parent.mkdir(parents=True, exist_ok=True)
        safe.write_text(content, encoding="utf-8")
        return safe

    def get_relative(self, full_path: Union[str, Path]) -> Path:
        """절대 경로를 sandbox 기준 상대 경로로 변환 (로그용)."""
        return Path(full_path).resolve().relative_to(self._root)
