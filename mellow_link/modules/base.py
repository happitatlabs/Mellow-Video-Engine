from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from fastapi import APIRouter


@dataclass(frozen=True)
class ModuleManifest:
    module_id: str
    name: str
    description: str
    run_kind: str
    start_path: str
    icon: str = "[]"
    visible_in_ui: bool = True


@dataclass(frozen=True)
class RegisteredModule:
    manifest: ModuleManifest
    router: APIRouter
    base_dir: Path
    readme_path: Optional[Path] = None
