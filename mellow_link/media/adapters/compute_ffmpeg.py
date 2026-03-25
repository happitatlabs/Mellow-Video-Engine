"""
로컬 FFmpeg 기반 미디어 연산 어댑터.

allow_media_compute() & allow_ffmpeg() 일 때만 실제 호출.
ENABLE_FFMPEG=0이면 명확한 에러로 차단.
모든 subprocess/ffmpeg 호출은 이 어댑터 내부에만 존재.
"""
import logging
import math
import os
import re
import shutil
import subprocess
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from mellow_link.media.adapters.base import MediaComputeAdapter
from mellow_link.services.runtime_config import load_settings

logger = logging.getLogger(__name__)

_FFMPEG_BLOCK_MSG = "ENABLE_FFMPEG=0. FFmpeg 호출이 비활성화되어 있습니다. 미디어 연산을 사용하려면 ENABLE_FFMPEG=1로 설정하세요."
_COMPUTE_BLOCK_MSG = "ENABLE_MEDIA_COMPUTE=0. 미디어 로컬 연산이 비활성화되어 있습니다."
_DEFAULT_CRF = 25
_DEFAULT_PRESET = "faster"


def _check_ffmpeg_allowed() -> None:
    try:
        from mellow_link.config.settings import get_settings
        s = get_settings()
        if not s.allow_media_compute():
            raise RuntimeError(_COMPUTE_BLOCK_MSG)
        if not s.allow_ffmpeg():
            raise RuntimeError(_FFMPEG_BLOCK_MSG)
    except RuntimeError:
        raise
    except Exception as e:
        logger.warning("[LocalFFmpegComputeAdapter] settings check failed: %s", e)
        raise RuntimeError(_FFMPEG_BLOCK_MSG)


def _resolve_tool(name: str) -> str:
    """ffmpeg/ffprobe 실행 경로. (video_processor와 동일 로직, 어댑터 자체 구현)"""
    tool = (name or "").strip().lower()
    if tool not in {"ffmpeg", "ffprobe"}:
        return name
    try:
        settings = load_settings()
        ffmpeg_cfg = (settings or {}).get("ffmpeg", {}) if isinstance(settings, dict) else {}
        configured_path = str(ffmpeg_cfg.get("path") or "").strip()
        if configured_path:
            configured = Path(configured_path).expanduser()
            if configured.exists():
                if tool == "ffmpeg":
                    return str(configured)
                sibling = configured.with_name("ffprobe" + configured.suffix)
                if sibling.exists():
                    return str(sibling)
    except Exception:
        pass
    env_full = os.getenv("MELLOW_FFMPEG_PATH" if tool == "ffmpeg" else "MELLOW_FFPROBE_PATH")
    if isinstance(env_full, str) and env_full.strip():
        p = Path(env_full.strip()).expanduser()
        if p.exists():
            return str(p)
    env_dir = os.getenv("MELLOW_FFMPEG_BIN_DIR")
    if isinstance(env_dir, str) and env_dir.strip():
        d = Path(env_dir.strip()).expanduser()
        exe = d / (tool + (".exe" if os.name == "nt" else ""))
        if exe.exists():
            return str(exe)
    comfy_out = os.getenv("MELLOW_COMFY_OUTPUT_DIR")
    if isinstance(comfy_out, str) and comfy_out.strip():
        out_dir = Path(comfy_out.strip()).expanduser()
        try:
            out_dir = out_dir.resolve()
        except Exception:
            pass
        for idx in (2, 1, 3):
            try:
                root = out_dir.parents[idx]
            except Exception:
                continue
            for d in [root / "ffmpeg" / "bin", root / "ffmpeg", root / "tools" / "ffmpeg" / "bin",
                     root / "ComfyUI" / "ffmpeg" / "bin", root / "ComfyUI" / "ffmpeg", root / "bin"]:
                exe = d / (tool + (".exe" if os.name == "nt" else ""))
                if exe.exists():
                    return str(exe)
    found = shutil.which(tool)
    if found:
        return found
    return name


def _resolve_ffmpeg() -> str:
    return _resolve_tool("ffmpeg")


def _resolve_ffprobe() -> str:
    return _resolve_tool("ffprobe")


def _safe_suffix(target_duration: float) -> str:
    sec = int(round(target_duration))
    return f"_looped_{sec}s"


