from __future__ import annotations

import argparse
import copy
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode
from urllib.request import Request, urlopen


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
REPORTS_DIR = REPO_ROOT / "outputs" / "reports"
DEFAULT_NEGATIVE = "camera drift, zoom, pan, dolly, warped motion, unstable frame, flicker, jitter, distortion, low quality"


def read_json_url(url: str, *, data: Optional[dict] = None) -> Dict[str, Any]:
    payload = None
    headers = {}
    if data is not None:
        payload = json.dumps(data).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = Request(url, data=payload, headers=headers, method="POST" if payload is not None else "GET")
    with urlopen(req, timeout=20) as res:
        return json.loads(res.read().decode("utf-8"))


def load_settings() -> Dict[str, Any]:
    import yaml

    return yaml.safe_load((REPO_ROOT / "config" / "settings.yaml").read_text(encoding="utf-8")) or {}


def spike_settings() -> Dict[str, Any]:
    from mellow_link.services.runtime_config import get_motion_video_spike_settings

    return get_motion_video_spike_settings(load_settings())


def latest_image() -> Path:
    files = sorted((REPO_ROOT / "outputs" / "images").glob("*.png"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        raise FileNotFoundError("No sample image found under outputs/images")
    return files[0].resolve()


def object_info() -> Dict[str, Any]:
    return read_json_url("http://127.0.0.1:8188/object_info")


def system_stats() -> Dict[str, Any]:
    return read_json_url("http://127.0.0.1:8188/system_stats")


def comfy_output_dir() -> Path:
    comfy_root = Path(spike_settings()["comfy_root"]).resolve()
    return comfy_root / "output"


def resolve_model_path(cfg: Dict[str, Any]) -> Optional[Path]:
    comfy_root = Path(cfg["comfy_root"]).resolve()
    model_file = cfg["model_file"]
    for rel in cfg["model_search_dirs"]:
        candidate = comfy_root / rel / model_file
        if candidate.exists():
            return candidate.resolve()
    return None


def supporting_assets(cfg: Dict[str, Any]) -> Dict[str, Path]:
    comfy_root = Path(cfg["comfy_root"]).resolve()
    return {
        "clip": (comfy_root / "models" / "clip" / cfg["clip_file"]).resolve(),
        "vae": (comfy_root / "models" / "vae" / cfg["vae_file"]).resolve(),
    }


def vram_snapshot(stats: Dict[str, Any]) -> Dict[str, Any]:
    device = (stats.get("devices") or [{}])[0]
    return {
        "gpu_name": device.get("name"),
        "vram_total_gb": round(float(device.get("vram_total", 0)) / (1024 ** 3), 2),
        "vram_free_gb": round(float(device.get("vram_free", 0)) / (1024 ** 3), 2),
    }


def feasibility(stats: Dict[str, Any]) -> Dict[str, str]:
    snap = vram_snapshot(stats)
    total = snap["vram_total_gb"]
    if total >= 28:
        return {"classification": "operational", "reason": "VRAM is above typical comfortable local LTX thresholds."}
    if total >= 15:
        return {
            "classification": "experimental_only",
            "reason": "16GB-class VRAM can support only low-resolution, short-clip, low-memory spikes.",
        }
    return {"classification": "blocked", "reason": "Available VRAM is below the practical floor for local LTX spikes."}


def build_workflow(cfg: Dict[str, Any], *, image_name: str, prompt: str, negative_prompt: str, seed: int) -> Dict[str, Any]:
    template = json.loads((REPO_ROOT / "mellow_link" / "data" / "workflows" / cfg["workflow"]).read_text(encoding="utf-8"))
    workflow = copy.deepcopy(template)
    workflow["1"]["inputs"]["image"] = image_name
    workflow["2"]["inputs"]["unet_name"] = cfg["model_file"]
    workflow["2"]["inputs"]["weight_dtype"] = cfg["unet_weight_dtype"]
    workflow["4"]["inputs"]["clip_name"] = cfg["clip_file"]
    workflow["4"]["inputs"]["device"] = cfg["clip_device"]
    workflow["5"]["inputs"]["text"] = prompt
    workflow["6"]["inputs"]["text"] = negative_prompt
    workflow["7"]["inputs"]["vae_name"] = cfg["vae_file"]
    workflow["8"]["inputs"]["frame_rate"] = float(cfg["fps"])
    workflow["9"]["inputs"]["width"] = int(cfg["width"])
    workflow["9"]["inputs"]["height"] = int(cfg["height"])
    workflow["9"]["inputs"]["length"] = int(cfg["length"])
    workflow["9"]["inputs"]["strength"] = float(cfg["strength"])
    workflow["10"]["inputs"]["seed"] = int(seed)
    workflow["10"]["inputs"]["steps"] = int(cfg["steps"])
    workflow["10"]["inputs"]["cfg"] = float(cfg["cfg"])
    workflow["10"]["inputs"]["sampler_name"] = cfg["sampler"]
    workflow["10"]["inputs"]["scheduler"] = cfg["scheduler"]
    workflow["12"]["inputs"]["fps"] = float(cfg["fps"])
    workflow["13"]["inputs"]["filename_prefix"] = f"spike/LTX_2B_0_9_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    return workflow


def queue_prompt(workflow: Dict[str, Any]) -> str:
    payload = {"prompt": workflow, "client_id": f"ltx-local-{int(time.time())}"}
    data = read_json_url("http://127.0.0.1:8188/prompt", data=payload)
    prompt_id = str(data.get("prompt_id") or "")
    if not prompt_id:
        raise RuntimeError(f"ComfyUI did not return prompt_id: {data}")
    return prompt_id


def fetch_history(prompt_id: str) -> Dict[str, Any]:
    return read_json_url(f"http://127.0.0.1:8188/history/{prompt_id}")


def collect_video_outputs(history: Dict[str, Any], prompt_id: str) -> List[Dict[str, Any]]:
    prompt_history = history.get(prompt_id, {})
    outputs = prompt_history.get("outputs", {})
    found: List[Dict[str, Any]] = []
    for node_id, node_output in outputs.items():
        if not isinstance(node_output, dict):
            continue
        for key in ("videos", "gifs", "animations"):
            items = node_output.get(key)
            if isinstance(items, list):
                for item in items:
                    if isinstance(item, dict):
                        item = dict(item)
                        item["node_id"] = node_id
                        item["key"] = key
                        found.append(item)
    return found


def wait_for_outputs(prompt_id: str, timeout_s: int = 900) -> Dict[str, Any]:
    start = time.time()
    while time.time() - start < timeout_s:
        hist = fetch_history(prompt_id)
        outputs = collect_video_outputs(hist, prompt_id)
        if outputs:
            return {"history": hist, "outputs": outputs, "elapsed_s": round(time.time() - start, 2)}
        time.sleep(5)
    raise TimeoutError(f"No video outputs found for prompt_id={prompt_id} within {timeout_s}s")


def upload_image(image_path: Path) -> str:
    boundary = "----mellowltxboundary"
    payload = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="image"; filename="{image_path.name}"\r\n'
        "Content-Type: application/octet-stream\r\n\r\n"
    ).encode("utf-8") + image_path.read_bytes() + f"\r\n--{boundary}--\r\n".encode("utf-8")
    req = Request(
        "http://127.0.0.1:8188/upload/image",
        data=payload,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    with urlopen(req, timeout=60) as res:
        data = json.loads(res.read().decode("utf-8"))
    return str(data.get("name") or image_path.name)


def preflight(cfg: Dict[str, Any]) -> Dict[str, Any]:
    stats = system_stats()
    objs = object_info()
    required_nodes = [
        "UNETLoader",
        "CLIPLoader",
        "CLIPTextEncode",
        "VAELoader",
        "LTXVConditioning",
        "LTXVImgToVideo",
        "ModelSamplingLTXV",
        "KSampler",
        "VAEDecode",
        "CreateVideo",
        "SaveVideo",
    ]
    missing_nodes = [n for n in required_nodes if n not in objs]
    model_path = resolve_model_path(cfg)
    assets = supporting_assets(cfg)
    return {
        "system_stats": stats,
        "feasibility": feasibility(stats),
        "missing_nodes": missing_nodes,
        "model_path": str(model_path) if model_path else None,
        "supporting_assets": {k: {"path": str(v), "exists": v.exists()} for k, v in assets.items()},
        "sample_image": str(latest_image()),
        "safe_defaults": {
            "resolution": f"{cfg['width']}x{cfg['height']}",
            "length_frames": cfg["length"],
            "fps": cfg["fps"],
            "duration_seconds": cfg["duration_seconds"],
            "steps": cfg["steps"],
            "cfg": cfg["cfg"],
            "weight_dtype": cfg["unet_weight_dtype"],
            "clip_device": cfg["clip_device"],
            "reserve_vram_gb": cfg["reserve_vram_gb"],
        },
    }


def run_generation(cfg: Dict[str, Any], *, prompt: str, seed: int) -> Dict[str, Any]:
    image_path = latest_image()
    comfy_image_name = upload_image(image_path)
    workflow = build_workflow(cfg, image_name=comfy_image_name, prompt=prompt, negative_prompt=DEFAULT_NEGATIVE, seed=seed)
    prompt_id = queue_prompt(workflow)
    result = wait_for_outputs(prompt_id, timeout_s=1800)
    return {
        "prompt_id": prompt_id,
        "elapsed_s": result["elapsed_s"],
        "outputs": result["outputs"],
    }


def write_report(report: Dict[str, Any]) -> Path:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = REPORTS_DIR / f"ltx_local_enablement_{stamp}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", action="store_true", help="Attempt actual local LTX generation after preflight.")
    parser.add_argument("--prompt", default="wind brushes the foreground grass while light flickers near the window, fixed camera, visible local motion")
    parser.add_argument("--seed", type=int, default=1)
    args = parser.parse_args()

    cfg = spike_settings()
    report: Dict[str, Any] = {"config": cfg, "preflight": preflight(cfg)}

    can_run = (
        report["preflight"]["feasibility"]["classification"] != "blocked"
        and not report["preflight"]["missing_nodes"]
        and report["preflight"]["model_path"]
        and all(x["exists"] for x in report["preflight"]["supporting_assets"].values())
    )

    if args.run:
        if can_run:
            started = time.time()
            try:
                report["sample_generation"] = run_generation(cfg, prompt=args.prompt, seed=args.seed)
                report["sample_generation"]["wall_clock_s"] = round(time.time() - started, 2)
                report["sample_generation"]["status"] = "success"
            except Exception as e:
                report["sample_generation"] = {
                    "status": "failed",
                    "error": str(e),
                    "wall_clock_s": round(time.time() - started, 2),
                }
        else:
            report["sample_generation"] = {
                "status": "blocked_preflight",
                "reason": "Required local model or supporting assets are missing, or required nodes are unavailable.",
            }

    path = write_report(report)
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
