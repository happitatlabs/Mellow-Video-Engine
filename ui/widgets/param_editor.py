"""
Parameter Editor Widget
=======================
DataTable 기반 파라미터 편집기.
셀 선택 후 Enter로 ModalScreen을 통해 값 수정.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple, Union

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Input, Label, Static


# =============================================================================
# Edit Modal Screen
# =============================================================================

class EditValueModal(ModalScreen[Optional[str]]):
    """값 편집용 모달 화면."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("enter", "submit", "Submit"),
    ]

    CSS = """
    EditValueModal {
        align: center middle;
    }

    #edit-modal-container {
        width: 60;
        height: auto;
        padding: 1 2;
        background: $surface;
        border: thick $primary;
    }

    #edit-modal-title {
        text-align: center;
        margin-bottom: 1;
    }

    #edit-modal-key {
        margin-bottom: 1;
        color: $text-muted;
    }

    #edit-modal-input {
        margin-bottom: 1;
    }

    #edit-modal-buttons {
        height: auto;
        align: center middle;
    }

    #edit-modal-buttons Button {
        margin: 0 1;
    }
    """

    def __init__(
        self,
        key: str,
        current_value: str,
        value_type: str = "string",
    ) -> None:
        super().__init__()
        self.key = key
        self.current_value = current_value
        self.value_type = value_type

    def compose(self) -> ComposeResult:
        with Container(id="edit-modal-container"):
            yield Label("[bold]Edit Parameter[/bold]", id="edit-modal-title")
            yield Label(f"Key: [cyan]{self.key}[/cyan]", id="edit-modal-key")
            yield Input(
                value=self.current_value,
                placeholder="Enter new value...",
                id="edit-modal-input",
            )
            with Container(id="edit-modal-buttons"):
                yield Button("Save", variant="primary", id="btn-save")
                yield Button("Cancel", variant="default", id="btn-cancel")

    def on_mount(self) -> None:
        """마운트 시 입력 필드에 포커스."""
        input_widget = self.query_one("#edit-modal-input", Input)
        input_widget.focus()

    @on(Button.Pressed, "#btn-save")
    def on_save(self) -> None:
        """저장."""
        input_widget = self.query_one("#edit-modal-input", Input)
        self.dismiss(input_widget.value)

    @on(Button.Pressed, "#btn-cancel")
    def on_cancel(self) -> None:
        """취소."""
        self.dismiss(None)

    def action_cancel(self) -> None:
        """ESC 키."""
        self.dismiss(None)

    def action_submit(self) -> None:
        """Enter 키."""
        input_widget = self.query_one("#edit-modal-input", Input)
        self.dismiss(input_widget.value)


# =============================================================================
# Parameter Editor Widget
# =============================================================================

