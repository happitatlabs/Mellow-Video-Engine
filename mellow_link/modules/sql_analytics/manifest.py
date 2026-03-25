from __future__ import annotations

from pathlib import Path

from mellow_link.modules.base import ModuleManifest, RegisteredModule
from mellow_link.modules.registry import ModuleRegistry

from .api import router

MANIFEST = ModuleManifest(
    module_id="sql_analytics",
    name="SQL Analytics",
    description="자연어 질문을 규칙/템플릿 기반 SQL 분석 run으로 실행합니다.",
    run_kind="sql_analysis",
    start_path="/modules/sql_analytics",
    icon="DB",
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
