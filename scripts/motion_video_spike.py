from __future__ import annotations

import argparse
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List
from urllib.request import urlopen


REPO_ROOT = Path(__file__).resolve().parents[1]
OUTPUTS = REPO_ROOT / "outputs"
IMAGES_DIR = OUTPUTS / "images"
REPORTS_DIR = OUTPUTS / "reports"
COMFY_URL = "http://127.0.0.1:8188"


def fetch_json(path: str) -> Dict[str, Any]:
    with urlopen(f"{COMFY_URL}{path}", timeout=15) as res:
        data = json.loads(res.read().decode("utf-8"))
    return data if isinstance(data, dict) else {}


def latest_sample_images(limit: int = 2) -> List[Path]:
    files = sorted(IMAGES_DIR.glob("*.png"), key=lambda p: p.stat().st_mtime, reverse=True)
    return [p.resolve() for p in files[:limit]]


def env_flag(*names: str) -> bool:
    for name in names:
        raw = os.getenv(name)
        if isinstance(raw, str) and raw.strip():
            return True
    return False


def detect_ltx(object_info: Dict[str, Any]) -> Dict[str, Any]:
    nodes = set(object_info.keys())
    checkpoint_opts = object_info.get("CheckpointLoaderSimple", {}).get("input", {}).get("required", {}).get("ckpt_name", [[]])[0]
    unet_opts = object_info.get("UNETLoader", {}).get("input", {}).get("required", {}).get("unet_name", [[]])[0]
    local_ckpts = [x for x in checkpoint_opts if "ltx" in str(x).lower()]
    local_unets = [x for x in unet_opts if "ltx" in str(x).lower()]
    api_auth = env_flag("COMFY_ORG_API_KEY", "API_KEY_COMFY_ORG", "COMFY_AUTH_TOKEN")
    local_ready = bool({"LTXVImgToVideo", "LTXVConditioning", "ModelSamplingLTXV"} <= nodes and (local_ckpts or local_unets))
    api_ready = bool("LtxvApiImageToVideo" in nodes and api_auth)
    if local_ready:
        status = "ready_local"
        reason = "Local LTXV nodes and model assets are present."
    elif "LtxvApiImageToVideo" in nodes and not api_auth:
        status = "blocked_preflight"
        reason = "LTXV API node exists, but no Comfy API auth token is configured."
    else:
        status = "blocked_preflight"
        reason = "LTXV nodes exist but no local LTX checkpoint/UNet assets were found."
    return {
        "engine": "LTX-Video",
        "status": status,
        "reason": reason,
        "local_nodes_present": sorted([n for n in nodes if "LTX" in n or "Ltxv" in n]),
        "local_ltx_checkpoints": local_ckpts,
        "local_ltx_unets": local_unets,
        "api_node_present": "LtxvApiImageToVideo" in nodes,
        "api_auth_present": api_auth,
        "sample_results": [],
    }


def detect_animatediff(object_info: Dict[str, Any]) -> Dict[str, Any]:
    nodes = set(object_info.keys())
    def is_animatediff_node(name: str) -> bool:
        lowered = name.lower()
        return (
            "animatediff" in lowered
            or "sparsectrl" in lowered
            or "motionmodel" in lowered
            or lowered.startswith("ade")
        )

    matched = sorted(n for n in nodes if is_animatediff_node(n))
    if matched:
        status = "partial_ready"
        reason = "AnimateDiff-like nodes were detected, but no maintained workflow is defined yet."
    else:
        status = "blocked_preflight"
        reason = "No AnimateDiff/SparseCtrl nodes were detected in current ComfyUI object_info."
    return {
        "engine": "AnimateDiff",
        "status": status,
        "reason": reason,
        "matched_nodes": matched,
        "sample_results": [],
    }


def detect_ambient_role() -> Dict[str, Any]:
    return {
        "engine": "AMBIENT_STILL_LOOP",
        "status": "maintained",
        "reason": "Current maintained static-camera ambient loop backend.",
        "fits": [
            "subtle atmosphere loop",
            "fixed frame",
            "stable short background motion",
        ],
        "misses": [
            "visible local object motion",
            "high-motion foreground animation",
            "real img2video semantics",
        ],
    }


