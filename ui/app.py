"""
DEPRECATED LEGACY UI
====================
This Textual UI is retained for reference only.
The maintained product entry points are `web_ui.py` and `api_server.py`.

Mellow-Video-Engine TUI Application
====================================
Textual 기반 터미널 UI 애플리케이션.

Human-in-the-loop 인터페이스로 비디오 생성 파이프라인을 제어합니다.

Backend Integration:
- LyricAligner: faster-whisper 기반 가사 추출
- VideoComposer: ffmpeg-python 기반 비디오 합성
- VideoService/ImageService: ComfyUI API 통신
"""

from __future__ import annotations

import asyncio
import logging
from enum import Enum
from pathlib import Path
from typing import Any, List, Optional, Dict

# MellowApp 코드 내부
from mellow_link.core.schemas import VideoRequest, ImageRequest
from mellow_link.services.video_service import VideoService
from mellow_link.services.image_service import ImageService

from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical
from textual.reactive import reactive
from textual.widgets import (
    Button,
    Footer,
    Header,
    Label,
    ProgressBar,
    RichLog,
    Static,
    TabbedContent,
    TabPane,
)

from .widgets.workflow_browser import WorkflowBrowser
from .widgets.param_editor import ParamEditor
from .screens.execution_screen import ExecutionScreen
from .lyric_editor import LyricEditorWidget, LyricEditorScreen

logger = logging.getLogger(__name__)


# =============================================================================
# Application State
# =============================================================================

class AppState(str, Enum):
    """애플리케이션 상태."""
    IDLE = "idle"
    TRANSCRIBING = "transcribing"
    EDITING = "editing"
    PLANNING = "planning"
    GENERATING = "generating"
    RENDERING = "rendering"
    DOWNLOADING = "downloading"
    ERROR = "error"
    CONNECTED = "connected"


STATE_COLORS = {
    AppState.IDLE: "dim",
    AppState.TRANSCRIBING: "magenta",
    AppState.EDITING: "blue",
    AppState.PLANNING: "yellow",
    AppState.GENERATING: "cyan",
    AppState.RENDERING: "green",
    AppState.DOWNLOADING: "blue",
    AppState.ERROR: "red",
    AppState.CONNECTED: "green",
}

STATE_LABELS = {
    AppState.IDLE: "Idle",
    AppState.TRANSCRIBING: "Transcribing Audio...",
    AppState.EDITING: "Editing Lyrics",
    AppState.PLANNING: "Planning Scenes...",
    AppState.GENERATING: "Generating Video...",
    AppState.RENDERING: "Rendering Final Video...",
    AppState.DOWNLOADING: "Downloading...",
    AppState.ERROR: "Error",
    AppState.CONNECTED: "Connected",
}


# =============================================================================
# Status Bar Widget
# =============================================================================

class StatusBar(Static):
    """하단 상태 바 위젯."""

    state: reactive[AppState] = reactive(AppState.IDLE)
    message: reactive[str] = reactive("")
    progress: reactive[float] = reactive(0.0)

    def compose(self) -> ComposeResult:
        with Horizontal(id="status-bar-content"):
            yield Label("", id="status-state")
            yield Label("", id="status-message")
            yield ProgressBar(total=100, show_eta=False, id="status-progress")

    def watch_state(self, state: AppState) -> None:
        """상태 변경 감시."""
        state_label = self.query_one("#status-state", Label)
        color = STATE_COLORS.get(state, "white")
        label = STATE_LABELS.get(state, str(state.value))
        state_label.update(f"[{color}][{label}][/{color}]")

    def watch_message(self, message: str) -> None:
        """메시지 변경 감시."""
        msg_label = self.query_one("#status-message", Label)
        msg_label.update(message)

    def watch_progress(self, progress: float) -> None:
        """진행률 변경 감시."""
        progress_bar = self.query_one("#status-progress", ProgressBar)
        progress_bar.update(progress=progress)


# =============================================================================
# Main Sidebar
# =============================================================================

class Sidebar(Container):
    """좌측 사이드바 - 워크플로우 브라우저."""

    def compose(self) -> ComposeResult:
        yield Label("[bold]Files[/bold]", id="sidebar-title")
        yield WorkflowBrowser(id="workflow-browser")
        yield Horizontal(
            Button("Refresh", id="btn-refresh", variant="default"),
            Button("Load", id="btn-load", variant="primary"),
            id="sidebar-buttons",
        )


# =============================================================================
# Main Content Area
# =============================================================================

class MainContent(Container):
    """메인 컨텐츠 영역."""

    def compose(self) -> ComposeResult:
        with TabbedContent(id="main-tabs"):
            with TabPane("Lyrics", id="tab-lyrics"):
                yield LyricEditorWidget(id="lyric-editor")
            with TabPane("Parameters", id="tab-params"):
                yield ParamEditor(id="param-editor")
            with TabPane("Logs", id="tab-logs"):
                yield RichLog(id="main-log", highlight=True, markup=True)
            with TabPane("Final Export & Preview", id="tab-preview"):
                # TUI에서 영상 자체를 렌더링하긴 어렵기 때문에,
                # "최종 검수실"로서 최신 결과물 경로/상태를 크게 보여주고 즉시 열 수 있게 한다.
                yield Static(
                    "[dim]아직 최종 결과물이 없습니다.[/dim]\n\n"
                    "1) Clips 생성\n"
                    "2) Render Video\n\n"
                    "완료 후 이 탭에서 최종 파일을 바로 열 수 있습니다.",
                    id="final-preview",
                )
                yield Button("Open Latest Export", id="btn-open-latest-export", variant="primary", disabled=True)


# =============================================================================
# Control Panel
# =============================================================================

