"""
Execution Screen
================
비디오 생성 중 실시간 진행률과 로그를 보여주는 화면.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Optional

from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import (
    Button,
    Footer,
    Header,
    Label,
    ProgressBar,
    RichLog,
    Static,
)

if TYPE_CHECKING:
    from modules.comfy_video_agent import ComfyVideoAgent, VideoGenerationParams


# =============================================================================
# Progress Widget
# =============================================================================

class ProgressPanel(Container):
    """진행률 패널."""

    CSS = """
    ProgressPanel {
        height: auto;
        padding: 1;
        border: solid $primary;
        margin-bottom: 1;
    }

    #progress-title {
        text-align: center;
        margin-bottom: 1;
    }

    #progress-bar {
        margin-bottom: 1;
    }

    #progress-stats {
        height: auto;
    }

    .stat-row {
        height: auto;
    }

    .stat-label {
        width: 15;
    }

    .stat-value {
        width: 1fr;
    }
    """

    def compose(self) -> ComposeResult:
        yield Label("[bold]Generation Progress[/bold]", id="progress-title")
        yield ProgressBar(total=100, show_eta=True, id="progress-bar")

        with Container(id="progress-stats"):
            with Horizontal(classes="stat-row"):
                yield Label("Status:", classes="stat-label")
                yield Label("Waiting...", id="stat-status", classes="stat-value")

            with Horizontal(classes="stat-row"):
                yield Label("Current Node:", classes="stat-label")
                yield Label("-", id="stat-node", classes="stat-value")

            with Horizontal(classes="stat-row"):
                yield Label("Step:", classes="stat-label")
                yield Label("0 / 0", id="stat-step", classes="stat-value")

            with Horizontal(classes="stat-row"):
                yield Label("Elapsed:", classes="stat-label")
                yield Label("0:00", id="stat-elapsed", classes="stat-value")

    def update_progress(
        self,
        progress: float,
        status: str = "",
        node: str = "",
        step_current: int = 0,
        step_total: int = 0,
        elapsed: float = 0.0,
    ) -> None:
        """진행률 업데이트."""
        # 프로그레스 바
        bar = self.query_one("#progress-bar", ProgressBar)
        bar.update(progress=progress)

        # 상태
        if status:
            stat_status = self.query_one("#stat-status", Label)
            stat_status.update(status)

        # 현재 노드
        if node:
            stat_node = self.query_one("#stat-node", Label)
            stat_node.update(node)

        # 스텝
        stat_step = self.query_one("#stat-step", Label)
        stat_step.update(f"{step_current} / {step_total}")

        # 경과 시간
        stat_elapsed = self.query_one("#stat-elapsed", Label)
        minutes = int(elapsed // 60)
        seconds = int(elapsed % 60)
        stat_elapsed.update(f"{minutes}:{seconds:02d}")


# =============================================================================
# Control Buttons
# =============================================================================

class ExecutionControls(Container):
    """실행 제어 버튼."""

    CSS = """
    ExecutionControls {
        height: auto;
        padding: 1;
        align: center middle;
    }

    ExecutionControls Button {
        margin: 0 1;
    }
    """

    def compose(self) -> ComposeResult:
        yield Button(
            "Cancel Generation",
            id="btn-cancel-exec",
            variant="error",
        )
        yield Button(
            "Free VRAM",
            id="btn-free-vram-exec",
            variant="warning",
        )
        yield Button(
            "Close",
            id="btn-close-exec",
            variant="default",
            disabled=True,
        )


# =============================================================================
# Execution Screen
# =============================================================================

class ExecutionScreen(Screen):
    """
    비디오 생성 실행 화면.

    Features:
    - 실시간 진행률 표시
    - RichLog로 로그 스트리밍
    - 긴급 정지 (VRAM 정리) 버튼
    - 작업 완료 후 결과 표시
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("q", "close", "Close"),
    ]

    CSS = """
    ExecutionScreen {
        background: $surface;
    }

    #exec-container {
        width: 100%;
        height: 100%;
        padding: 1;
    }

    #exec-title {
        text-align: center;
        margin-bottom: 1;
    }

    #exec-log {
        height: 1fr;
        border: solid $primary;
    }

    #exec-result {
        height: auto;
        padding: 1;
        border: solid $success;
        margin-top: 1;
        display: none;
    }

    #exec-result.visible {
        display: block;
    }
    """

    def __init__(
        self,
        agent: Optional["ComfyVideoAgent"] = None,
        params: Optional["VideoGenerationParams"] = None,
        workflow_type: str = "ltx_video",
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        super().__init__(name=name, id=id, classes=classes)
        self.agent = agent
        self.params = params
        self.workflow_type = workflow_type

        self._is_running = False
        self._is_cancelled = False
        self._start_time = 0.0
        self._output_files: list[Path] = []

    def compose(self) -> ComposeResult:
        yield Header()

        with Container(id="exec-container"):
            yield Label(
                f"[bold]Video Generation - {self.workflow_type.upper()}[/bold]",
                id="exec-title",
            )
            yield ProgressPanel(id="progress-panel")
            yield RichLog(id="exec-log", highlight=True, markup=True)
            yield Static("", id="exec-result")
            yield ExecutionControls()

        yield Footer()

    async def on_mount(self) -> None:
        """마운트 시 생성 시작."""
        self._log("Execution screen mounted")
        self._log(f"Workflow type: {self.workflow_type}")

        if self.agent and self.params:
            self._start_generation()

    def _log(self, message: str, level: str = "info") -> None:
        """로그 출력."""
        try:
            log_widget = self.query_one("#exec-log", RichLog)

            if level == "error":
                log_widget.write(f"[red][ERROR][/red] {message}")
            elif level == "warning":
                log_widget.write(f"[yellow][WARN][/yellow] {message}")
            elif level == "success":
                log_widget.write(f"[green][OK][/green] {message}")
            else:
                log_widget.write(f"[dim][INFO][/dim] {message}")
        except Exception:
            pass

    def _update_progress(
        self,
        progress: float,
        status: str = "",
        node: str = "",
        step_current: int = 0,
        step_total: int = 0,
    ) -> None:
        """진행률 업데이트."""
        import time

        panel = self.query_one("#progress-panel", ProgressPanel)
        elapsed = time.time() - self._start_time if self._start_time else 0

        panel.update_progress(
            progress=progress,
            status=status,
            node=node,
            step_current=step_current,
            step_total=step_total,
            elapsed=elapsed,
        )

    @work(thread=True, exclusive=True, group="execution")
    def _start_generation(self) -> None:
        """
        비디오 생성 시작.

        ComfyVideoAgent는 동기식이므로 @work(thread=True)로 실행.
        """
        import time
        from modules.comfy_video_agent import WorkflowType

        self._is_running = True
        self._start_time = time.time()

        self.call_from_thread(self._log, "Starting video generation...")
        self.call_from_thread(
            self._update_progress,
            0.0,
            "Initializing...",
        )

        try:
            # 워크플로우 타입 결정
            if self.workflow_type.lower() == "svd":
                wf_type = WorkflowType.SVD
            else:
                wf_type = WorkflowType.LTX_VIDEO

            # 진행률 콜백
            def progress_callback(current: int, total: int, node_id: str) -> None:
                if self._is_cancelled:
                    return

                progress = (current / total) * 100 if total > 0 else 0

                self.call_from_thread(
                    self._update_progress,
                    progress,
                    "Generating...",
                    node_id,
                    current,
                    total,
                )

                self.call_from_thread(
                    self._log,
                    f"Step {current}/{total} - Node: {node_id}",
                )

            # 생성 실행
            self.call_from_thread(self._log, f"Starting {wf_type.value}...")

            result = self.agent.generate_video(
                workflow_type=wf_type,
                params=self.params,
                progress_callback=progress_callback,
            )

            # 결과 처리
            if result.success:
                self._output_files = result.output_files

                self.call_from_thread(
                    self._log,
                    f"Generation completed in {result.execution_time:.1f}s",
                    "success",
                )

                for f in result.output_files:
                    self.call_from_thread(self._log, f"  Output: {f}", "success")

                self.call_from_thread(
                    self._update_progress,
                    100.0,
                    "Complete!",
                )

                self.call_from_thread(self._show_result, True)

            else:
                self.call_from_thread(
                    self._log,
                    f"Generation failed: {result.error_message}",
                    "error",
                )

                self.call_from_thread(
                    self._update_progress,
                    0.0,
                    f"Failed: {result.error_message}",
                )

                self.call_from_thread(self._show_result, False)

        except Exception as e:
            self.call_from_thread(self._log, f"Error: {e}", "error")
            self.call_from_thread(self._update_progress, 0.0, f"Error: {e}")
            self.call_from_thread(self._show_result, False)

        finally:
            self._is_running = False
            # Close 버튼 활성화
            btn_close = self.query_one("#btn-close-exec", Button)
            self.call_from_thread(setattr, btn_close, "disabled", False)

    def _show_result(self, success: bool) -> None:
        """결과 표시."""
        result_widget = self.query_one("#exec-result", Static)

        if success:
            lines = ["[bold green]Generation Complete![/bold green]\n"]
            lines.append(f"Output files: {len(self._output_files)}")
            for f in self._output_files:
                lines.append(f"  - {f.name}")
            result_widget.update("\n".join(lines))
        else:
            result_widget.update("[bold red]Generation Failed[/bold red]")

        result_widget.add_class("visible")

    @on(Button.Pressed, "#btn-cancel-exec")
    def on_cancel_pressed(self) -> None:
        """Cancel 버튼."""
        self._cancel_generation()

    @on(Button.Pressed, "#btn-free-vram-exec")
    def on_free_vram_pressed(self) -> None:
        """Free VRAM 버튼."""
        self._free_vram()

    @on(Button.Pressed, "#btn-close-exec")
    def on_close_pressed(self) -> None:
        """Close 버튼."""
        self.app.pop_screen()

    def action_cancel(self) -> None:
        """ESC 키."""
        if self._is_running:
            self._cancel_generation()
        else:
            self.app.pop_screen()

    def action_close(self) -> None:
        """Q 키."""
        if not self._is_running:
            self.app.pop_screen()

    @work(thread=True)
    def _cancel_generation(self) -> None:
        """생성 취소."""
        self._is_cancelled = True
        self.call_from_thread(self._log, "Cancelling generation...", "warning")

        if self.agent:
            try:
                self.agent.cancel_current()
                self.call_from_thread(self._log, "Generation cancelled")
            except Exception as e:
                self.call_from_thread(self._log, f"Cancel error: {e}", "error")

        self.call_from_thread(self._update_progress, 0.0, "Cancelled")

    @work(thread=True)
    def _free_vram(self) -> None:
        """VRAM 정리."""
        self.call_from_thread(self._log, "Freeing VRAM...", "warning")

        if self.agent:
            try:
                self.agent._clear_vram()
                self.call_from_thread(self._log, "VRAM freed successfully", "success")
            except Exception as e:
                self.call_from_thread(self._log, f"Failed to free VRAM: {e}", "error")


# =============================================================================
# Screen Exports
# =============================================================================

__all__ = [
    "ExecutionScreen",
    "ProgressPanel",
]