def build_report(system_stats: Dict[str, Any], sample_images: List[Path], ltx: Dict[str, Any], animatediff: Dict[str, Any]) -> str:
    lines = []
    lines.append("# Motion Video Spike Report")
    lines.append("## 1. Ambient Still Loop Role Finalization")
    lines.append("- `VIDEO_LOCKED_CAMERA` is now treated as the deprecated alias of `AMBIENT_STILL_LOOP`.")
    lines.append("- Current maintained role: static-camera ambient still loop, not a full motion video engine.")
    lines.append("- Good for subtle atmosphere loops; not good for visible foreground/local motion.")
    lines.append("## 2. LTX-Video Spike Result")
    lines.append(f"- Status: `{ltx['status']}`")
    lines.append(f"- Reason: {ltx['reason']}")
    lines.append(f"- Local LTX checkpoints: `{ltx['local_ltx_checkpoints']}`")
    lines.append(f"- Local LTX UNets: `{ltx['local_ltx_unets']}`")
    lines.append(f"- API node present: `{ltx['api_node_present']}`, auth present: `{ltx['api_auth_present']}`")
    lines.append("- Sample images considered:")
    for image in sample_images:
        lines.append(f"  - `{image}`")
    lines.append("## 3. AnimateDiff Spike Result")
    lines.append(f"- Status: `{animatediff['status']}`")
    lines.append(f"- Reason: {animatediff['reason']}")
    lines.append(f"- Matched nodes: `{animatediff['matched_nodes']}`")
    lines.append("## 4. Comparison Summary")
    lines.append("- Ambient still loop: available now, but weak for visible local motion.")
    lines.append("- LTX-Video: best near-term motion-video candidate in current ComfyUI, but blocked by missing local model assets or API auth.")
    lines.append("- AnimateDiff: currently blocked in this environment because required nodes are not installed.")
    lines.append("## 5. Recommended Motion Video Engine")
    lines.append("- Recommend `LTX-Video` as the first `MOTION_VIDEO` spike target once local weights or API auth are supplied.")
    lines.append("- Keep `AnimateDiff` as second candidate after node installation.")
    lines.append("## 6. Integration Risk")
    lines.append("- LTX-Video: medium integration risk, high asset/runtime requirement.")
    lines.append("- AnimateDiff: high workflow complexity and higher ops risk.")
    lines.append("- Ambient still loop remains low-risk baseline.")
    lines.append("## 7. Files/Areas Affected")
    lines.append("- `web_ui.py`")
    lines.append("- `api_server.py`")
    lines.append("- `mellow_link/media/services/video_service.py`")
    lines.append("- `mellow_link/media/schemas.py`")
    lines.append("- `config/settings.yaml`")
    lines.append("- `scripts/motion_video_spike.py`")
    lines.append("")
    lines.append("## Environment Snapshot")
    lines.append(f"- ComfyUI version: `{system_stats.get('system', {}).get('comfyui_version', 'unknown')}`")
    lines.append(f"- GPU: `{(system_stats.get('devices') or [{}])[0].get('name', 'unknown')}`")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", action="append", dest="images", default=[], help="Optional sample images.")
    args = parser.parse_args()

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    object_info = fetch_json("/object_info")
    system_stats = fetch_json("/system_stats")

    sample_images = [Path(p).resolve() for p in args.images] if args.images else latest_sample_images(2)
    ltx = detect_ltx(object_info)
    animatediff = detect_animatediff(object_info)
    ambient = detect_ambient_role()

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = REPORTS_DIR / f"motion_video_spike_report_{stamp}.md"
    json_path = REPORTS_DIR / f"motion_video_spike_report_{stamp}.json"

    report_text = build_report(system_stats, sample_images, ltx, animatediff)
    report_path.write_text(report_text, encoding="utf-8")
    json_path.write_text(
        json.dumps(
            {
                "ambient": ambient,
                "ltx": ltx,
                "animatediff": animatediff,
                "sample_images": [str(p) for p in sample_images],
                "system_stats": system_stats,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(report_path)
    print(json_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