def _get_encoding_params() -> tuple:
    crf, preset = _DEFAULT_CRF, _DEFAULT_PRESET
    raw = os.getenv("MELLOW_VIDEO_CRF")
    if isinstance(raw, str) and raw.strip():
        try:
            crf = max(23, min(28, int(raw.strip())))
        except Exception:
            pass
    raw = os.getenv("MELLOW_VIDEO_PRESET")
    if isinstance(raw, str) and raw.strip() and raw.strip().lower() in ("faster", "medium"):
        preset = raw.strip().lower()
    return crf, preset


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(float(value), maximum))


def _normalized_box_to_pixels(box: Tuple[float, float, float, float], width: int, height: int) -> Dict[str, int]:
    x, y, w, h = box
    px = int(round(width * x))
    py = int(round(height * y))
    pw = max(24, int(round(width * w)))
    ph = max(24, int(round(height * h)))
    px = max(0, min(px, max(0, width - pw)))
    py = max(0, min(py, max(0, height - ph)))
    return {"x": px, "y": py, "w": pw, "h": ph}


def _image_region_score(image: Any, edge_image: Any, box: Tuple[int, int, int, int], *, score_mode: str) -> float:
    try:
        from PIL import ImageStat  # type: ignore
    except Exception:
        return 0.0

    x0, y0, x1, y1 = box
    crop = image.crop((x0, y0, x1, y1))
    edge_crop = edge_image.crop((x0, y0, x1, y1))
    lum = ImageStat.Stat(crop.convert("L")).mean[0]
    edge = ImageStat.Stat(edge_crop.convert("L")).mean[0]
    if score_mode == "light":
        return (lum * 1.7) + (edge * 0.3)
    if score_mode == "reflection":
        return (lum * 1.1) + (edge * 0.7)
    if score_mode == "fabric":
        return (edge * 1.4) + (lum * 0.3)
    return (edge * 1.6) + (lum * 0.15)


def _choose_region_box(
    image_path: Path,
    *,
    width: int,
    height: int,
    score_mode: str,
    default_box: Tuple[float, float, float, float],
    x_range: Tuple[float, float],
    y_range: Tuple[float, float],
    box_size: Tuple[float, float],
    step_ratio: float = 0.08,
) -> Tuple[float, float, float, float]:
    try:
        from PIL import Image, ImageFilter  # type: ignore

        with Image.open(image_path) as img:
            rgb = img.convert("RGB").resize((width, height))
            edge_img = rgb.convert("L").filter(ImageFilter.FIND_EDGES)
            box_w = max(32, int(width * box_size[0]))
            box_h = max(32, int(height * box_size[1]))
            x0 = max(0, min(int(width * x_range[0]), width - box_w))
            x1 = max(x0, min(int(width * x_range[1]), width - box_w))
            y0 = max(0, min(int(height * y_range[0]), height - box_h))
            y1 = max(y0, min(int(height * y_range[1]), height - box_h))
            step_x = max(12, int(width * step_ratio))
            step_y = max(12, int(height * step_ratio))
            best_score = -1.0
            best_box = default_box
            for px in range(x0, x1 + 1, step_x):
                for py in range(y0, y1 + 1, step_y):
                    candidate = (px, py, min(px + box_w, width), min(py + box_h, height))
                    score = _image_region_score(rgb, edge_img, candidate, score_mode=score_mode)
                    if score > best_score:
                        best_score = score
                        best_box = (
                            candidate[0] / float(width),
                            candidate[1] / float(height),
                            (candidate[2] - candidate[0]) / float(width),
                            (candidate[3] - candidate[1]) / float(height),
                        )
            return best_box
    except Exception:
        return default_box


