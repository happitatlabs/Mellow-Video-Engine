from __future__ import annotations

from pathlib import Path

from mellow_link.modules.base import ModuleManifest, RegisteredModule
from mellow_link.modules.registry import ModuleRegistry

from .api import router

MANIFEST = ModuleManifest(
    module_id="rebuild_assistant",
    name="Rebuild Assistant",
    description="레거시 기능을 분석해 현대화 재구성 초안을 만드는 모듈입니다.",
    run_kind="rebuild_plan",
    start_path="/modules/rebuild_assistant",
    icon="RB",
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
