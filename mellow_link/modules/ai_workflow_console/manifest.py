from __future__ import annotations

from pathlib import Path

from mellow_link.modules.base import ModuleManifest, RegisteredModule
from mellow_link.modules.registry import ModuleRegistry

from .api import router

MANIFEST = ModuleManifest(
    module_id="ai_workflow_console",
    name="AI Workflow Console",
    description="이미지/비디오/생성 작업 run을 시작하고 관리하는 모듈입니다.",
    run_kind="workflow_run",
    start_path="/modules/ai_workflow_console",
    icon="WF",
    visible_in_ui=False,
)


def register_module(registry: ModuleRegistry) -> None:
    base_dir = Path(__file__).resolve().parent
    registry.register(
        RegisteredModule(
            manifest=MANIFEST,
            router=router,
            base_dir=base_dir,
            readme_path=base_dir / "README.md",
        )
    )