class ControlPanel(Container):
    """우측 컨트롤 패널."""

    def compose(self) -> ComposeResult:
        yield Label("[bold]Pipeline[/bold]", id="control-title")

        with Vertical(id="control-buttons"):
            yield Button(
                "1. Transcribe Audio",
                id="btn-transcribe",
                variant="primary",
            )
            yield Button(
                "2. Plan Scenes",
                id="btn-plan",
                variant="primary",
                disabled=True,
            )
            yield Button(
                "3. Generate Images",
                id="btn-generate-images",
                variant="warning",
                disabled=True,
            )
            yield Button(
                "4. Generate Clips",
                id="btn-generate",
                variant="warning",
                disabled=True,
            )
            yield Button(
                "5. Render Video",
                id="btn-render",
                variant="success",
                disabled=True,
            )

            yield Static("", id="spacer")

            yield Button(
                "Connect ComfyUI",
                id="btn-connect",
                variant="default",
            )
            yield Button(
                "Free VRAM",
                id="btn-free-vram",
                variant="error",
            )
            yield Button(
                "Cancel",
                id="btn-cancel",
                variant="error",
                disabled=True,
            )

        yield Label("[bold]Status[/bold]", id="stats-title")
        yield Static("", id="stats-content")


# =============================================================================
# MellowApp - Main Application
# =============================================================================

