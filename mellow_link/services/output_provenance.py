"""
Output provenance helpers for maintained runtime artifacts.

Writes sidecar JSON files next to generated outputs without changing the output
file format itself.
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from mellow_link.services.runtime_config import (
    load_prompts_config,
    load_settings,
    should_strip_prompt_metadata,
)


logger = logging.getLogger(__name__)


def sidecar_path_for(output_path: str | Path) -> Path:
    target = Path(output_path).resolve()
    return target.with_suffix(target.suffix + ".meta.json")


def sidecar_exists(output_path: str | Path) -> bool:
    return sidecar_path_for(output_path).exists()


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def config_fingerprint() -> Dict[str, str]:
    settings_json = json.dumps(load_settings(), ensure_ascii=False, sort_keys=True)
    prompts_json = json.dumps(load_prompts_config(), ensure_ascii=False, sort_keys=True)
    return {
        "settings_sha256": _sha256_text(settings_json),
        "prompts_sha256": _sha256_text(prompts_json),
    }


def build_provenance_record(
    *,
    artifact_type: str,
    output_path: str | Path,
    source: Dict[str, Any],
    runtime: Optional[Dict[str, Any]] = None,
    request: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    fingerprints = config_fingerprint()
    payload = {
        "artifact_type": str(artifact_type),
        "output_path": str(Path(output_path).resolve()),
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "source": source,
        "runtime": {
            "official_entrypoints": ["web_ui.py", "api_server.py"],
            "planner_module": "mellow_link.services.visual_planner.VisualPlanner",
            "policy_source": "config/prompts.yaml",
            "strip_prompt_metadata": should_strip_prompt_metadata(),
            **fingerprints,
        },
        "request": request or {},
    }
    if runtime:
        payload["runtime"].update(runtime)
    return payload


def _build_minimal_fallback_record(
    *,
    artifact_type: str,
    output_path: str | Path,
    source: Optional[Dict[str, Any]] = None,
    runtime: Optional[Dict[str, Any]] = None,
    request: Optional[Dict[str, Any]] = None,
    error: Optional[str] = None,
) -> Dict[str, Any]:
    return {
        "artifact_type": str(artifact_type),
        "output_path": str(Path(output_path).resolve()),
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "source": source or {},
        "runtime": {
            "official_entrypoints": ["web_ui.py", "api_server.py"],
            "planner_module": "mellow_link.services.visual_planner.VisualPlanner",
            "policy_source": "config/prompts.yaml",
            "strip_prompt_metadata": should_strip_prompt_metadata(),
            "fallback_sidecar": True,
            **(runtime or {}),
        },
        "request": request or {},
        "warnings": [f"fallback_sidecar_written:{error}"] if error else ["fallback_sidecar_written"],
    }


def write_sidecar(
    output_path: str | Path,
    *,
    artifact_type: str,
    source: Dict[str, Any],
    runtime: Optional[Dict[str, Any]] = None,
    request: Optional[Dict[str, Any]] = None,
) -> Path:
    target = Path(output_path).resolve()
    sidecar = sidecar_path_for(target)
    payload = build_provenance_record(
        artifact_type=artifact_type,
        output_path=target,
        source=source,
        runtime=runtime,
        request=request,
    )
    sidecar.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return sidecar


def write_sidecar_best_effort(
    output_path: str | Path,
    *,
    artifact_type: str,
    source: Dict[str, Any],
    runtime: Optional[Dict[str, Any]] = None,
    request: Optional[Dict[str, Any]] = None,
) -> Path:
    target = Path(output_path).resolve()
    sidecar = sidecar_path_for(target)
    try:
        return write_sidecar(
            target,
            artifact_type=artifact_type,
            source=source,
            runtime=runtime,
            request=request,
        )
    except Exception as exc:
        logger.warning("[output_provenance] Primary sidecar write failed for %s: %s", target, exc)
        fallback = _build_minimal_fallback_record(
            artifact_type=artifact_type,
            output_path=target,
            source=source,
            runtime=runtime,
            request=request,
            error=str(exc),
        )
        sidecar.write_text(json.dumps(fallback, ensure_ascii=False, indent=2), encoding="utf-8")
        return sidecar


def ensure_sidecar(
    output_path: str | Path,
    *,
    artifact_type: str,
    source: Dict[str, Any],
    runtime: Optional[Dict[str, Any]] = None,
    request: Optional[Dict[str, Any]] = None,
) -> Path:
    target = Path(output_path).resolve()
    if not target.exists():
        raise FileNotFoundError(f"Cannot create sidecar for missing artifact: {target}")
    sidecar = sidecar_path_for(target)
    if sidecar.exists():
        return sidecar
    logger.warning("[output_provenance] Missing sidecar for %s. Recreating.", target)
    return write_sidecar_best_effort(
        target,
        artifact_type=artifact_type,
        source=source,
        runtime=runtime,
        request=request,
    )


def move_artifact_with_sidecar(output_path: str | Path, destination_dir: str | Path) -> Dict[str, Path]:
    src = Path(output_path).resolve()
    if not src.exists():
        raise FileNotFoundError(f"Artifact not found: {src}")
    dst_dir = Path(destination_dir).resolve()
    dst_dir.mkdir(parents=True, exist_ok=True)

    dst_artifact = dst_dir / src.name
    shutil.move(str(src), str(dst_artifact))

    src_sidecar = sidecar_path_for(src)
    moved_sidecar = None
    if src_sidecar.exists():
        moved_sidecar = dst_dir / src_sidecar.name
        shutil.move(str(src_sidecar), str(moved_sidecar))
    else:
        logger.warning("[output_provenance] Artifact moved without existing sidecar: %s", src)

    return {
        "artifact": dst_artifact,
        "sidecar": moved_sidecar if moved_sidecar is not None else sidecar_path_for(dst_artifact),
    }


def archive_artifact_with_sidecar(
    output_path: str | Path,
    *,
    archive_root: str | Path,
    label: str,
    stamp: Optional[str] = None,
) -> Dict[str, Path]:
    archive_stamp = str(stamp or datetime.now().strftime("%Y%m%d"))
    destination = Path(archive_root).resolve() / f"{archive_stamp}_{label}"
    return move_artifact_with_sidecar(output_path, destination)