class ParamEditor(Container):
    """
    워크플로우 파라미터 편집기.

    Features:
    - DataTable로 (Key, Value, Type) 표시
    - Enter 키로 ModalScreen 팝업하여 값 수정
    - JSON 워크플로우 구조를 평면화하여 표시
    - 수정된 값을 원본 구조에 반영
    """

    BINDINGS = [
        Binding("enter", "edit_cell", "Edit"),
        Binding("r", "reset", "Reset"),
    ]

    CSS = """
    ParamEditor {
        height: 100%;
    }

    #param-table {
        height: 1fr;
    }

    #param-info {
        height: auto;
        padding: 1;
        border-top: solid $primary;
    }
    """

    # 편집 가능한 노드 타입 및 속성
    EDITABLE_NODE_TYPES = {
        "KSampler": ["seed", "steps", "cfg", "denoise"],
        "CLIPTextEncode": ["text"],
        "LTXVGemmaEnhancePrompt": ["text"],
        "EmptyLatentImage": ["width", "height", "batch_size"],
        "SVD_img2vid_Conditioning": ["motion_bucket_id", "augmentation_level", "video_frames"],
        "LoadImage": ["image"],
        "VHS_VideoCombine": ["frame_rate", "filename_prefix"],
        "SaveVideo": ["filename_prefix"],
        "SaveAnimatedWEBP": ["filename_prefix"],
    }

    # 값 타입 힌트
    VALUE_TYPES = {
        "seed": "int",
        "steps": "int",
        "cfg": "float",
        "denoise": "float",
        "width": "int",
        "height": "int",
        "batch_size": "int",
        "frame_rate": "int",
        "video_frames": "int",
        "motion_bucket_id": "int",
        "augmentation_level": "float",
        "text": "string",
        "filename_prefix": "string",
        "image": "string",
    }

    def __init__(
        self,
        *,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
        disabled: bool = False,
    ) -> None:
        super().__init__(name=name, id=id, classes=classes, disabled=disabled)

        # 원본 워크플로우 데이터
        self._workflow_data: Dict[str, Any] = {}

        # 평면화된 파라미터 목록: [(node_id, key, value, value_type), ...]
        self._params: List[Tuple[str, str, Any, str]] = []

        # 수정된 값 추적
        self._modified: Dict[Tuple[str, str], Any] = {}

    def compose(self) -> ComposeResult:
        yield DataTable(id="param-table", cursor_type="row")
        yield Static("", id="param-info")

    def on_mount(self) -> None:
        """마운트 시 테이블 초기화."""
        table = self.query_one("#param-table", DataTable)
        table.add_columns("Node", "Parameter", "Value", "Type")
        table.zebra_stripes = True

    def load_workflow(self, workflow_data: Dict[str, Any]) -> None:
        """
        워크플로우 JSON 로드 및 파라미터 추출.

        Args:
            workflow_data: ComfyUI 워크플로우 JSON
        """
        self._workflow_data = workflow_data
        self._params = []
        self._modified = {}

        # 파라미터 추출
        for node_id, node_data in workflow_data.items():
            if not isinstance(node_data, dict):
                continue

            # _comment 같은 메타 필드 스킵
            if node_id.startswith("_"):
                continue

            class_type = node_data.get("class_type", "Unknown")
            inputs = node_data.get("inputs", {})

            # 편집 가능한 속성 확인
            editable_keys = self.EDITABLE_NODE_TYPES.get(class_type, [])

            for key, value in inputs.items():
                # 링크(배열)는 스킵
                if isinstance(value, list):
                    continue

                # 편집 가능한 속성인지 확인
                if class_type in self.EDITABLE_NODE_TYPES:
                    if key not in editable_keys:
                        continue

                value_type = self.VALUE_TYPES.get(key, "string")
                self._params.append((node_id, key, value, value_type))

        # 테이블 업데이트
        self._refresh_table()

    def _refresh_table(self) -> None:
        """테이블 새로고침."""
        table = self.query_one("#param-table", DataTable)
        table.clear()

        for node_id, key, value, value_type in self._params:
            # 수정된 값이 있으면 사용
            modified_key = (node_id, key)
            if modified_key in self._modified:
                display_value = self._modified[modified_key]
                # 수정된 값은 하이라이트
                display_value = f"[bold cyan]{display_value}[/bold cyan]"
            else:
                display_value = str(value)

            # 긴 텍스트 truncate
            if len(str(display_value)) > 40:
                display_value = str(display_value)[:37] + "..."

            node_label = self._get_node_label(node_id)
            table.add_row(node_label, key, display_value, value_type, key=f"{node_id}:{key}")

    def _get_node_label(self, node_id: str) -> str:
        """노드 ID에서 레이블 생성."""
        node_data = self._workflow_data.get(node_id, {})
        class_type = node_data.get("class_type", "Unknown")
        return f"[{node_id}] {class_type}"

    @on(DataTable.RowSelected)
    def on_row_selected(self, event: DataTable.RowSelected) -> None:
        """행 선택 시 정보 표시."""
        table = self.query_one("#param-table", DataTable)
        info = self.query_one("#param-info", Static)

        if event.row_key:
            row_key = str(event.row_key.value)
            node_id, key = row_key.split(":", 1)

            # 원본 값 찾기
            for nid, k, value, vtype in self._params:
                if nid == node_id and k == key:
                    info.update(
                        f"[dim]Node:[/dim] {node_id} | "
                        f"[dim]Key:[/dim] {key} | "
                        f"[dim]Type:[/dim] {vtype} | "
                        f"[dim]Value:[/dim] {value}"
                    )
                    break

    def action_edit_cell(self) -> None:
        """선택된 셀 편집."""
        table = self.query_one("#param-table", DataTable)

        if table.cursor_row is None:
            return

        # 현재 행의 키 가져오기
        row_key = table.get_row_at(table.cursor_row)
        if not row_key:
            return

        # 행 키에서 node_id와 key 추출
        cursor_row = table.cursor_row
        row_keys = list(table.rows.keys())

        if cursor_row >= len(row_keys):
            return

        row_key_value = str(row_keys[cursor_row].value)
        node_id, key = row_key_value.split(":", 1)

        # 현재 값 찾기
        current_value = ""
        value_type = "string"

        for nid, k, value, vtype in self._params:
            if nid == node_id and k == key:
                # 수정된 값이 있으면 그것을 사용
                modified_key = (node_id, key)
                if modified_key in self._modified:
                    current_value = str(self._modified[modified_key])
                else:
                    current_value = str(value)
                value_type = vtype
                break

        # 모달 표시
        def handle_result(result: Optional[str]) -> None:
            if result is not None:
                self._apply_edit(node_id, key, result, value_type)

        self.app.push_screen(
            EditValueModal(key, current_value, value_type),
            handle_result,
        )

    def _apply_edit(
        self,
        node_id: str,
        key: str,
        new_value: str,
        value_type: str,
    ) -> None:
        """편집 결과 적용."""
        # 타입 변환
        try:
            if value_type == "int":
                converted = int(new_value)
            elif value_type == "float":
                converted = float(new_value)
            else:
                converted = new_value
        except ValueError:
            converted = new_value

        # 수정 추적
        self._modified[(node_id, key)] = converted

        # 원본 워크플로우에도 반영
        if node_id in self._workflow_data:
            inputs = self._workflow_data[node_id].get("inputs", {})
            if key in inputs:
                inputs[key] = converted

        # 테이블 새로고침
        self._refresh_table()

    def action_reset(self) -> None:
        """모든 수정 초기화."""
        self._modified.clear()
        self._refresh_table()

    def get_params(self) -> Dict[str, Any]:
        """
        현재 파라미터 값 반환.

        Returns:
            주요 파라미터 딕셔너리
        """
        result = {}

        for node_id, key, value, vtype in self._params:
            modified_key = (node_id, key)

            # 수정된 값 우선
            if modified_key in self._modified:
                final_value = self._modified[modified_key]
            else:
                final_value = value

            # 주요 파라미터만 추출
            if key in ("seed", "text", "prompt", "motion_bucket_id", "batch_size", "num_frames"):
                # 특수 매핑
                if key == "text":
                    result["prompt"] = final_value
                elif key == "batch_size":
                    result["num_frames"] = final_value
                else:
                    result[key] = final_value

        return result

    def get_modified_workflow(self) -> Dict[str, Any]:
        """
        수정된 워크플로우 데이터 반환.

        Returns:
            수정이 반영된 워크플로우 JSON
        """
        return self._workflow_data.copy()


# =============================================================================
# Widget Exports
# =============================================================================

__all__ = [
    "ParamEditor",
    "EditValueModal",
]