class MellowApp(App):
    """
    Mellow-Video-Engine TUI 메인 애플리케이션.

    Features:
    - Reactive 상태 관리
    - 비동기/동기 작업 분리 (@work 데코레이터)
    - Human-in-the-loop 비디오 생성 제어
    - faster-whisper 가사 추출
    - ffmpeg-python 비디오 합성
    """

    TITLE = "Mellow Video Engine"
    SUB_TITLE = "AI Music Video Generator"

    CSS = """
    /* Layout */
    #main-container {
        layout: grid;
        grid-size: 3 1;
        grid-columns: 1fr 3fr 1fr;
    }

    /* Sidebar */
    Sidebar {
        width: 100%;
        height: 100%;
        border-right: solid $primary;
        padding: 1;
    }

    #sidebar-title {
        text-align: center;
        margin-bottom: 1;
    }

    #workflow-browser {
        height: 1fr;
        margin-bottom: 1;
    }

    #sidebar-buttons {
        height: auto;
        align: center middle;
    }

    #sidebar-buttons Button {
        margin: 0 1;
    }

    /* Main Content */
    MainContent {
        width: 100%;
        height: 100%;
        padding: 1;
    }

    #main-tabs {
        height: 100%;
    }

    #lyric-editor {
        height: 100%;
    }

    #param-editor {
        height: 100%;
    }

    #main-log {
        height: 100%;
        border: solid $primary;
    }

    /* Control Panel */
    ControlPanel {
        width: 100%;
        height: 100%;
        border-left: solid $primary;
        padding: 1;
    }

    #control-title, #stats-title {
        text-align: center;
        margin-bottom: 1;
    }

    #control-buttons {
        height: auto;
    }

    #control-buttons Button {
        width: 100%;
        margin-bottom: 1;
    }

    #spacer {
        height: 2;
    }

    #stats-content {
        margin-top: 1;
        padding: 1;
        border: solid $secondary;
    }

    /* Status Bar */
    StatusBar {
        dock: bottom;
        height: 3;
        border-top: solid $primary;
        padding: 0 1;
    }

    #status-bar-content {
        width: 100%;
        height: 100%;
        align: left middle;
    }

    #status-state {
        width: 25;
        text-align: center;
    }

    #status-message {
        width: 1fr;
        padding-left: 2;
    }

    #status-progress {
        width: 30;
    }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("r", "refresh", "Refresh"),
        Binding("t", "transcribe", "Transcribe"),
        Binding("g", "generate", "Generate"),
        Binding("escape", "cancel", "Cancel"),
        Binding("f1", "help", "Help"),
    ]

    # Reactive state
    app_state: reactive[AppState] = reactive(AppState.IDLE)
    current_audio: reactive[Optional[Path]] = reactive(None)
    current_workflow: reactive[Optional[Path]] = reactive(None)
    is_connected: reactive[bool] = reactive(False)

    def __init__(
        self,
        config_path: Optional[Path] = None,
        workflows_dir: Optional[Path] = None,
        output_dir: Optional[Path] = None,
        initial_audio: Optional[Path] = None,
    ):
        """
        Initialize MellowApp.

        Args:
            config_path: Path to settings.yaml configuration file
            workflows_dir: Directory containing workflow JSON files
            output_dir: Directory for generated output files
            initial_audio: Optional audio file to preload on startup
        """
        super().__init__()

        self.config_path = config_path or Path("config/settings.yaml")
        self.workflows_dir = workflows_dir or Path("workflows")
        self.output_dir = output_dir or Path("output")
        self.initial_audio = Path(initial_audio) if initial_audio else None

        # 백엔드 컴포넌트
        self.video_service: Optional[VideoService] = None
        self.image_service: Optional[ImageService] = None
        self.lyric_aligner: Optional["LyricAligner"] = None
        self.visual_planner: Optional["VisualPlanner"] = None
        self.video_composer: Optional["VideoComposer"] = None

        # 파이프라인 데이터
        self.lyrics_segments: List[Dict[str, Any]] = []
        self.scene_plans: List[Dict[str, Any]] = []
        self.generated_images: List[Path] = []
        self.generated_clips: List[Path] = []
        self.workflow_data: dict = {}
        self.latest_export_path: Optional[Path] = None
        self._button_default_labels: Dict[str, str] = {}

        # 로깅 핸들러
        self._log_handler: Optional[TUILogHandler] = None

    def compose(self) -> ComposeResult:
        yield Header()
        with Container(id="main-container"):
            yield Sidebar()
            yield MainContent()
            yield ControlPanel()
        yield StatusBar()
        yield Footer()

    async def on_mount(self) -> None:
        """앱 마운트 시 초기화."""
        self._setup_logging()
        self._log("Mellow-Video-Engine TUI started")
        self._log(f"Workflows: {self.workflows_dir}")
        self._log(f"Output: {self.output_dir}")

        # 디렉토리 설정
        self.output_dir.mkdir(parents=True, exist_ok=True)

        # 워크플로우 브라우저 경로 설정
        browser = self.query_one("#workflow-browser", WorkflowBrowser)
        browser.path = self.workflows_dir

        # 백엔드 초기화
        self._init_backends()

        # 버튼 기본 라벨 스냅샷 (UX 피드백용)
        try:
            for bid in ("#btn-generate-images", "#btn-generate", "#btn-render"):
                btn = self.query_one(bid, Button)
                self._button_default_labels[bid] = str(btn.label)
        except Exception:
            pass

        # 초기 오디오 파일 로드 (CLI에서 전달된 경우)
        if self.initial_audio and self.initial_audio.exists():
            self._log(f"Loading initial audio: {self.initial_audio.name}")
            self._handle_file_selection(self.initial_audio)

        # 초기 상태 업데이트
        self._update_stats()

        # Final Export & Preview: outputs/final/ 폴더 모니터링 (폴링)
        try:
            self.set_interval(2.0, self._poll_final_exports)
        except Exception:
            pass

    def _poll_final_exports(self) -> None:
        """
        (CRITICAL) 최종 검수실 경로 동기화:
        outputs/final/ 폴더에서 최신 mp4를 찾아 UI를 자동 갱신한다.
        """
        try:
            final_dir = Path("outputs") / "final"
            if not final_dir.exists():
                return
            candidates = list(final_dir.glob("*.mp4"))
            if not candidates:
                return
            latest = max(candidates, key=lambda p: p.stat().st_mtime)
            if self.latest_export_path and Path(self.latest_export_path).resolve() == latest.resolve():
                return
            self._set_final_export(latest)
        except Exception:
            return

    def _init_backends(self) -> None:
        """백엔드 컴포넌트 초기화."""
        # 1) New engine services (required for image/video generation)
        try:
            self.video_service = VideoService(
                host="127.0.0.1",
                port=8188,
                output_dir=self.output_dir / "videos",
            )
            self.image_service = ImageService(
                host="127.0.0.1",
                port=8188,
                output_dir=self.output_dir / "images",
            )
            self._log("Mellow-Link Services (Video/Image) initialized", "success")
        except Exception as e:
            self.video_service = None
            self.image_service = None
            self._log(f"Service init error: {e}", "error")

        # 2) Legacy components (optional)
        try:
            from OLD_backend.audio_engine import LyricAligner  # type: ignore
            from OLD_backend.video_engine import VideoComposer  # type: ignore

            self.lyric_aligner = LyricAligner(device="cuda", compute_type="float16")
            self.video_composer = VideoComposer()
            self._log("Legacy backend (LyricAligner/VideoComposer) initialized", "success")
        except Exception as e:
            self.lyric_aligner = None
            self.video_composer = None
            self._log(f"Legacy backend unavailable: {e}", "warning")

    def _setup_logging(self) -> None:
        """TUI 로그 핸들러 설정."""
        self._log_handler = TUILogHandler(self)
        self._log_handler.setLevel(logging.INFO)

        root_logger = logging.getLogger()
        # 서비스(mellow_link) 로그가 INFO 레벨로도 흐르도록 루트 레벨을 올린다.
        if root_logger.level > logging.INFO:
            root_logger.setLevel(logging.INFO)
        root_logger.addHandler(self._log_handler)

    def _log(self, message: str, level: str = "info") -> None:
        """로그 메시지 출력."""
        try:
            log_widget = self.query_one("#main-log", RichLog)
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

    # =========================================================================
    # UX Feedback Helpers (Buttons / Preview)
    # =========================================================================

    def _set_button_feedback(self, button_id: str, *, busy: bool, percent: Optional[float] = None) -> None:
        """
        ✅ verified:
          - 클릭 즉시 disabled
          - 라벨을 "Generating... (0%)" 형태로 갱신
        """
        try:
            btn = self.query_one(button_id, Button)
            if busy:
                btn.disabled = True
                pct = int(max(0.0, min(100.0, float(percent or 0.0))))
                btn.label = f"Generating... ({pct}%)"
            else:
                # 기본 라벨 복구
                btn.label = self._button_default_labels.get(button_id, str(btn.label))
                btn.disabled = False
        except Exception:
            pass

    def _set_final_export(self, path: Path) -> None:
        """Final Export & Preview 탭에 최신 결과물을 표시."""
        self.latest_export_path = Path(path)
        try:
            preview = self.query_one("#final-preview", Static)
            preview.update(
                "\n".join(
                    [
                        "[bold]최종 검수실[/bold]",
                        "",
                        f"[green]Latest Export:[/green] {self.latest_export_path.name}",
                        str(self.latest_export_path),
                        "",
                        "[dim]Open Latest Export 버튼으로 바로 재생하세요.[/dim]",
                    ]
                )
            )
            btn = self.query_one("#btn-open-latest-export", Button)
            btn.disabled = False
            tabs = self.query_one("#main-tabs", TabbedContent)
            tabs.active = "tab-preview"
        except Exception:
            pass

    def _update_status(
        self,
        state: Optional[AppState] = None,
        message: str = "",
        progress: float = 0.0,
    ) -> None:
        """상태 바 업데이트."""
        status_bar = self.query_one(StatusBar)
        if state:
            self.app_state = state
            status_bar.state = state
        status_bar.message = message
        status_bar.progress = progress

    def _update_stats(self) -> None:
        """통계 정보 업데이트."""
        stats = self.query_one("#stats-content", Static)
        lines = [
            f"Audio: {self.current_audio.name if self.current_audio else 'None'}",
            f"Lyrics: {len(self.lyrics_segments)} segments",
            f"Scenes: {len(self.scene_plans)} planned",
            f"Images: {len(self.generated_images)} generated",
            f"Clips: {len(self.generated_clips)} generated",
            f"ComfyUI: {'Connected' if self.is_connected else 'Disconnected'}",
        ]
        stats.update("\n".join(lines))

    def _update_pipeline_buttons(self) -> None:
        """파이프라인 버튼 상태 업데이트."""
        btn_transcribe = self.query_one("#btn-transcribe", Button)
        btn_plan = self.query_one("#btn-plan", Button)
        btn_generate_images = self.query_one("#btn-generate-images", Button)
        btn_generate = self.query_one("#btn-generate", Button)
        btn_render = self.query_one("#btn-render", Button)
        btn_cancel = self.query_one("#btn-cancel", Button)

        is_busy = self.app_state in (
            AppState.TRANSCRIBING,
            AppState.PLANNING,
            AppState.GENERATING,
            AppState.RENDERING,
        )

        # Transcribe: 오디오 파일 있을 때만
        btn_transcribe.disabled = is_busy or self.current_audio is None

        # Plan: 가사 있을 때만
        btn_plan.disabled = is_busy or len(self.lyrics_segments) == 0

        # Generate Images: 장면 기획 있고 연결됐을 때만
        btn_generate_images.disabled = is_busy or len(self.scene_plans) == 0 or not self.is_connected

        # Generate Clips: 장면 기획 있고 연결됐을 때만
        btn_generate.disabled = is_busy or len(self.scene_plans) == 0 or not self.is_connected

        # Render: 클립 있을 때만
        btn_render.disabled = is_busy or len(self.generated_clips) == 0

        # Cancel: 작업 중일 때만
        btn_cancel.disabled = not is_busy

    # =========================================================================
    # Reactive Watchers
    # =========================================================================

    def watch_app_state(self, state: AppState) -> None:
        """앱 상태 변경 감시."""
        self._update_stats()
        self._update_pipeline_buttons()

    def watch_is_connected(self, connected: bool) -> None:
        """연결 상태 변경 감시."""
        btn_connect = self.query_one("#btn-connect", Button)
        btn_connect.label = "Disconnect" if connected else "Connect ComfyUI"
        btn_connect.variant = "error" if connected else "default"

        if connected:
            self._update_status(AppState.CONNECTED, "Connected to ComfyUI")
        else:
            self._update_status(AppState.IDLE, "Disconnected")

        self._update_stats()
        self._update_pipeline_buttons()

    def watch_current_audio(self, audio: Optional[Path]) -> None:
        """오디오 파일 변경 감시."""
        self._update_stats()
        self._update_pipeline_buttons()

    # =========================================================================
    # Event Handlers
    # =========================================================================

    @on(Button.Pressed, "#btn-refresh")
    async def on_refresh_pressed(self) -> None:
        """Refresh 버튼."""
        browser = self.query_one("#workflow-browser", WorkflowBrowser)
        await browser.reload()
        self._log("File list refreshed")

    @on(Button.Pressed, "#btn-load")
    def on_load_pressed(self) -> None:
        """Load 버튼."""
        browser = self.query_one("#workflow-browser", WorkflowBrowser)
        selected = browser.get_selected_path()

        if selected:
            self._handle_file_selection(selected)

    @on(Button.Pressed, "#btn-transcribe")
    def on_transcribe_pressed(self) -> None:
        """Transcribe 버튼."""
        if self.current_audio:
            self._run_transcription()

    @on(Button.Pressed, "#btn-plan")
    def on_plan_pressed(self) -> None:
        """Plan Scenes 버튼."""
        if self.lyrics_segments:
            self._run_visual_planning()

    @on(Button.Pressed, "#btn-generate")
    def on_generate_pressed(self) -> None:
        """Generate Clips 버튼."""
        if self.scene_plans and self.is_connected:
            # ✅ verified: 즉시 UX 피드백
            self._set_button_feedback("#btn-generate", busy=True, percent=0.0)
            self._run_clip_generation()

    @on(Button.Pressed, "#btn-generate-images")
    def on_generate_images_pressed(self) -> None:
        """Generate Images 버튼."""
        if self.scene_plans and self.is_connected:
            # ✅ verified: 즉시 UX 피드백
            self._set_button_feedback("#btn-generate-images", busy=True, percent=0.0)
            self._run_image_generation()

    @on(Button.Pressed, "#btn-render")
    def on_render_pressed(self) -> None:
        """Render Video 버튼."""
        if self.generated_clips:
            self._set_button_feedback("#btn-render", busy=True, percent=0.0)
            self._run_video_render()

    @on(Button.Pressed, "#btn-open-latest-export")
    def on_open_latest_export_pressed(self) -> None:
        """Final Export 열기."""
        if self.latest_export_path and self.latest_export_path.exists():
            self._play_video(self.latest_export_path)

    @on(Button.Pressed, "#btn-connect")
    def on_connect_pressed(self) -> None:
        """Connect/Disconnect 버튼."""
        if self.is_connected:
            self._disconnect_services()
        else:
            self._connect_services()

    @on(Button.Pressed, "#btn-free-vram")
    def on_free_vram_pressed(self) -> None:
        """Free VRAM 버튼."""
        self._free_vram()

    @on(Button.Pressed, "#btn-cancel")
    def on_cancel_pressed(self) -> None:
        """Cancel 버튼."""
        self._cancel_operation()

    @on(WorkflowBrowser.FileSelected)
    def on_file_selected(self, event: WorkflowBrowser.FileSelected) -> None:
        """파일 선택됨."""
        self._handle_file_selection(event.path)

    @on(LyricEditorWidget.LyricsConfirmed)
    def on_lyrics_confirmed(self, event: LyricEditorWidget.LyricsConfirmed) -> None:
        """가사 확정됨."""
        self.lyrics_segments = event.segments
        self._log(f"Lyrics confirmed: {len(self.lyrics_segments)} segments", "success")
        self._update_stats()
        self._update_pipeline_buttons()
        self.notify("Lyrics saved! You can now plan scenes.")

    # =========================================================================
    # File Handling
    # =========================================================================

    def _handle_file_selection(self, path: Path) -> None:
        """파일 선택 처리."""
        suffix = path.suffix.lower()

        if suffix in {".mp3", ".wav", ".flac", ".m4a", ".ogg", ".aac"}:
            # 오디오 파일
            self.current_audio = path
            self._log(f"Audio selected: {path.name}", "success")
            self._update_stats()
            self._update_pipeline_buttons()
            self.notify(f"Audio loaded: {path.name}. Press 'Transcribe' to extract lyrics.")

        elif suffix == ".json":
            # 워크플로우 파일
            self._load_workflow(path)

        elif suffix in {".mp4", ".webm"}:
            # 비디오 파일 - 재생
            self._play_video(path)

    def _load_workflow(self, path: Path) -> None:
        """워크플로우 JSON 로드."""
        import json

        try:
            with open(path, "r", encoding="utf-8") as f:
                self.workflow_data = json.load(f)

            self.current_workflow = path
            self._log(f"Loaded workflow: {path.name}", "success")

            # 파라미터 에디터 업데이트
            editor = self.query_one("#param-editor", ParamEditor)
            editor.load_workflow(self.workflow_data)

            # 탭 전환
            tabs = self.query_one("#main-tabs", TabbedContent)
            tabs.active = "tab-params"

        except Exception as e:
            self._log(f"Failed to load workflow: {e}", "error")
            self.notify(f"Failed to load: {e}", severity="error")

    def _play_video(self, path: Path) -> None:
        """비디오 재생."""
        import subprocess
        import sys

        self._log(f"Playing video: {path.name}")

        try:
            if sys.platform == "win32":
                subprocess.Popen(["start", "", str(path)], shell=True)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(path)])
            else:
                subprocess.Popen(["xdg-open", str(path)])
        except Exception as e:
            self._log(f"Failed to play video: {e}", "error")

    # =========================================================================
    # Actions
    # =========================================================================

    def action_quit(self) -> None:
        """앱 종료."""
        self._log("Shutting down...")
        # best-effort: 서비스 연결 해제는 백그라운드로 처리
        try:
            if self.is_connected:
                self._disconnect_services()
        except Exception:
            pass
        self.exit()

    def action_refresh(self) -> None:
        """새로고침."""
        self.on_refresh_pressed()

    def action_transcribe(self) -> None:
        """음성 인식."""
        if self.current_audio:
            self._run_transcription()

    def action_generate(self) -> None:
        """생성 시작."""
        if self.scene_plans and self.is_connected:
            self._run_clip_generation()

    def action_cancel(self) -> None:
        """작업 취소."""
        self._cancel_operation()

    def action_help(self) -> None:
        """도움말."""
        self._log("=== Pipeline Flow ===")
        self._log("1. Select audio file (.mp3, .wav, etc.)")
        self._log("2. Transcribe -> Extract lyrics with timestamps")
        self._log("3. Edit lyrics in the Lyrics tab")
        self._log("4. Plan Scenes -> Generate visual plans from lyrics")
        self._log("5. Generate Images -> Create still images via ComfyUI")
        self._log("6. Generate Clips -> Create video clips via ComfyUI (image -> video)")
        self._log("7. Render Video -> Combine clips with transitions")

    # =========================================================================
    # Preview Helpers
    # =========================================================================

    def _set_preview_file(self, path: Path) -> None:
        """(호환) 기존 Preview 업데이트는 Final Export & Preview로 위임."""
        self._set_final_export(Path(path))

    # =========================================================================
    # Transcription (Thread-Safe)
    # =========================================================================

    @work(thread=True, exclusive=True, group="transcribe")
    def _run_transcription(self) -> None:
        """
        오디오 전사 실행.

        faster-whisper는 동기식이므로 @work(thread=True)로 실행.
        VRAM을 사용하므로 완료 후 자동 정리됨.
        """
        if not self.lyric_aligner or not self.current_audio:
            self.call_from_thread(self._log, "Transcriber not available", "error")
            return

        self.call_from_thread(
            self._update_status,
            AppState.TRANSCRIBING,
            "Transcribing audio...",
            0.0,
        )

        try:
            def progress_callback(progress: float, status: str) -> None:
                self.call_from_thread(
                    self._update_status,
                    AppState.TRANSCRIBING,
                    status,
                    progress * 100,
                )

            self.call_from_thread(
                self._log,
                f"Transcribing: {self.current_audio.name}",
            )

            # 전사 실행
            segments = self.lyric_aligner.transcribe(
                self.current_audio,
                model_size="large-v3",
                progress_callback=progress_callback,
            )

            # 결과 저장
            self.lyrics_segments = segments

            self.call_from_thread(
                self._log,
                f"Transcription complete: {len(segments)} segments",
                "success",
            )

            # 가사 에디터 업데이트
            editor = self.query_one("#lyric-editor", LyricEditorWidget)
            self.call_from_thread(editor.load_segments, segments)

            # 탭 전환
            tabs = self.query_one("#main-tabs", TabbedContent)
            self.call_from_thread(setattr, tabs, "active", "tab-lyrics")

            self.call_from_thread(
                self._update_status,
                AppState.EDITING,
                "Edit lyrics and confirm",
                100.0,
            )

            self.call_from_thread(
                self.notify,
                "Transcription complete! Edit lyrics in the Lyrics tab.",
            )

        except Exception as e:
            self.call_from_thread(self._log, f"Transcription failed: {e}", "error")
            self.call_from_thread(self._update_status, AppState.ERROR, str(e))
            self.call_from_thread(self.notify, f"Error: {e}", severity="error")

        finally:
            self.call_from_thread(self._update_stats)
            self.call_from_thread(self._update_pipeline_buttons)

    # =========================================================================
    # Visual Planning (Async)
    # =========================================================================

    @work(exclusive=True, group="planning")
    async def _run_visual_planning(self) -> None:
        """비주얼 플래닝 실행."""
        from mellow_link.services.visual_planner import VisualPlanner, PlannerConfig

        self._update_status(AppState.PLANNING, "Planning scenes...", 0.0)

        try:
            # lyrics_segments -> planner input
            segments_for_planning: List[Dict[str, Any]] = []
            for i, seg in enumerate(self.lyrics_segments):
                segments_for_planning.append(
                    {
                        "id": str(i),
                        "text": seg.get("text", ""),
                        "start_time": seg.get("start", 0),
                        "end_time": seg.get("end", 0),
                    }
                )

            meta = {
                "mood": "cinematic",
                "story": "",
            }

            planner = VisualPlanner(config=PlannerConfig(max_scenes=20, width=1216, height=704))
            # LLM이 살아있으면 시네마토그래퍼 페르소나로 plan_scenes_async가 우선 동작
            self.scene_plans = await planner.plan_scenes_async(
                lyrics_segments=segments_for_planning,
                metadata=meta,
                base_seed=None,
            )

            self._log(f"Planned {len(self.scene_plans)} scenes (story/context enabled)", "success")
            self._update_status(AppState.IDLE, "Planning complete", 100.0)
            self._update_stats()
            self._update_pipeline_buttons()
            self.notify(f"Scene planning complete! {len(self.scene_plans)} scenes ready.")

        except Exception as e:
            self._log(f"Planning error: {e}", "error")
            self._update_status(AppState.ERROR, str(e))
            self.notify(f"Planning failed: {e}", severity="error")

    # =========================================================================
    # Image / Clip Generation (Service Route)
    # =========================================================================

    @work(exclusive=True, group="generation")
    async def _run_image_generation(self) -> None:
        """장면별 원본 이미지 생성."""
        if not self.image_service or not self.scene_plans:
            self._log("Not ready to generate images", "error")
            self._set_button_feedback("#btn-generate-images", busy=False)
            return
        if not self.is_connected:
            self._log("Not connected to ComfyUI", "error")
            self._set_button_feedback("#btn-generate-images", busy=False)
            return

        self._update_status(AppState.GENERATING, "Generating images...", 0.0)

        self.generated_images = []
        total = len(self.scene_plans)

        try:
            for i, scene in enumerate(self.scene_plans):
                self._update_status(
                    AppState.GENERATING,
                    f"Generating image {i + 1}/{total}",
                    (i / max(total, 1)) * 100,
                )

                static_desc = scene.get("static_scene_description") or scene.get("static_prompt") or scene.get("visual_prompt") or scene.get("prompt") or ""
                shared = scene.get("shared_keywords") or ""
                static_prompt = ", ".join([p for p in [str(static_desc).strip(), str(shared).strip()] if str(p).strip()])
                negative = scene.get("negative_prompt") or ""
                if not str(static_prompt).strip():
                    self._log(f"Scene {i + 1}: missing static_prompt", "warning")
                    continue

                async def on_progress(progress: float, msg: str) -> None:
                    # ✅ verified: 버튼 라벨에 실시간 퍼센트 반영 (전체 진행률)
                    overall = ((i + (float(progress) / 100.0)) / max(total, 1)) * 100.0
                    self._set_button_feedback("#btn-generate-images", busy=True, percent=overall)
                    self._update_status(
                        AppState.GENERATING,
                        f"Image {i + 1}/{total}: {msg}",
                        float(progress),
                    )

                req = ImageRequest(
                    static_prompt=str(static_prompt).strip(),
                    prompt=str(static_prompt).strip(),
                    negative_prompt=str(negative) if negative else None,
                    width=1216,
                    height=704,
                    steps=20,
                    cfg_scale=7.0,
                    seed=int(scene.get("seed", -1)) if scene.get("seed", None) is not None else -1,
                    batch_size=1,
                    model=None,
                    workflow="flux_dev_api.json",
                    sampler_name="euler",
                    scheduler="normal",
                    denoise=1.0,
                )

                img_path_str = await self.image_service.generate_image(req, on_progress=on_progress)
                img_path = Path(img_path_str)
                self.generated_images.append(img_path)
                scene["image_path"] = str(img_path)
                self._log(f"Image {i + 1} generated: {img_path.name}", "success")
                self._set_final_export(img_path)

            self._log(f"Image generation complete: {len(self.generated_images)} images", "success")
            self._update_status(AppState.IDLE, "Image generation complete", 100.0)
        except Exception as e:
            self._log(f"Image generation error: {e}", "error")
            self._update_status(AppState.ERROR, str(e))
        finally:
            self._set_button_feedback("#btn-generate-images", busy=False)
            self._update_stats()
            self._update_pipeline_buttons()

    @work(exclusive=True, group="generation")
    async def _run_clip_generation(self) -> None:
        """클립 생성 실행 (VideoService Route)."""
        if not self.video_service or not self.scene_plans:
            self._log("Not ready to generate clips", "error")
            self._set_button_feedback("#btn-generate", busy=False)
            return
        if not self.is_connected:
            self._log("Not connected to ComfyUI", "error")
            self._set_button_feedback("#btn-generate", busy=False)
            return

        self._update_status(AppState.GENERATING, "Generating clips...", 0.0)

        try:
            self.generated_clips = []
            total = len(self.scene_plans)

            for i, scene in enumerate(self.scene_plans):
                self._update_status(
                    AppState.GENERATING,
                    f"Generating clip {i + 1}/{total}",
                    (i / max(total, 1)) * 100,
                )

                static_desc = scene.get("static_scene_description") or scene.get("static_prompt") or scene.get("visual_prompt") or scene.get("prompt") or ""
                dynamic_desc = scene.get("dynamic_action_description") or scene.get("motion_prompt") or ""
                shared = scene.get("shared_keywords") or ""
                static_prompt = ", ".join([p for p in [str(static_desc).strip(), str(shared).strip()] if str(p).strip()])
                motion_prompt = ", ".join([p for p in [str(dynamic_desc).strip(), str(shared).strip()] if str(p).strip()])
                negative = scene.get("negative_prompt") or ""

                # 이미지가 없으면(버튼 생략/순서 어긋남) 자동으로 생성해 안전하게 진행
                image_path = scene.get("image_path")
                if not image_path:
                    if not self.image_service:
                        raise RuntimeError("ImageService not available, but image_path is missing")
                    self._log(f"Scene {i + 1}: image_path missing -> generating image first", "warning")

                    async def on_img_progress(progress: float, msg: str) -> None:
                        overall = ((i + (float(progress) / 100.0)) / max(total, 1)) * 100.0
                        self._set_button_feedback("#btn-generate", busy=True, percent=overall)
                        self._update_status(
                            AppState.GENERATING,
                            f"Auto image {i + 1}/{total}: {msg}",
                            float(progress),
                        )

                    img_req = ImageRequest(
                        static_prompt=str(static_prompt).strip(),
                        prompt=str(static_prompt).strip(),
                        negative_prompt=str(negative) if negative else None,
                        width=1216,
                        height=704,
                        steps=20,
                        cfg_scale=7.0,
                        seed=int(scene.get("seed", -1)) if scene.get("seed", None) is not None else -1,
                        batch_size=1,
                        model=None,
                        workflow="flux_dev_api.json",
                        sampler_name="euler",
                        scheduler="normal",
                        denoise=1.0,
                    )
                    image_path = await self.image_service.generate_image(img_req, on_progress=on_img_progress)
                    scene["image_path"] = str(image_path)
                    p_img = Path(str(image_path))
                    self.generated_images.append(p_img)
                    self._set_final_export(p_img)

                async def on_vid_progress(progress: float, msg: str) -> None:
                    # VideoService progress는 best-effort (0~100)
                    overall = ((i + (float(progress) / 100.0)) / max(total, 1)) * 100.0
                    self._set_button_feedback("#btn-generate", busy=True, percent=overall)
                    self._update_status(
                        AppState.GENERATING,
                        f"Clip {i + 1}/{total}: {msg}",
                        float(progress),
                    )

                req = VideoRequest(
                    image_path=str(image_path),
                    motion_prompt=str(motion_prompt).strip() if motion_prompt else None,
                    prompt=str(motion_prompt or static_prompt).strip(),
                    mode="VIDEO_ONLY",          # ✅ verified
                    motion_bucket_id=int(scene.get("motion_bucket_id", 127)),
                    workflow="svd_xt_main.json",
                    width=1216,
                    height=704,
                    target_duration=12.0,       # ✅ verified
                    loop_mode="boomerang",
                    overlap_seconds=0.35,
                    fps=8,
                )

                out_path_str = await self.video_service.generate_video(req, on_progress=on_vid_progress)
                out_path = Path(out_path_str)
                self.generated_clips.append(out_path)

                self._log(f"Clip {i + 1} generated: {out_path.name}", "success")
                self._set_final_export(out_path)

            self._log(f"Clip generation complete: {len(self.generated_clips)} clips", "success")
            self._update_status(AppState.IDLE, "Clip generation complete", 100.0)

        except Exception as e:
            self._log(f"Clip generation error: {e}", "error")
            self._update_status(AppState.ERROR, str(e))

        finally:
            self._set_button_feedback("#btn-generate", busy=False)
            self._update_stats()
            self._update_pipeline_buttons()

    # =========================================================================
    # Video Rendering (Thread-Safe)
    # =========================================================================

    @work(thread=True, exclusive=True, group="render")
    def _run_video_render(self) -> None:
        """
        최종 비디오 렌더링.

        ffmpeg-python은 subprocess를 사용하므로 @work(thread=True)로 실행.
        실시간 진행률 콜백을 통해 ProgressBar 업데이트.
        """
        if not self.video_composer or not self.generated_clips:
            self.call_from_thread(self._log, "No clips to render", "error")
            return

        if not self.current_audio:
            self.call_from_thread(self._log, "No audio file", "error")
            return

        self.call_from_thread(
            self._update_status,
            AppState.RENDERING,
            "Rendering final video...",
            0.0,
        )

        try:
            # 자막 데이터 준비
            subtitles = []
            for seg in self.lyrics_segments:
                subtitles.append({
                    "text": seg.get("text", ""),
                    "start": seg.get("start", 0),
                    "end": seg.get("end", 0),
                })

            # 출력 파일 경로
            output_path = self.output_dir / f"mellow_final_{self.current_audio.stem}.mp4"

            def progress_callback(progress: float, status: str) -> None:
                # ✅ verified: Render 버튼 라벨에 진행률 표시
                self.call_from_thread(self._set_button_feedback, "#btn-render", busy=True, percent=float(progress))
                self.call_from_thread(
                    self._update_status,
                    AppState.RENDERING,
                    status,
                    progress,
                )

            self.call_from_thread(self._log, "Starting final render...")

            # 렌더링 실행
            success = self.video_composer.render(
                inputs=self.generated_clips,
                audio=self.current_audio,
                subtitles=subtitles,
                output=output_path,
                progress_callback=progress_callback,
            )

            if success:
                self.call_from_thread(
                    self._log,
                    f"Render complete: {output_path.name}",
                    "success",
                )
                self.call_from_thread(
                    self._update_status,
                    AppState.IDLE,
                    "Render complete!",
                    100.0,
                )
                self.call_from_thread(
                    self.notify,
                    f"Video saved: {output_path.name}",
                )

                # Final Export & Preview 업데이트
                self.call_from_thread(self._set_final_export, output_path)

                # 브라우저 새로고침
                browser = self.query_one("#workflow-browser", WorkflowBrowser)
                self.call_from_thread(browser.reload)

            else:
                self.call_from_thread(self._log, "Render failed", "error")
                self.call_from_thread(self._update_status, AppState.ERROR, "Render failed")

        except Exception as e:
            self.call_from_thread(self._log, f"Render error: {e}", "error")
            self.call_from_thread(self._update_status, AppState.ERROR, str(e))
            self.call_from_thread(self.notify, f"Render failed: {e}", severity="error")

        finally:
            self.call_from_thread(self._set_button_feedback, "#btn-render", busy=False)
            self.call_from_thread(self._update_stats)
            self.call_from_thread(self._update_pipeline_buttons)

    # =========================================================================
    # ComfyUI Connection (Service Route)
    # =========================================================================

    @work(exclusive=True, group="services")
    async def _connect_services(self) -> None:
        """ComfyUI 연결 (VideoService + ImageService)."""
        if not self.video_service or not self.image_service:
            self._log("Services not initialized", "error")
            self._update_status(AppState.ERROR, "Services not initialized")
            return

        self._update_status(AppState.IDLE, "Connecting to ComfyUI...", 0.0)
        try:
            await self.image_service.connect()
            await self.video_service.connect()
            self.is_connected = True
            self._log("Connected to ComfyUI (services)", "success")
            self._update_status(AppState.CONNECTED, "Connected", 100.0)
        except Exception as e:
            self.is_connected = False
            self._log(f"Service connection error: {e}", "error")
            self._update_status(AppState.ERROR, str(e))
        finally:
            self._update_stats()
            self._update_pipeline_buttons()

    @work(exclusive=True, group="services")
    async def _disconnect_services(self) -> None:
        """ComfyUI 연결 해제 (VideoService + ImageService)."""
        self._update_status(AppState.IDLE, "Disconnecting...", 0.0)
        try:
            if self.video_service:
                await self.video_service.disconnect()
            if self.image_service:
                await self.image_service.disconnect()
            self.is_connected = False
            self._log("Disconnected from ComfyUI (services)")
            self._update_status(AppState.IDLE, "Disconnected", 0.0)
        except Exception as e:
            self._log(f"Disconnect error: {e}", "warning")
            self.is_connected = False
        finally:
            self._update_stats()
            self._update_pipeline_buttons()

    @work(exclusive=True, group="services")
    async def _free_vram(self) -> None:
        """VRAM 정리 (서비스 + Whisper best-effort)."""
        self._log("Freeing VRAM...")
        try:
            if self.image_service:
                ok = await self.image_service.unload_model()
                if ok:
                    self._log("ComfyUI models unloaded (ImageService)", "success")
                else:
                    self._log("ComfyUI model unload skipped/failed", "warning")
        except Exception as e:
            self._log(f"ComfyUI VRAM free error: {e}", "warning")

        if self.lyric_aligner:
            try:
                self.lyric_aligner._cleanup_vram()
                self._log("Whisper VRAM freed", "success")
            except Exception as e:
                self._log(f"Whisper VRAM error: {e}", "warning")

    @work(exclusive=True, group="cancel")
    async def _cancel_operation(self) -> None:
        """작업 취소 (best-effort)."""
        self._log("Cancelling operation...", "warning")
        try:
            if self.video_service:
                _ = await self.video_service.interrupt()
            if self.image_service:
                _ = await self.image_service.interrupt()
        except Exception:
            pass
        self._update_status(AppState.IDLE, "Cancelled")
        self._update_pipeline_buttons()


# =============================================================================
# TUI Log Handler
# =============================================================================

class TUILogHandler(logging.Handler):
    """TUI RichLog 위젯으로 로그 전송."""

    def __init__(self, app: MellowApp):
        super().__init__()
        self.app = app

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            level = record.levelname.lower()

            if level == "error":
                level = "error"
            elif level == "warning":
                level = "warning"
            else:
                level = "info"

            if hasattr(self.app, "_log"):
                self.app.call_from_thread(self.app._log, msg, level)
        except Exception:
            pass


# =============================================================================
# Module Exports
# =============================================================================

__all__ = [
    "MellowApp",
    "AppState",
    "StatusBar",
    "Sidebar",
    "MainContent",
    "ControlPanel",
    "TUILogHandler",
]


# =============================================================================
# Development Entry Point
# =============================================================================

def run_app(
    config_path: Optional[Path] = None,
    workflows_dir: Optional[Path] = None,
    output_dir: Optional[Path] = None,
    initial_audio: Optional[Path] = None,
) -> None:
    """
    Run the MellowApp TUI.

    This function is provided for programmatic launching and testing.
    For production use, use the main.py entry point.

    Args:
        config_path: Path to settings.yaml
        workflows_dir: Workflows directory
        output_dir: Output directory
        initial_audio: Optional audio file to preload
    """
    app = MellowApp(
        config_path=config_path,
        workflows_dir=workflows_dir,
        output_dir=output_dir,
        initial_audio=initial_audio,
    )
    app.run()


if __name__ == "__main__":
    # Development entry point - allows running `python -m ui.app` directly
    import sys
    from pathlib import Path

    # Parse simple arguments for development
    initial_audio = None
    if len(sys.argv) > 1:
        audio_path = Path(sys.argv[1])
        if audio_path.exists():
            initial_audio = audio_path

    run_app(initial_audio=initial_audio)