def _choose_patch_regions(
    image_path: Path,
    *,
    region_style: str,
    width: int,
    height: int,
) -> Dict[str, Tuple[float, float, float, float]]:
    defaults = {
        "window_light": {
            "light": (0.54, 0.12, 0.26, 0.50),
            "foliage": (0.14, 0.60, 0.40, 0.24),
            "fabric": (0.70, 0.10, 0.16, 0.60),
        },
        "foliage": {
            "light": (0.48, 0.10, 0.24, 0.40),
            "foliage": (0.10, 0.56, 0.60, 0.28),
            "fabric": (0.74, 0.10, 0.14, 0.54),
        },
        "fabric": {
            "light": (0.50, 0.12, 0.22, 0.40),
            "foliage": (0.16, 0.62, 0.34, 0.20),
            "fabric": (0.64, 0.06, 0.20, 0.68),
        },
        "reflection": {
            "light": (0.46, 0.18, 0.30, 0.34),
            "foliage": (0.24, 0.66, 0.52, 0.18),
            "fabric": (0.72, 0.10, 0.12, 0.52),
        },
    }
    region_defaults = defaults.get(region_style, defaults["window_light"])
    return {
        "light": _choose_region_box(
            image_path,
            width=width,
            height=height,
            score_mode="light",
            default_box=region_defaults["light"],
            x_range=(0.10, 0.70),
            y_range=(0.05, 0.44),
            box_size=(region_defaults["light"][2], region_defaults["light"][3]),
        ),
        "foliage": _choose_region_box(
            image_path,
            width=width,
            height=height,
            score_mode="foliage",
            default_box=region_defaults["foliage"],
            x_range=(0.05, 0.58),
            y_range=(0.48, 0.76),
            box_size=(region_defaults["foliage"][2], region_defaults["foliage"][3]),
        ),
        "fabric": _choose_region_box(
            image_path,
            width=width,
            height=height,
            score_mode="fabric",
            default_box=region_defaults["fabric"],
            x_range=(0.58, 0.82),
            y_range=(0.04, 0.24),
            box_size=(region_defaults["fabric"][2], region_defaults["fabric"][3]),
        ),
    }


def _write_debug_visualization(
    image_path: Path,
    output_path: Path,
    *,
    placements: Dict[str, Dict[str, Any]],
    width: int,
    height: int,
) -> Optional[Path]:
    try:
        from PIL import Image, ImageDraw  # type: ignore

        debug_path = output_path.parent / f"{output_path.stem}_debug_regions.png"
        with Image.open(image_path) as img:
            canvas = img.convert("RGB").resize((width, height))
            draw = ImageDraw.Draw(canvas)
            colors = {
                "light": "#ffd966",
                "haze": "#8ecae6",
                "foliage": "#6aa84f",
                "fabric": "#c27ba0",
            }
            for patch_type, placement in placements.items():
                box = placement["pixels"]
                x0 = int(box["x"])
                y0 = int(box["y"])
                x1 = x0 + int(box["w"])
                y1 = y0 + int(box["h"])
                color = colors.get(patch_type, "#ffffff")
                draw.rectangle((x0, y0, x1, y1), outline=color, width=4)
                label = f"{patch_type} a={placement.get('alpha', 0):.2f} dx={placement.get('shift_x_px', 0):.1f} dy={placement.get('shift_y_px', 0):.1f}"
                draw.rectangle((x0, max(0, y0 - 24), min(width, x0 + 360), y0), fill="#000000")
                draw.text((x0 + 4, max(0, y0 - 20)), label, fill=color)
            canvas.save(debug_path)
        return debug_path
    except Exception as e:
        logger.warning("[LocalFFmpegComputeAdapter] Failed to write ambient debug visualization: %s", e)
        return None


def _run_cmd(cmd: List[str], timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
        shell=False,
    )


