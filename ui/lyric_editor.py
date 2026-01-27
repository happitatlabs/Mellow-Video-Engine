"""
Lyric Editor Widget (Textual TUI Version)
=========================================
DataTable 기반 가사 편집기.
타이밍과 텍스트를 수정할 수 있는 Human-in-the-loop 인터페이스.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Header,
    Input,
    Label,
    Static,
)


# =============================================================================
# Edit Modal Screens
# =============================================================================

class EditTextModal(ModalScreen[Optional[str]]):
    """텍스트 편집 모달."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("enter", "submit", "Submit"),
    ]

    CSS = """
    EditTextModal {
        align: center middle;
    }

    #text-modal-container {
        width: 80;
        height: auto;
        padding: 1 2;
        background: $surface;
        border: thick $primary;
    }

    #text-modal-title {
        text-align: center;
        margin-bottom: 1;
    }

    #text-modal-input {
        margin-bottom: 1;
    }

    #text-modal-buttons {
        height: auto;
        align: center middle;
    }

    #text-modal-buttons Button {
        margin: 0 1;
    }
    """

    def __init__(self, title: str, current_value: str) -> None:
        super().__init__()
        self.title_text = title
        self.current_value = current_value

    def compose(self) -> ComposeResult:
        with Container(id="text-modal-container"):
            yield Label(f"[bold]{self.title_text}[/bold]", id="text-modal-title")
            yield Input(value=self.current_value, id="text-modal-input")
            with Horizontal(id="text-modal-buttons"):
                yield Button("Save", variant="primary", id="btn-save")
                yield Button("Cancel", variant="default", id="btn-cancel")

    def on_mount(self) -> None:
        self.query_one("#text-modal-input", Input).focus()

    @on(Button.Pressed, "#btn-save")
    def on_save(self) -> None:
        self.dismiss(self.query_one("#text-modal-input", Input).value)

    @on(Button.Pressed, "#btn-cancel")
    def on_cancel(self) -> None:
        self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_submit(self) -> None:
        self.dismiss(self.query_one("#text-modal-input", Input).value)


