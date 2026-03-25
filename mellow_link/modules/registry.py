from __future__ import annotations

from typing import Dict, List

from .base import RegisteredModule


class ModuleRegistry:
    def __init__(self) -> None:
        self._modules: Dict[str, RegisteredModule] = {}

    def register(self, module: RegisteredModule) -> None:
        self._modules[module.manifest.module_id] = module

    def list_modules(self) -> List[RegisteredModule]:
        return list(self._modules.values())

    def get(self, module_id: str) -> RegisteredModule | None:
        return self._modules.get(module_id)


_registry: ModuleRegistry | None = None


def get_module_registry() -> ModuleRegistry:
    global _registry
    if _registry is not None:
        return _registry

    registry = ModuleRegistry()

    from .sql_analytics.manifest import register_module as register_sql_analytics
    from .research_assistant.manifest import register_module as register_research_assistant
    from .ai_workflow_console.manifest import register_module as register_ai_workflow_console
    from .rebuild_assistant.manifest import register_module as register_rebuild_assistant

    register_sql_analytics(registry)
    register_research_assistant(registry)
    register_ai_workflow_console(registry)
    register_rebuild_assistant(registry)

    _registry = registry
    return registry