class LocalFFmpegComputeAdapter(MediaComputeAdapter):
    """FFmpeg 기반 로컬 전용 연산. ENABLE_FFMPEG=0이면 모든 호출에서 차단."""

    def transcode_video(
        self,
        input_path: str | Path,
        output_path: str | Path,
        **kwargs: Any,
    ) -> Path:
        _check_ffmpeg_allowed()
        inp, out = Path(input_path).resolve(), Path(output_path).resolve()
        out.parent.mkdir(parents=True, exist_ok=True)
        ffmpeg = _resolve_ffmpeg()
        cmd = [
            ffmpeg, "-y", "-i", str(inp),
            "-c:v", kwargs.get("video_codec", "libx264"),
            "-c:a", kwargs.get("audio_codec", "aac"),
            str(out),
        ]
        res = _run_cmd(cmd, timeout=kwargs.get("timeout", 300))
        if res.returncode != 0:
            raise RuntimeError((res.stderr or res.stdout or "ffmpeg failed")[-800:])
        return out

    def generate_thumbnail(
        self,
        input_path: str | Path,
        output_path: str | Path,
        **kwargs: Any,
    ) -> Path:
        _check_ffmpeg_allowed()
        inp, out = Path(input_path).resolve(), Path(output_path).resolve()
        out.parent.mkdir(parents=True, exist_ok=True)
        ffmpeg = _resolve_ffmpeg()
        t = kwargs.get("time_offset", "0")
        cmd = [ffmpeg, "-y", "-i", str(inp), "-vframes", "1", "-ss", str(t), str(out)]
        res = _run_cmd(cmd, timeout=60)
        if res.returncode != 0:
            raise RuntimeError((res.stderr or res.stdout or "ffmpeg thumbnail failed")[-800:])
        return out

    def merge_audio(
        self,
        video_path: str | Path,
        audio_path: str | Path,
        output_path: str | Path,
        **kwargs: Any,
    ) -> Path:
        _check_ffmpeg_allowed()
        v, a, out = Path(video_path).resolve(), Path(audio_path).resolve(), Path(output_path).resolve()
        out.parent.mkdir(parents=True, exist_ok=True)
        ffmpeg = _resolve_ffmpeg()
        cmd = [
            ffmpeg, "-y", "-i", str(v), "-i", str(a),
            "-c:v", "copy", "-c:a", "aac", "-shortest",
            str(out),
        ]
        res = _run_cmd(cmd, timeout=kwargs.get("timeout", 300))
        if res.returncode != 0:
            raise RuntimeError((res.stderr or res.stdout or "ffmpeg merge failed")[-800:])
        return out

    def extract_frames(
        self,
        input_path: str | Path,
        output_dir: str | Path,
        **kwargs: Any,
    ) -> List[Path]:
        _check_ffmpeg_allowed()
        inp = Path(input_path).resolve()
        odir = Path(output_dir).resolve()
        odir.mkdir(parents=True, exist_ok=True)
        ffmpeg = _resolve_ffmpeg()
        fps = kwargs.get("fps", "1")
        pattern = str(odir / "frame_%04d.png")
        cmd = [ffmpeg, "-y", "-i", str(inp), "-vf", f"fps={fps}", pattern]
        res = _run_cmd(cmd, timeout=kwargs.get("timeout", 300))
        if res.returncode != 0:
            raise RuntimeError((res.stderr or res.stdout or "ffmpeg extract_frames failed")[-800:])
        return sorted(odir.glob("frame_*.png"))

    def probe_duration_seconds(self, video_path: str | Path) -> Optional[float]:
        _check_ffmpeg_allowed()
        try:
            p = Path(video_path).resolve()
            if not p.exists():
                return None
            try:
                cmd = [
                    _resolve_ffprobe(), "-v", "error",
                    "-show_entries", "format=duration",
                    "-of", "default=noprint_wrappers=1:nokey=1", str(p),
                ]
                res = _run_cmd(cmd, timeout=30)
                if res.returncode == 0 and (res.stdout or "").strip():
                    return float((res.stdout or "").strip())
            except FileNotFoundError:
                pass
            cmd = [_resolve_ffmpeg(), "-hide_banner", "-i", str(p)]
            res = _run_cmd(cmd, timeout=30)
            text = (res.stderr or "") + "\n" + (res.stdout or "")
            m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", text)
            if not m:
                return None
            hh, mm, ss = m.group(1), m.group(2), m.group(3)
            return int(hh) * 3600.0 + int(mm) * 60.0 + float(ss)
        except Exception:
            return None

    def extend_video_if_needed(
        self,
        input_path: str | Path,
        *,
        target_duration: float = 12.0,
        fps: int = 8,
        mode: str = "boomerang",
        overlap_seconds: float = 0.35,
    ) -> Path:
        _check_ffmpeg_allowed()
        inp = Path(input_path).resolve()
        if not inp.exists():
            return inp
        dur = self.probe_duration_seconds(inp)
        if dur is not None and dur >= target_duration:
            return inp
        crf, preset = _get_encoding_params()
        out_path = inp.parent / f"{inp.stem}{_safe_suffix(target_duration)}.mp4"
        out_path = out_path.resolve()
        if mode.lower() in ("boomerang", "pingpong", "ping-pong"):
            self._extend_boomerang(inp, out_path, target_duration=target_duration, fps=fps, crf=crf, preset=preset)
        elif mode.lower() in ("crossfade", "dissolve", "xfade"):
            self._extend_crossfade(inp, out_path, target_duration=target_duration, fps=fps, overlap=overlap_seconds, crf=crf, preset=preset)
        else:
            return inp
        return out_path

    def stabilize_video_drift(
        self,
        input_path: str | Path,
        *,
        strength: float = 0.18,
    ) -> Path:
        _check_ffmpeg_allowed()
        inp = Path(input_path).resolve()
        if not inp.exists():
            return inp
        ff = _resolve_ffmpeg()
        out_path = inp.parent / f"{inp.stem}_stabilized.mp4"
        out_path = out_path.resolve()
        clamped = max(0.0, min(float(strength), 1.0))
        crop_ratio = max(0.94, 1.0 - (clamped * 0.03))
        crop_expr = (
            f"crop=iw*{crop_ratio:.4f}:ih*{crop_ratio:.4f}:(iw-ow)/2:(ih-oh)/2,"
            f"scale=1216:704:flags=lanczos"
        )
        filter_chain = (
            f"deshake=x=32:y=32:rx=16:ry=16:edge=mirror,"
            f"{crop_expr},fps=8,format=yuv420p"
        )
        cmd = [
            ff,
            "-y",
            "-i",
            str(inp),
            "-an",
            "-vf",
            filter_chain,
            "-vcodec",
            "libx264",
            "-crf",
            str(_get_encoding_params()[0]),
            "-preset",
            _get_encoding_params()[1],
            "-pix_fmt",
            "yuv420p",
            str(out_path),
        ]
        res = _run_cmd(cmd, timeout=300)
        if res.returncode != 0:
            raise RuntimeError((res.stderr or res.stdout or "ffmpeg stabilize failed")[-800:])
        return out_path

    def create_ambient_loop_from_image(
        self,
        image_path: str | Path,
        output_path: str | Path,
        *,
        target_duration: float = 12.0,
        fps: int = 8,
        strength: float = 0.18,
        motion_profile: Optional[dict] = None,
    ) -> Path:
        _check_ffmpeg_allowed()
        inp = Path(image_path).resolve()
        out = Path(output_path).resolve()
        if not inp.exists():
            raise FileNotFoundError(f"image_path not found: {inp}")
        out.parent.mkdir(parents=True, exist_ok=True)

        ff = _resolve_ffmpeg()
        profile = dict(motion_profile or {})
        overall = max(0.0, min(float(profile.get("overall_strength", strength) or strength), 1.0))
        light_pulse = max(0.0, min(float(profile.get("light_pulse", 0.45) or 0.45), 1.0))
        haze_drift = max(0.0, min(float(profile.get("haze_drift", 0.3) or 0.3), 1.0))
        foliage_shimmer = max(0.0, min(float(profile.get("foliage_shimmer", 0.18) or 0.18), 1.0))
        fabric_shimmer = max(0.0, min(float(profile.get("fabric_shimmer", 0.18) or 0.18), 1.0))
        local_emphasis = max(0.0, min(float(profile.get("local_motion_emphasis", 0.72) or 0.72), 1.0))
        global_balance = max(0.0, min(float(profile.get("global_motion_balance", 0.2) or 0.2), 1.0))
        region_style = str(profile.get("region_style", "window_light") or "window_light")
        visibility_mode = str(profile.get("visibility_mode", "visibility_first") or "visibility_first").strip().lower()
        min_patch_alpha = _clamp(float(profile.get("min_patch_alpha", 0.22) or 0.22), 0.02, 0.60)
        min_patch_shift_px = _clamp(float(profile.get("min_patch_shift_px", 14.0) or 14.0), 1.0, 48.0)
        min_light_pulse = _clamp(float(profile.get("min_light_pulse", 0.75) or 0.75), 0.10, 1.0)
        debug_visualization = bool(profile.get("debug_visualization", False))

        if visibility_mode == "visibility_first":
            overall = _clamp(max(overall, 0.62), 0.0, 1.0)
            local_emphasis = _clamp(max(local_emphasis, 0.90), 0.0, 1.0)
            global_balance = _clamp(min(global_balance, 0.08), 0.0, 1.0)
            light_pulse = _clamp(max(light_pulse, min_light_pulse), 0.0, 1.0)
            foliage_shimmer = _clamp(max(foliage_shimmer, 0.52), 0.0, 1.0)
            fabric_shimmer = _clamp(max(fabric_shimmer, 0.46), 0.0, 1.0)
            haze_drift = _clamp(max(haze_drift, 0.36), 0.0, 1.0)

        glow_alpha = max(min_patch_alpha, (0.10 + overall * 0.20) * (0.65 + local_emphasis * 0.70) * max(light_pulse, min_light_pulse))
        haze_alpha = max(min_patch_alpha * 0.55, (0.06 + overall * 0.08) * (0.12 + global_balance * 0.40) * max(haze_drift, 0.22))
        foliage_alpha = max(min_patch_alpha, (0.08 + overall * 0.15) * max(foliage_shimmer, 0.22))
        fabric_alpha = max(min_patch_alpha, (0.08 + overall * 0.14) * max(fabric_shimmer, 0.22))
        global_dx = max(0.4, 0.25 + (overall * global_balance * 2.0))
        global_dy = max(0.2, 0.18 + (overall * global_balance * 1.4))
        local_dx = max(min_patch_shift_px, 9.0 + (overall * local_emphasis * 14.0))
        local_dy = max(min_patch_shift_px * 0.70, 6.0 + (overall * local_emphasis * 9.0))
        saturation_boost = 1.02 + (overall * 0.030)

        regions = _choose_patch_regions(inp, region_style=region_style, width=1216, height=704)
        lx, ly, lw, lh = regions["light"]
        fx, fy, fw, fh = regions["foliage"]
        cx, cy, cw, ch = regions["fabric"]

        placements = {
            "haze": {
                "box": (0.0, 0.0, 1.0, 1.0),
                "pixels": {"x": 0, "y": 0, "w": 1216, "h": 704},
                "alpha": round(haze_alpha, 3),
                "shift_x_px": round(global_dx, 3),
                "shift_y_px": round(global_dy, 3),
                "pulse": round(haze_drift, 3),
            },
            "light": {
                "box": regions["light"],
                "pixels": _normalized_box_to_pixels(regions["light"], 1216, 704),
                "alpha": round(glow_alpha, 3),
                "shift_x_px": round(local_dx, 3),
                "shift_y_px": round(local_dy, 3),
                "pulse": round(light_pulse, 3),
            },
            "foliage": {
                "box": regions["foliage"],
                "pixels": _normalized_box_to_pixels(regions["foliage"], 1216, 704),
                "alpha": round(foliage_alpha, 3),
                "shift_x_px": round(local_dx * 1.35, 3),
                "shift_y_px": round(local_dy * 0.95, 3),
                "pulse": round(foliage_shimmer, 3),
            },
            "fabric": {
                "box": regions["fabric"],
                "pixels": _normalized_box_to_pixels(regions["fabric"], 1216, 704),
                "alpha": round(fabric_alpha, 3),
                "shift_x_px": round(local_dx * 0.95, 3),
                "shift_y_px": round(local_dy * 1.10, 3),
                "pulse": round(fabric_shimmer, 3),
            },
        }
        logger.info(
            "[LocalFFmpegComputeAdapter] Ambient motion profile visibility_mode=%s region_style=%s overall=%.3f local=%.3f global=%.3f min_alpha=%.3f min_shift_px=%.2f min_light_pulse=%.2f placements=%s",
            visibility_mode,
            region_style,
            overall,
            local_emphasis,
            global_balance,
            min_patch_alpha,
            min_patch_shift_px,
            min_light_pulse,
            json.dumps(placements, ensure_ascii=False),
        )

        if debug_visualization:
            debug_path = _write_debug_visualization(inp, out, placements=placements, width=1216, height=704)
            if debug_path:
                logger.info("[LocalFFmpegComputeAdapter] Ambient debug visualization saved: %s", debug_path)
            try:
                debug_json = out.parent / f"{out.stem}_debug_regions.json"
                debug_json.write_text(
                    json.dumps(
                        {
                            "visibility_mode": visibility_mode,
                            "region_style": region_style,
                            "placements": placements,
                        },
                        ensure_ascii=False,
                        indent=2,
                    ),
                    encoding="utf-8",
                )
                logger.info("[LocalFFmpegComputeAdapter] Ambient debug region data saved: %s", debug_json)
            except Exception as e:
                logger.warning("[LocalFFmpegComputeAdapter] Failed to write ambient debug region data: %s", e)

        filter_complex = (
            "[0:v]scale=1216:704,format=rgba,split=5[base][haze_src][light_src][foliage_src][fabric_src];"
            f"[haze_src]gblur=sigma=42,eq=brightness=0.028:saturation=0.94,colorchannelmixer=aa={haze_alpha:.3f}[haze];"
            f"[light_src]crop=iw*{lw:.4f}:ih*{lh:.4f}:iw*{lx:.4f}:ih*{ly:.4f},gblur=sigma=22,"
            f"eq=brightness=0.120:saturation={1.06 + overall * 0.05:.3f}:gamma={1.04 + light_pulse * 0.06:.3f},colorchannelmixer=aa={glow_alpha:.3f}[light_patch];"
            f"[foliage_src]crop=iw*{fw:.4f}:ih*{fh:.4f}:iw*{fx:.4f}:ih*{fy:.4f},gblur=sigma=3,"
            f"eq=brightness=0.035:saturation={1.06 + foliage_shimmer * 0.12:.3f}:contrast={1.02 + foliage_shimmer * 0.05:.3f},colorchannelmixer=aa={foliage_alpha:.3f}[foliage_patch];"
            f"[fabric_src]crop=iw*{cw:.4f}:ih*{ch:.4f}:iw*{cx:.4f}:ih*{cy:.4f},gblur=sigma=2,"
            f"eq=brightness=0.026:saturation={1.05 + fabric_shimmer * 0.10:.3f}:contrast={1.02 + fabric_shimmer * 0.04:.3f},colorchannelmixer=aa={fabric_alpha:.3f}[fabric_patch];"
            f"[base][haze]overlay=x='cos(2*PI*t/{target_duration})*{global_dx:.3f}':y='sin(2*PI*t/{target_duration})*{global_dy:.3f}':format=auto[tmp1];"
            f"[tmp1][light_patch]overlay=x='W*{lx:.4f}+sin(2*PI*t/{target_duration})*{local_dx:.3f}':y='H*{ly:.4f}+cos(2*PI*t/{target_duration})*{local_dy:.3f}':format=auto[tmp2];"
            f"[tmp2][foliage_patch]overlay=x='W*{fx:.4f}+sin(4*PI*t/{target_duration})*{local_dx * 1.35:.3f}':y='H*{fy:.4f}+cos(4*PI*t/{target_duration})*{local_dy * 0.95:.3f}':format=auto[tmp3];"
            f"[tmp3][fabric_patch]overlay=x='W*{cx:.4f}+sin(3*PI*t/{target_duration})*{local_dx * 0.95:.3f}':y='H*{cy:.4f}+cos(3*PI*t/{target_duration})*{local_dy * 1.10:.3f}':format=auto[tmp4];"
            f"[tmp4]eq=contrast=1.0:saturation={saturation_boost:.3f},fps={int(fps)},format=yuv420p[v]"
        )

        crf, preset = _get_encoding_params()
        cmd = [
            ff,
            "-y",
            "-loop",
            "1",
            "-i",
            str(inp),
            "-t",
            str(float(target_duration)),
            "-filter_complex",
            filter_complex,
            "-map",
            "[v]",
            "-an",
            "-vcodec",
            "libx264",
            "-crf",
            str(crf),
            "-preset",
            preset,
            "-pix_fmt",
            "yuv420p",
            str(out),
        ]
        res = _run_cmd(cmd, timeout=300)
        if res.returncode != 0:
            raise RuntimeError((res.stderr or res.stdout or "ffmpeg ambient loop failed")[-800:])
        return out

    def _extend_boomerang(
        self,
        input_path: Path,
        output_path: Path,
        *,
        target_duration: float,
        fps: int,
        crf: int,
        preset: str,
    ) -> None:
        ff = _resolve_ffmpeg()
        tmp_dir = input_path.parent / ".tmp_video_proc"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        fwd = tmp_dir / (input_path.stem + "_fwd.mp4")
        rev = tmp_dir / (input_path.stem + "_rev.mp4")
        for src, dst, vf in (
            (input_path, fwd, f"fps={fps},format=yuv420p"),
            (input_path, rev, f"reverse,fps={fps},format=yuv420p"),
        ):
            cmd = [ff, "-y", "-i", str(src), "-an", "-vf", vf, "-vcodec", "libx264", "-crf", str(crf), "-preset", preset, "-pix_fmt", "yuv420p", str(dst)]
            res = _run_cmd(cmd, timeout=180)
            if res.returncode != 0:
                raise RuntimeError((res.stderr or res.stdout or "ffmpeg failed")[-800:])
        base_dur = self.probe_duration_seconds(fwd) or 0.0
        cycle_dur = max(0.01, base_dur * 2.0)
        cycles = max(1, int(math.ceil(target_duration / cycle_dur)))
        concat_list = tmp_dir / (input_path.stem + "_concat.txt")
        lines = []
        for _ in range(cycles):
            lines.append(f"file '{fwd.as_posix()}'")
            lines.append(f"file '{rev.as_posix()}'")
        concat_list.write_text("\n".join(lines) + "\n", encoding="utf-8")
        out_path = Path(output_path).resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        cmd = [
            ff, "-y", "-f", "concat", "-safe", "0", "-i", str(concat_list),
            "-t", str(float(target_duration)), "-an", "-vcodec", "libx264", "-crf", str(crf), "-preset", preset, "-pix_fmt", "yuv420p", "-r", str(int(fps)), str(out_path),
        ]
        res = _run_cmd(cmd, timeout=240)
        if res.returncode != 0:
            raise RuntimeError((res.stderr or res.stdout or "ffmpeg concat failed")[-800:])

    def _extend_crossfade(
        self,
        input_path: Path,
        output_path: Path,
        *,
        target_duration: float,
        fps: int,
        overlap: float,
        crf: int,
        preset: str,
    ) -> None:
        ff = _resolve_ffmpeg()
        tmp_dir = input_path.parent / ".tmp_video_proc"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        base = tmp_dir / (input_path.stem + "_base.mp4")
        cmd = [
            ff, "-y", "-i", str(input_path), "-an", "-vf", f"fps={fps},format=yuv420p",
            "-vcodec", "libx264", "-crf", str(crf), "-preset", preset, "-pix_fmt", "yuv420p", str(base),
        ]
        res = _run_cmd(cmd, timeout=180)
        if res.returncode != 0:
            raise RuntimeError((res.stderr or res.stdout or "ffmpeg normalize failed")[-800:])
        base_d = self.probe_duration_seconds(base) or 0.0
        if base_d <= 0.0:
            raise RuntimeError("base clip duration is 0")
        ov = max(0.05, float(overlap))
        ov = min(ov, max(0.05, base_d * 0.4))
        cur = base
        cur_d = base_d
        idx = 0
        while cur_d < target_duration:
            idx += 1
            next_out = tmp_dir / f"{input_path.stem}_xfade_{idx}.mp4"
            offset = max(0.0, cur_d - ov)
            filter_complex = f"[0:v][1:v]xfade=transition=fade:duration={ov}:offset={offset},fps={fps},format=yuv420p[v]"
            cmd = [ff, "-y", "-i", str(cur), "-i", str(base), "-filter_complex", filter_complex, "-map", "[v]", "-an", "-vcodec", "libx264", "-crf", str(crf), "-preset", preset, "-pix_fmt", "yuv420p", str(next_out)]
            res = _run_cmd(cmd, timeout=240)
            if res.returncode != 0:
                raise RuntimeError((res.stderr or res.stdout or "ffmpeg xfade failed")[-800:])
            cur = next_out
            cur_d = self.probe_duration_seconds(cur) or (cur_d + base_d - ov)
            if idx > 10:
                break
        out_path = Path(output_path).resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        cmd = [ff, "-y", "-i", str(cur), "-t", str(float(target_duration)), "-an", "-vcodec", "libx264", "-crf", str(crf), "-preset", preset, "-pix_fmt", "yuv420p", "-r", str(int(fps)), str(out_path)]
        res = _run_cmd(cmd, timeout=240)
        if res.returncode != 0:
            raise RuntimeError((res.stderr or res.stdout or "ffmpeg trim failed")[-800:])