class EditTimingModal(ModalScreen[Optional[Tuple[float, float]]]):
    """타이밍 편집 모달."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("enter", "submit", "Submit"),
    ]

    CSS = """
    EditTimingModal {
        align: center middle;
    }

    #timing-modal-container {
        width: 50;
        height: auto;
        padding: 1 2;
        background: $surface;
        border: thick $primary;
    }

    #timing-modal-title {
        text-align: center;
        margin-bottom: 1;
    }

    .timing-row {
        height: auto;
        margin-bottom: 1;
    }

    .timing-label {
        width: 10;
    }

    .timing-input {
        width: 1fr;
    }

    #timing-modal-buttons {
        height: auto;
        align: center middle;
    }

    #timing-modal-buttons Button {
        margin: 0 1;
    }
    """

    def __init__(self, start: float, end: float) -> None:
        super().__init__()
        self.start = start
        self.end = end

    def compose(self) -> ComposeResult:
        with Container(id="timing-modal-container"):
            yield Label("[bold]Edit Timing[/bold]", id="timing-modal-title")

            with Horizontal(classes="timing-row"):
                yield Label("Start:", classes="timing-label")
                yield Input(value=f"{self.start:.3f}", id="input-start", classes="timing-input")

            with Horizontal(classes="timing-row"):
                yield Label("End:", classes="timing-label")
                yield Input(value=f"{self.end:.3f}", id="input-end", classes="timing-input")

            with Horizontal(id="timing-modal-buttons"):
                yield Button("Save", variant="primary", id="btn-save")
                yield Button("Cancel", variant="default", id="btn-cancel")

    def on_mount(self) -> None:
        self.query_one("#input-start", Input).focus()

    @on(Button.Pressed, "#btn-save")
    def on_save(self) -> None:
        try:
            start = float(self.query_one("#input-start", Input).value)
            end = float(self.query_one("#input-end", Input).value)
            self.dismiss((start, end))
        except ValueError:
            self.notify("Invalid timing values", severity="error")

    @on(Button.Pressed, "#btn-cancel")
    def on_cancel(self) -> None:
        self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_submit(self) -> None:
        self.on_save()


# =============================================================================
# LyricEditorWidget
# =============================================================================

class LyricEditorWidget(Container):
    """
    DataTable 기반 가사 편집기.

    Features:
    - 가사 세그먼트 표시 (시작, 종료, 텍스트, 신뢰도)
    - 셀 더블클릭/Enter로 편집
    - 세그먼트 추가/삭제/병합
    - JSON 내보내기/불러오기
    """

    BINDINGS = [
        Binding("e", "edit_text", "Edit Text"),
        Binding("t", "edit_timing", "Edit Timing"),
        Binding("a", "add_segment", "Add"),
        Binding("d", "delete_segment", "Delete"),
        Binding("m", "merge_segments", "Merge"),
        Binding("s", "save", "Save"),
    ]

    CSS = """
    LyricEditorWidget {
        height: 100%;
    }

    #lyric-toolbar {
        height: auto;
        padding: 1;
        border-bottom: solid $primary;
    }

    #lyric-toolbar Button {
        margin-right: 1;
    }

    #lyric-table {
        height: 1fr;
    }

    #lyric-info {
        height: auto;
        padding: 1;
        border-top: solid $primary;
    }
    """

    class LyricsModified(Message):
        """가사 수정됨 이벤트."""
        def __init__(self, segments: List[Dict[str, Any]]) -> None:
            self.segments = segments
            super().__init__()

    class LyricsConfirmed(Message):
        """가사 확정됨 이벤트."""
        def __init__(self, segments: List[Dict[str, Any]]) -> None:
            self.segments = segments
            super().__init__()

    def __init__(
        self,
        *,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
        disabled: bool = False,
    ) -> None:
        super().__init__(name=name, id=id, classes=classes, disabled=disabled)

        # 세그먼트 데이터: [{"text": str, "start": float, "end": float, ...}, ...]
        self._segments: List[Dict[str, Any]] = []
        self._modified = False

    def compose(self) -> ComposeResult:
        with Horizontal(id="lyric-toolbar"):
            yield Button("Edit Text", id="btn-edit-text", variant="primary")
            yield Button("Edit Timing", id="btn-edit-timing", variant="default")
            yield Button("Add", id="btn-add", variant="success")
            yield Button("Delete", id="btn-delete", variant="error")
            yield Button("Merge", id="btn-merge", variant="warning")
            yield Button("Confirm", id="btn-confirm", variant="success")

        yield DataTable(id="lyric-table", cursor_type="row")

        yield Static("", id="lyric-info")

    def on_mount(self) -> None:
        """마운트 시 테이블 초기화."""
        table = self.query_one("#lyric-table", DataTable)
        table.add_columns("#", "Start", "End", "Duration", "Text", "Conf")
        table.zebra_stripes = True

    def load_segments(self, segments: List[Dict[str, Any]]) -> None:
        """
        세그먼트 로드.

        Args:
            segments: audio_engine에서 반환된 세그먼트 리스트
        """
        self._segments = [s.copy() for s in segments]
        self._modified = False
        self._refresh_table()
        self._update_info()

    def get_segments(self) -> List[Dict[str, Any]]:
        """현재 세그먼트 반환."""
        return [s.copy() for s in self._segments]

    def _refresh_table(self) -> None:
        """테이블 새로고침."""
        table = self.query_one("#lyric-table", DataTable)
        table.clear()

        for i, seg in enumerate(self._segments):
            start = seg.get("start", 0)
            end = seg.get("end", 0)
            duration = end - start
            text = seg.get("text", "")
            confidence = seg.get("confidence", 1.0)

            # 텍스트 truncate
            display_text = text[:50] + "..." if len(text) > 50 else text

            # 신뢰도 색상
            if confidence >= 0.9:
                conf_display = f"[green]{confidence:.0%}[/green]"
            elif confidence >= 0.7:
                conf_display = f"[yellow]{confidence:.0%}[/yellow]"
            else:
                conf_display = f"[red]{confidence:.0%}[/red]"

            table.add_row(
                str(i + 1),
                f"{start:.2f}",
                f"{end:.2f}",
                f"{duration:.2f}s",
                display_text,
                conf_display,
                key=str(i),
            )

    def _update_info(self) -> None:
        """정보 표시 업데이트."""
        info = self.query_one("#lyric-info", Static)

        if not self._segments:
            info.update("[dim]No segments loaded[/dim]")
            return

        total_duration = sum(s.get("end", 0) - s.get("start", 0) for s in self._segments)
        total_chars = sum(len(s.get("text", "")) for s in self._segments)

        modified_marker = " [yellow]*Modified[/yellow]" if self._modified else ""

        info.update(
            f"Segments: {len(self._segments)} | "
            f"Total Duration: {total_duration:.1f}s | "
            f"Characters: {total_chars}"
            f"{modified_marker}"
        )

    def _get_selected_index(self) -> Optional[int]:
        """선택된 행 인덱스 반환."""
        table = self.query_one("#lyric-table", DataTable)
        if table.cursor_row is not None and table.cursor_row < len(self._segments):
            return table.cursor_row
        return None

    # =========================================================================
    # Button Handlers
    # =========================================================================

    @on(Button.Pressed, "#btn-edit-text")
    def on_edit_text_pressed(self) -> None:
        self.action_edit_text()

    @on(Button.Pressed, "#btn-edit-timing")
    def on_edit_timing_pressed(self) -> None:
        self.action_edit_timing()

    @on(Button.Pressed, "#btn-add")
    def on_add_pressed(self) -> None:
        self.action_add_segment()

    @on(Button.Pressed, "#btn-delete")
    def on_delete_pressed(self) -> None:
        self.action_delete_segment()

    @on(Button.Pressed, "#btn-merge")
    def on_merge_pressed(self) -> None:
        self.action_merge_segments()

    @on(Button.Pressed, "#btn-confirm")
    def on_confirm_pressed(self) -> None:
        """확정 버튼."""
        self.post_message(self.LyricsConfirmed(self.get_segments()))

    @on(DataTable.RowSelected)
    def on_row_selected(self, event: DataTable.RowSelected) -> None:
        """행 선택 시 상세 정보 표시."""
        idx = self._get_selected_index()
        if idx is not None and idx < len(self._segments):
            seg = self._segments[idx]
            info = self.query_one("#lyric-info", Static)
            info.update(
                f"[{idx + 1}] {seg.get('start', 0):.3f} - {seg.get('end', 0):.3f} | "
                f"{seg.get('text', '')}"
            )

    # =========================================================================
    # Actions
    # =========================================================================

    def action_edit_text(self) -> None:
        """텍스트 편집."""
        idx = self._get_selected_index()
        if idx is None:
            self.notify("No segment selected", severity="warning")
            return

        seg = self._segments[idx]
        current_text = seg.get("text", "")

        def handle_result(result: Optional[str]) -> None:
            if result is not None and result != current_text:
                self._segments[idx]["text"] = result
                self._segments[idx]["is_modified"] = True
                self._modified = True
                self._refresh_table()
                self._update_info()
                self.post_message(self.LyricsModified(self.get_segments()))

        self.app.push_screen(
            EditTextModal("Edit Lyric Text", current_text),
            handle_result,
        )

    def action_edit_timing(self) -> None:
        """타이밍 편집."""
        idx = self._get_selected_index()
        if idx is None:
            self.notify("No segment selected", severity="warning")
            return

        seg = self._segments[idx]
        start = seg.get("start", 0)
        end = seg.get("end", 0)

        def handle_result(result: Optional[Tuple[float, float]]) -> None:
            if result is not None:
                new_start, new_end = result
                if new_end > new_start:
                    self._segments[idx]["start"] = new_start
                    self._segments[idx]["end"] = new_end
                    self._segments[idx]["is_modified"] = True
                    self._modified = True
                    self._refresh_table()
                    self._update_info()
                    self.post_message(self.LyricsModified(self.get_segments()))
                else:
                    self.notify("End time must be after start time", severity="error")

        self.app.push_screen(
            EditTimingModal(start, end),
            handle_result,
        )

    def action_add_segment(self) -> None:
        """세그먼트 추가."""
        idx = self._get_selected_index()

        # 기본 타이밍 계산
        if idx is not None and idx < len(self._segments):
            prev_seg = self._segments[idx]
            new_start = prev_seg.get("end", 0)
            new_end = new_start + 2.0
        elif self._segments:
            last_seg = self._segments[-1]
            new_start = last_seg.get("end", 0)
            new_end = new_start + 2.0
        else:
            new_start = 0.0
            new_end = 2.0

        new_segment = {
            "text": "",
            "start": new_start,
            "end": new_end,
            "confidence": 1.0,
            "is_modified": True,
            "words": [],
        }

        insert_idx = (idx + 1) if idx is not None else len(self._segments)
        self._segments.insert(insert_idx, new_segment)
        self._modified = True
        self._refresh_table()
        self._update_info()

        # 새 행 선택
        table = self.query_one("#lyric-table", DataTable)
        table.cursor_coordinate = (insert_idx, 0)

        self.post_message(self.LyricsModified(self.get_segments()))
        self.notify(f"Segment added at position {insert_idx + 1}")

    def action_delete_segment(self) -> None:
        """세그먼트 삭제."""
        idx = self._get_selected_index()
        if idx is None:
            self.notify("No segment selected", severity="warning")
            return

        if len(self._segments) <= 1:
            self.notify("Cannot delete the last segment", severity="warning")
            return

        del self._segments[idx]
        self._modified = True
        self._refresh_table()
        self._update_info()
        self.post_message(self.LyricsModified(self.get_segments()))
        self.notify(f"Segment {idx + 1} deleted")

    def action_merge_segments(self) -> None:
        """현재 세그먼트와 다음 세그먼트 병합."""
        idx = self._get_selected_index()
        if idx is None:
            self.notify("No segment selected", severity="warning")
            return

        if idx >= len(self._segments) - 1:
            self.notify("No next segment to merge with", severity="warning")
            return

        current = self._segments[idx]
        next_seg = self._segments[idx + 1]

        # 병합
        merged = {
            "text": current.get("text", "") + " " + next_seg.get("text", ""),
            "start": current.get("start", 0),
            "end": next_seg.get("end", 0),
            "confidence": (current.get("confidence", 1) + next_seg.get("confidence", 1)) / 2,
            "is_modified": True,
            "words": current.get("words", []) + next_seg.get("words", []),
        }

        self._segments[idx] = merged
        del self._segments[idx + 1]

        self._modified = True
        self._refresh_table()
        self._update_info()
        self.post_message(self.LyricsModified(self.get_segments()))
        self.notify(f"Segments {idx + 1} and {idx + 2} merged")

    def action_save(self) -> None:
        """저장 (확정과 동일)."""
        self.post_message(self.LyricsConfirmed(self.get_segments()))

    # =========================================================================
    # Import/Export
    # =========================================================================

    def export_to_json(self, path: Path) -> None:
        """JSON으로 내보내기."""
        with open(path, "w", encoding="utf-8") as f:
            json.dump({
                "segments": self._segments,
            }, f, ensure_ascii=False, indent=2)

    def import_from_json(self, path: Path) -> None:
        """JSON에서 불러오기."""
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        self._segments = data.get("segments", [])
        self._modified = False
        self._refresh_table()
        self._update_info()

    def export_to_srt(self, path: Path) -> None:
        """SRT 형식으로 내보내기."""
        lines = []

        for i, seg in enumerate(self._segments, 1):
            start = self._format_srt_time(seg.get("start", 0))
            end = self._format_srt_time(seg.get("end", 0))
            text = seg.get("text", "")

            lines.append(str(i))
            lines.append(f"{start} --> {end}")
            lines.append(text)
            lines.append("")

        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))

    @staticmethod
    def _format_srt_time(seconds: float) -> str:
        """SRT 타임스탬프 포맷."""
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        millis = int((seconds % 1) * 1000)
        return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


# =============================================================================
# Lyric Editor Screen (Full Screen Version)
# =============================================================================

class LyricEditorScreen(Container):
    """가사 편집 전체 화면."""

    CSS = """
    LyricEditorScreen {
        width: 100%;
        height: 100%;
    }

    #editor-container {
        width: 100%;
        height: 100%;
        padding: 1;
    }

    #editor-title {
        text-align: center;
        margin-bottom: 1;
    }
    """

    def __init__(
        self,
        segments: List[Dict[str, Any]],
        audio_path: Optional[Path] = None,
        on_confirm: Optional[Callable[[List[Dict[str, Any]]], None]] = None,
        on_cancel: Optional[Callable[[], None]] = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self.initial_segments = segments
        self.audio_path = audio_path
        self.on_confirm_callback = on_confirm
        self.on_cancel_callback = on_cancel

    def compose(self) -> ComposeResult:
        with Container(id="editor-container"):
            title = f"Lyric Editor - {self.audio_path.name}" if self.audio_path else "Lyric Editor"
            yield Label(f"[bold]{title}[/bold]", id="editor-title")
            yield LyricEditorWidget(id="lyric-editor-widget")

    def on_mount(self) -> None:
        editor = self.query_one("#lyric-editor-widget", LyricEditorWidget)
        editor.load_segments(self.initial_segments)

    @on(LyricEditorWidget.LyricsConfirmed)
    def on_lyrics_confirmed(self, event: LyricEditorWidget.LyricsConfirmed) -> None:
        if self.on_confirm_callback:
            self.on_confirm_callback(event.segments)


# =============================================================================
# Module Exports
# =============================================================================

__all__ = [
    "LyricEditorWidget",
    "LyricEditorScreen",
    "EditTextModal",
    "EditTimingModal",
]
