"""
Workflow Browser Widget
=======================
DirectoryTree를 상속받아 워크플로우 파일(.json)과 결과물(.mp4)만 표시.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Iterable, Optional

from rich.text import Text
from textual import on
from textual.message import Message
from textual.widgets import DirectoryTree
from textual.widgets._directory_tree import DirEntry


class WorkflowBrowser(DirectoryTree):
    """
    워크플로우 및 결과물 브라우저.

    Features:
    - .json (워크플로우) 및 .mp4 (결과물) 파일만 표시
    - 디렉토리 구조 유지
    - 파일 선택 시 이벤트 발생
    - .mp4 선택 시 시스템 플레이어로 재생
    """

    # 허용되는 파일 확장자
    ALLOWED_EXTENSIONS = {".json", ".mp4", ".webm", ".gif", ".webp"}

    # 파일 아이콘 매핑
    FILE_ICONS = {
        ".json": "",
        ".mp4": "",
        ".webm": "",
        ".gif": "",
        ".webp": "",
    }

    class FileSelected(Message):
        """파일 선택 이벤트."""

        def __init__(self, path: Path) -> None:
            self.path = path
            super().__init__()

    def __init__(
        self,
        path: str | Path = ".",
        *,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
        disabled: bool = False,
    ) -> None:
        super().__init__(
            path,
            name=name,
            id=id,
            classes=classes,
            disabled=disabled,
        )
        self._selected_path: Optional[Path] = None

    def filter_paths(self, paths: Iterable[Path]) -> Iterable[Path]:
        """
        파일 필터링.

        .json과 .mp4 파일, 그리고 디렉토리만 표시.
        숨김 파일/폴더 제외.
        """
        for path in paths:
            # 숨김 파일/폴더 제외
            if path.name.startswith("."):
                continue

            # 디렉토리는 항상 표시
            if path.is_dir():
                # 빈 디렉토리 또는 허용된 파일이 없는 디렉토리는 숨김
                if self._has_valid_files(path):
                    yield path

            # 허용된 확장자만 표시
            elif path.suffix.lower() in self.ALLOWED_EXTENSIONS:
                yield path

    def _has_valid_files(self, directory: Path) -> bool:
        """디렉토리에 유효한 파일이 있는지 확인."""
        try:
            for item in directory.iterdir():
                if item.is_dir():
                    if self._has_valid_files(item):
                        return True
                elif item.suffix.lower() in self.ALLOWED_EXTENSIONS:
                    return True
        except PermissionError:
            pass
        return False

    def render_label(
        self,
        node: "DirEntry",
        base_style: str,
        style: str,
    ) -> Text:
        """
        노드 레이블 렌더링.

        파일 타입에 따라 아이콘 추가.
        """
        # [추가된 방어 코드] 데이터가 없는 루트 노드라면 그냥 이름만 보여주고 끝낸다.
        if node.data is None:
            return Text(str(node.label), style=style)

        path = node.data.path  # 이제 안전하게 접근 가능
        

        if path.is_dir():
            icon = " " if node.is_expanded else " "
            label = Text(f"{icon} {path.name}/", style=style)
        else:
            ext = path.suffix.lower()
            icon = self.FILE_ICONS.get(ext, "")

            # 파일 타입에 따른 색상
            if ext == ".json":
                color = "cyan"
            elif ext in (".mp4", ".webm"):
                color = "green"
            else:
                color = "yellow"

            label = Text()
            label.append(f"{icon} ", style=color)
            label.append(path.name, style=style)

        return label

    @on(DirectoryTree.FileSelected)
    def on_file_selected(self, event: DirectoryTree.FileSelected) -> None:
        """파일 선택 처리."""
        event.stop()

        path = event.path
        self._selected_path = path

        # 커스텀 이벤트 발생
        self.post_message(self.FileSelected(path))

        # .mp4 파일은 자동 재생
        if path.suffix.lower() in (".mp4", ".webm"):
            self._play_video(path)

    def get_selected_path(self) -> Optional[Path]:
        """현재 선택된 파일 경로 반환."""
        return self._selected_path

    def _play_video(self, path: Path) -> None:
        """
        비디오 파일 재생.

        시스템 기본 플레이어 사용 (subprocess.Popen).
        절대 cv2.imshow 사용 금지 - TUI 스레드와 충돌.
        """
        try:
            if sys.platform == "win32":
                # Windows: start 명령 사용
                subprocess.Popen(
                    ["start", "", str(path)],
                    shell=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            elif sys.platform == "darwin":
                # macOS: open 명령 사용
                subprocess.Popen(
                    ["open", str(path)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            else:
                # Linux: xdg-open 사용
                subprocess.Popen(
                    ["xdg-open", str(path)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
        except Exception as e:
            self.log.error(f"Failed to play video: {e}")

    async def reload(self) -> None:
        """디렉토리 트리 새로고침."""
        self.reset_node(self.root, str(self.path))
        await self.reload_node(self.root)  # 여기에 await 필수!
