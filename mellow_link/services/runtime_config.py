"""
Shared runtime configuration helpers for the maintained web/API flow.

This module centralizes:
- config/settings.yaml loading
- config/prompts.yaml loading
- unified output directory mapping
- prompt metadata policy flags
"""

from __future__ import annotations

import logging
import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict

import yaml


logger = logging.getLogger(__name__)


def _discover_repo_root() -> Path:
    candidates = [
        Path.cwd(),
        Path(os.getenv("MELLOW_VIDEO_ENGINE_ROOT", "")).resolve() if os.getenv("MELLOW_VIDEO_ENGINE_ROOT") else None,
        Path(__file__).resolve().parents[2],
        Path(__file__).resolve().parents[1],
    ]
    for candidate in candidates:
        if candidate and (candidate / "config" / "settings.yaml").exists():
            return candidate.resolve()
    return Path.cwd().resolve()


_REPO_ROOT = _discover_repo_root()
_CONFIG_DIR = _REPO_ROOT / "config"
_SETTINGS_PATH = _CONFIG_DIR / "settings.yaml"
_PROMPTS_PATH = _CONFIG_DIR / "prompts.yaml"


def _read_yaml(path: Path) -> Dict[str, Any]:
    if not path.exists():
        logger.warning("[runtime_config] Missing config file: %s. Falling back to defaults.", path)
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception as e:
        logger.warning("[runtime_config] Failed to read config file %s: %s. Falling back to defaults.", path, e)
        return {}
    return data if isinstance(data, dict) else {}


@lru_cache(maxsize=1)
def load_settings() -> Dict[str, Any]:
    data = _read_yaml(_SETTINGS_PATH)
    if not data:
        logger.warning("[runtime_config] settings.yaml unavailable or empty. Runtime will use built-in defaults.")
    return data


@lru_cache(maxsize=1)
def load_prompts_config() -> Dict[str, Any]:
    data = _read_yaml(_PROMPTS_PATH)
    if not data:
        logger.warning("[runtime_config] prompts.yaml unavailable or empty. Planner will use fallback prompt policy.")
    return data


def get_outputs_root(settings: Dict[str, Any] | None = None) -> Path:
    data = settings or load_settings()
    outputs = data.get("outputs", {}) if isinstance(data, dict) else {}
    root = str(outputs.get("root") or "./outputs")
    return (_REPO_ROOT / root).resolve() if not Path(root).is_absolute() else Path(root).resolve()


def get_comfyui_endpoint(settings: Dict[str, Any] | None = None) -> Dict[str, Any]:
    data = settings or load_settings()
    comfy = data.get("comfyui", {}) if isinstance(data, dict) else {}
    host = str(comfy.get("host") or "127.0.0.1").strip() or "127.0.0.1"
    port = int(comfy.get("port") or 8188)
    use_ssl = bool(comfy.get("use_ssl", False))
    timeout = float(comfy.get("timeout") or 300)
    scheme = "https" if use_ssl else "http"
    ws_scheme = "wss" if use_ssl else "ws"
    return {
        "host": host,
        "port": port,
        "use_ssl": use_ssl,
        "timeout": timeout,
        "base_url": f"{scheme}://{host}:{port}",
        "ws_url": f"{ws_scheme}://{host}:{port}/ws",
    }


def get_output_directories(settings: Dict[str, Any] | None = None) -> Dict[str, Path]:
    data = settings or load_settings()
    outputs = data.get("outputs", {}) if isinstance(data, dict) else {}
    root = get_outputs_root(data)
    mapping = {
        "root": root,
        "uploads": root / str(outputs.get("uploads_dir") or "uploads"),
        "transcripts": root / str(outputs.get("transcripts_dir") or "transcripts"),
        "images": root / str(outputs.get("images_dir") or "images"),
        "videos": root / str(outputs.get("videos_dir") or "videos"),
        "final": root / str(outputs.get("final_dir") or "final"),
    }
    for key, path in mapping.items():
        try:
            path.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            raise RuntimeError(
                f"Failed to prepare output directory '{key}' at '{path}'. "
                f"Check filesystem permissions and outputs.root in config/settings.yaml."
            ) from e
    return mapping


def should_strip_prompt_metadata(settings: Dict[str, Any] | None = None) -> bool:
    data = settings or load_settings()
    outputs = data.get("outputs", {}) if isinstance(data, dict) else {}
    return bool(outputs.get("strip_prompt_metadata", False))


