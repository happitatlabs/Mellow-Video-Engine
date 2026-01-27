"""
Mellow-Video-Engine TUI Application
====================================
Textual 기반 터미널 UI 애플리케이션.

Human-in-the-loop 인터페이스로 비디오 생성 파이프라인을 제어합니다.

Backend Integration:
- LyricAligner: faster-whisper 기반 가사 추출
- VideoComposer: ffmpeg-python 기반 비디오 합성
- ComfyVideoAgent: ComfyUI API 통신
"""

from __future__ import annotations

import asyncio
import logging
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, List, Optional, Dict

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

if TYPE_CHECKING:
    from modules.visual_planner import VisualPlanner
    from modules.comfy_video_agent import ComfyVideoAgent, ComfyConfig
    from backend.audio_engine import LyricAligner
    from backend.video_engine import VideoComposer

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
            with TabPane("Preview", id="tab-preview"):
                yield Static(
                    "[dim]Select a video file to preview[/dim]",
                    id="preview-placeholder",
                )


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
                "3. Generate Clips",
                id="btn-generate",
                variant="warning",
                disabled=True,
            )
            yield Button(
                "4. Render Video",
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
        self.lyric_aligner: Optional["LyricAligner"] = None
        self.video_composer: Optional["VideoComposer"] = None
        self.visual_planner: Optional["VisualPlanner"] = None
        self.comfy_agent: Optional["ComfyVideoAgent"] = None
        self.comfy_config = None

        # 파이프라인 데이터
        self.lyrics_segments: List[Dict[str, Any]] = []
        self.scene_plans: List[Dict[str, Any]] = []
        self.generated_clips: List[Path] = []
        self.workflow_data: dict = {}

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

        # 초기 오디오 파일 로드 (CLI에서 전달된 경우)
        if self.initial_audio and self.initial_audio.exists():
            self._log(f"Loading initial audio: {self.initial_audio.name}")
            self._handle_file_selection(self.initial_audio)

        # 초기 상태 업데이트
        self._update_stats()

    def _init_backends(self) -> None:
        """백엔드 컴포넌트 초기화."""
        try:
            from backend.audio_engine import LyricAligner
            from backend.video_engine import VideoComposer

            self.lyric_aligner = LyricAligner(device="cuda", compute_type="float16")
            self.video_composer = VideoComposer()

            self._log("Backend engines initialized", "success")
        except ImportError as e:
            self._log(f"Backend import error: {e}", "warning")
        except Exception as e:
            self._log(f"Backend init error: {e}", "error")

    def _setup_logging(self) -> None:
        """TUI 로그 핸들러 설정."""
        self._log_handler = TUILogHandler(self)
        self._log_handler.setLevel(logging.INFO)

        root_logger = logging.getLogger()
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
            f"Clips: {len(self.generated_clips)} generated",
            f"ComfyUI: {'Connected' if self.is_connected else 'Disconnected'}",
        ]
        stats.update("\n".join(lines))

    def _update_pipeline_buttons(self) -> None:
        """파이프라인 버튼 상태 업데이트."""
        btn_transcribe = self.query_one("#btn-transcribe", Button)
        btn_plan = self.query_one("#btn-plan", Button)
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

        # Generate: 장면 기획 있고 ComfyUI 연결됐을 때만
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
            self._run_clip_generation()

    @on(Button.Pressed, "#btn-render")
    def on_render_pressed(self) -> None:
        """Render Video 버튼."""
        if self.generated_clips:
            self._run_video_render()

    @on(Button.Pressed, "#btn-connect")
    def on_connect_pressed(self) -> None:
        """Connect/Disconnect 버튼."""
        if self.is_connected:
            self._disconnect_comfy()
        else:
            self._connect_comfy()

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
        if self.comfy_agent:
            self.comfy_agent.disconnect()
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
        self._log("5. Generate Clips -> Create video clips via ComfyUI")
        self._log("6. Render Video -> Combine clips with transitions")

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
        from modules.visual_planner import VisualPlanner, LLMConfig

        self._update_status(AppState.PLANNING, "Planning scenes...", 0.0)

        try:
            llm_config = LLMConfig(
                provider="ollama",
                base_url="http://localhost:11434",
                model_name="llama3.1:8b",
            )

            prompts_config = {}
            prompts_path = Path("config/prompts.yaml")
            if prompts_path.exists():
                import yaml
                with open(prompts_path, "r", encoding="utf-8") as f:
                    prompts_config = yaml.safe_load(f)

            planner = VisualPlanner(
                llm_config=llm_config,
                prompts_config=prompts_config,
                mock=True,  # TODO: 실제 사용 시 False
            )
            await planner.initialize()

            # 세그먼트를 SegmentInfo 형식으로 변환
            segments_for_planning = []
            for i, seg in enumerate(self.lyrics_segments):
                segments_for_planning.append({
                    "id": str(i),
                    "text": seg.get("text", ""),
                    "start_time": seg.get("start", 0),
                    "end_time": seg.get("end", 0),
                    "confidence": seg.get("confidence", 1.0),
                })

            async def progress(current: int, total: int, plan) -> None:
                progress_pct = (current / total) * 100
                self._update_status(
                    AppState.PLANNING,
                    f"Planning scene {current}/{total}",
                    progress_pct,
                )

            scenes = await planner.plan_scenes(
                segments=segments_for_planning,
                global_mood="cinematic",
                progress_callback=progress,
            )

            self.scene_plans = [s.model_dump() if hasattr(s, 'model_dump') else s for s in scenes]

            self._log(f"Planned {len(self.scene_plans)} scenes", "success")
            self._update_status(AppState.IDLE, "Planning complete", 100.0)
            self._update_stats()
            self._update_pipeline_buttons()

            self.notify(f"Scene planning complete! {len(self.scene_plans)} scenes ready.")

            await planner.cleanup()

        except Exception as e:
            self._log(f"Planning error: {e}", "error")
            self._update_status(AppState.ERROR, str(e))
            self.notify(f"Planning failed: {e}", severity="error")

    # =========================================================================
    # Clip Generation (Thread-Safe)
    # =========================================================================

    @work(thread=True, exclusive=True, group="generation")
    def _run_clip_generation(self) -> None:
        """클립 생성 실행."""
        from modules.comfy_video_agent import VideoGenerationParams, WorkflowType

        if not self.comfy_agent or not self.scene_plans:
            self.call_from_thread(self._log, "Not ready to generate", "error")
            return

        self.call_from_thread(
            self._update_status,
            AppState.GENERATING,
            "Generating clips...",
            0.0,
        )

        try:
            self.generated_clips = []
            total = len(self.scene_plans)

            for i, scene in enumerate(self.scene_plans):
                self.call_from_thread(
                    self._update_status,
                    AppState.GENERATING,
                    f"Generating clip {i + 1}/{total}",
                    (i / total) * 100,
                )

                params = VideoGenerationParams(
                    prompt=scene.get("visual_prompt", ""),
                    negative_prompt=scene.get("negative_prompt", ""),
                    num_frames=97,
                    output_prefix=f"mellow_clip_{i:03d}",
                    output_dir=self.output_dir,
                )

                result = self.comfy_agent.generate_video(
                    workflow_type=WorkflowType.LTX_VIDEO,
                    params=params,
                )

                if result.success and result.output_files:
                    self.generated_clips.extend(result.output_files)
                    self.call_from_thread(
                        self._log,
                        f"Clip {i + 1} generated: {result.output_files[0].name}",
                        "success",
                    )
                else:
                    self.call_from_thread(
                        self._log,
                        f"Clip {i + 1} failed: {result.error_message}",
                        "error",
                    )

            self.call_from_thread(
                self._log,
                f"Generation complete: {len(self.generated_clips)} clips",
                "success",
            )

            self.call_from_thread(
                self._update_status,
                AppState.IDLE,
                "Generation complete",
                100.0,
            )

        except Exception as e:
            self.call_from_thread(self._log, f"Generation error: {e}", "error")
            self.call_from_thread(self._update_status, AppState.ERROR, str(e))

        finally:
            self.call_from_thread(self._update_stats)
            self.call_from_thread(self._update_pipeline_buttons)

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
            self.call_from_thread(self._update_stats)
            self.call_from_thread(self._update_pipeline_buttons)

    # =========================================================================
    # ComfyUI Connection (Thread-Safe)
    # =========================================================================

    @work(thread=True, exclusive=True, group="comfy")
    def _connect_comfy(self) -> None:
        """ComfyUI 연결."""
        from modules.comfy_video_agent import ComfyVideoAgent, ComfyConfig

        self.call_from_thread(
            self._update_status,
            AppState.IDLE,
            "Connecting to ComfyUI...",
        )

        try:
            self.comfy_config = ComfyConfig(
                host="127.0.0.1",
                port=8188,
                ltx_workflow_path=self.workflows_dir / "ltx_video_api.json",
                svd_workflow_path=self.workflows_dir / "svd_api.json",
            )

            self.comfy_agent = ComfyVideoAgent(self.comfy_config)
            connected = self.comfy_agent.connect()

            if connected:
                self.call_from_thread(self._log, "Connected to ComfyUI", "success")
                self.call_from_thread(setattr, self, "is_connected", True)
            else:
                self.call_from_thread(self._log, "Failed to connect", "error")
                self.call_from_thread(self._update_status, AppState.ERROR, "Connection failed")

        except Exception as e:
            self.call_from_thread(self._log, f"Connection error: {e}", "error")
            self.call_from_thread(self._update_status, AppState.ERROR, str(e))

    @work(thread=True, exclusive=True, group="comfy")
    def _disconnect_comfy(self) -> None:
        """ComfyUI 연결 해제."""
        if self.comfy_agent:
            try:
                self.comfy_agent.disconnect()
                self.call_from_thread(self._log, "Disconnected from ComfyUI")
            except Exception as e:
                self.call_from_thread(self._log, f"Disconnect error: {e}", "warning")
            finally:
                self.comfy_agent = None

        self.call_from_thread(setattr, self, "is_connected", False)

    @work(thread=True)
    def _free_vram(self) -> None:
        """VRAM 정리."""
        self.call_from_thread(self._log, "Freeing VRAM...")

        # ComfyUI VRAM 정리
        if self.comfy_agent:
            try:
                self.comfy_agent._clear_vram()
                self.call_from_thread(self._log, "ComfyUI VRAM freed", "success")
            except Exception as e:
                self.call_from_thread(self._log, f"ComfyUI VRAM error: {e}", "warning")

        # LyricAligner VRAM 정리
        if self.lyric_aligner:
            try:
                self.lyric_aligner._cleanup_vram()
                self.call_from_thread(self._log, "Whisper VRAM freed", "success")
            except Exception as e:
                self.call_from_thread(self._log, f"Whisper VRAM error: {e}", "warning")

    def _cancel_operation(self) -> None:
        """작업 취소."""
        self._log("Cancelling operation...", "warning")

        if self.comfy_agent:
            try:
                self.comfy_agent.cancel_current()
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
