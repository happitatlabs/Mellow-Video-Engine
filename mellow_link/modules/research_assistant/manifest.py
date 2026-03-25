from __future__ import annotations

from pathlib import Path

from mellow_link.modules.base import ModuleManifest, RegisteredModule
from mellow_link.modules.registry import ModuleRegistry

from .api import router

MANIFEST = ModuleManifest(
    module_id="research_assistant",
    name="Research Assistant",
    description="문서/RAG 기반 분석 run을 생성하는 리서치 모듈입니다.",
    run_kind="research_run",
    start_path="/modules/research_assistant",
    icon="RG",
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