def get_video_generation_settings(settings: Dict[str, Any] | None = None) -> Dict[str, Any]:
    data = settings or load_settings()
    comfy = data.get("comfyui", {}) if isinstance(data, dict) else {}
    comfy_video = comfy.get("video_generation", {}) if isinstance(comfy, dict) else {}
    video = data.get("video", {}) if isinstance(data, dict) else {}
    ambient = video.get("ambient_loop", {}) if isinstance(video, dict) else {}
    motion = video.get("motion_video", {}) if isinstance(video, dict) else {}
    merged = {
        "default_mode": str(video.get("default_mode") or "LOCAL_MOTION_LOOP"),
        "locked_camera_mode": bool(video.get("locked_camera_mode", False)),
        "locked_camera_backend": str(video.get("locked_camera_backend") or "ambient_loop"),
        "locked_camera_workflow": str(video.get("locked_camera_workflow") or "svd_xt_locked_camera.json"),
        "local_motion_workflow": str(motion.get("workflow") or "ltx_2b_v0_9_ckpt_i2v_lowmem.json"),
        "local_motion_width": int(motion.get("width", 576) or 576),
        "local_motion_height": int(motion.get("height", 320) or 320),
        "local_motion_frames": int(motion.get("frames", 17) or 17),
        "local_motion_fps": int(motion.get("fps", 6) or 6),
        "local_motion_duration": float(motion.get("duration_seconds", 2.83) or 2.83),
        "stabilize_zoom_drift": bool(video.get("stabilize_zoom_drift", False)),
        "stabilization_strength": float(video.get("stabilization_strength", 0.18) or 0.18),
        "ambient_motion_strength": float(ambient.get("motion_strength", video.get("ambient_motion_strength", 0.34)) or 0.34),
        "ambient_debug_visualization": bool(ambient.get("debug_visualization", False)),
        "ambient_visibility_mode": str(ambient.get("visibility_mode") or "visibility_first"),
        "ambient_min_patch_alpha": float(ambient.get("min_patch_alpha", 0.14) or 0.14),
        "ambient_min_patch_shift_px": float(ambient.get("min_patch_shift_px", 6.0) or 6.0),
        "ambient_min_light_pulse": float(ambient.get("min_light_pulse", 0.55) or 0.55),
        "motion_bucket_id": int(video.get("motion_bucket_id_locked", comfy_video.get("motion_bucket_id", 1)) or 1),
        "augmentation_level": float(video.get("augmentation_level_locked", 0.0) or 0.0),
        "video_frames": int(video.get("video_frames_locked", comfy_video.get("frames", 21)) or 21),
        "raw_fps": int(video.get("raw_fps_locked", 7) or 7),
        "output_fps": int(video.get("output_fps", 8) or 8),
        "sampler_steps_locked": int(video.get("sampler_steps_locked", 14) or 14),
        "sampler_cfg_locked": float(video.get("sampler_cfg_locked", 1.5) or 1.5),
    }
    return merged


def get_motion_video_spike_settings(settings: Dict[str, Any] | None = None) -> Dict[str, Any]:
    data = settings or load_settings()
    video = data.get("video", {}) if isinstance(data, dict) else {}
    spike = video.get("motion_video_spike", {}) if isinstance(video, dict) else {}
    comfy_root = str(spike.get("comfy_root") or "D:/AI_Hub/Models/ComfyUI_windows_portable/ComfyUI")
    model_search_dirs = spike.get("model_search_dirs") or ["models/unet", "models/diffusion_models", "models/checkpoints"]
    if not isinstance(model_search_dirs, list):
        model_search_dirs = ["models/unet", "models/diffusion_models", "models/checkpoints"]
    return {
        "engine": str(spike.get("engine") or "ltx_local_2b_v0_9"),
        "enabled": bool(spike.get("enabled", False)),
        "workflow": str(spike.get("workflow") or "ltx_2b_v0_9_i2v_lowmem.json"),
        "comfy_root": comfy_root,
        "model_file": str(spike.get("model_file") or "ltx-video-2b-v0.9.safetensors"),
        "model_search_dirs": [str(x) for x in model_search_dirs],
        "clip_file": str(spike.get("clip_file") or "t5xxl_fp8_e4m3fn.safetensors"),
        "vae_file": str(spike.get("vae_file") or "ae.safetensors"),
        "width": int(spike.get("width", 576) or 576),
        "height": int(spike.get("height", 320) or 320),
        "length": int(spike.get("length", 17) or 17),
        "fps": int(spike.get("fps", 6) or 6),
        "duration_seconds": float(spike.get("duration_seconds", 2.83) or 2.83),
        "steps": int(spike.get("steps", 20) or 20),
        "cfg": float(spike.get("cfg", 3.0) or 3.0),
        "sampler": str(spike.get("sampler") or "euler"),
        "scheduler": str(spike.get("scheduler") or "simple"),
        "strength": float(spike.get("strength", 0.75) or 0.75),
        "unet_weight_dtype": str(spike.get("unet_weight_dtype") or "fp8_e4m3fn_fast"),
        "clip_device": str(spike.get("clip_device") or "cpu"),
        "reserve_vram_gb": int(spike.get("reserve_vram_gb", 4) or 4),
    }


def is_media_ai_enabled() -> bool:
    try:
        from mellow_link.config.settings import get_settings

        return bool(get_settings().allow_media_ai())
    except Exception as e:
        logger.warning("[runtime_config] Failed to resolve ENABLE_MEDIA_AI from mellow_link.config.settings: %s", e)
        raw = (os.getenv("ENABLE_MEDIA_AI", "") or "").strip().lower()
        return raw in {"1", "true", "yes", "on", "enabled"}
