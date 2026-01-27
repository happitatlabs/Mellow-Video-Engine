"""
Mellow-Video-Engine UI Module
=============================
Textual 기반 터미널 UI 컴포넌트.

Components:
- MellowApp: 메인 애플리케이션
- Widgets: 커스텀 위젯 (WorkflowBrowser, ParamEditor)
- Screens: 화면 (ExecutionScreen)
"""

from .app import MellowApp, AppState
from .widgets import WorkflowBrowser, ParamEditor, EditValueModal
from .screens import ExecutionScreen, ProgressPanel

# Legacy exports (backward compatibility)
from .lyric_editor import LyricEditorWidget
from .asset_selector import AssetSelectorWidget

__all__ = [
    # Main App
    "MellowApp",
    "AppState",
    # Widgets
    "WorkflowBrowser",
    "ParamEditor",
    "EditValueModal",
    # Screens
    "ExecutionScreen",
    "ProgressPanel",
    # Legacy
    "LyricEditorWidget",
    "AssetSelectorWidget",
]
