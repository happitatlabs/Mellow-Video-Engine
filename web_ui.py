from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

_REPO_ROOT = Path(__file__).absolute().parent
if str(_REPO_ROOT) in sys.path:
    sys.path.remove(str(_REPO_ROOT))
sys.path.insert(0, str(_REPO_ROOT))

for _name, _module in list(sys.modules.items()):
    if not (_name == "mellow_link" or _name.startswith("mellow_link.")):
        continue
    _module_file = getattr(_module, "__file__", None)
    if not _module_file:
        continue
    try:
        if _REPO_ROOT not in Path(_module_file).absolute().parents:
            del sys.modules[_name]
    except Exception:
        pass

import gradio as gr
import mellow_link
from mellow_link.core.schemas import ImageRequest, VideoRequest
from mellow_link.services.runtime_config import (
    get_comfyui_endpoint,
    get_motion_video_spike_settings,
    get_output_directories,
    get_video_generation_settings,
    is_media_ai_enabled,
    load_settings as load_runtime_settings,
)
from mellow_link.services.output_provenance import ensure_sidecar, write_sidecar_best_effort
from mellow_link.services.image_service import ImageService
from mellow_link.services.video_service import VideoService
from mellow_link.services.runtime_readiness import assert_media_generation_ready, get_runtime_readiness

# Logger setup
logger = logging.getLogger("MellowWeb")

_MELLOW_LINK_FILE = Path(getattr(mellow_link, "__file__", "")).absolute()
if _REPO_ROOT not in _MELLOW_LINK_FILE.parents:
    raise RuntimeError(
        f"Imported mellow_link from unexpected location: {_MELLOW_LINK_FILE}. "
        f"Expected package under {_REPO_ROOT}. Run from D:/Mellow-Video-Engine using the local .venv."
    )

# =============================================================================
# Constants
# =============================================================================
MAX_SCENES = 20  # 최대 씬 개수 (visibility로 제어)


# =============================================================================
# Logging -> UI (shared buffer)
# =============================================================================

_WEB_LOG_BUFFER: deque[str] = deque(maxlen=250)


class _WebUILogHandler(logging.Handler):
    """서비스/후처리 로그를 Gradio 상태창에 표시하기 위한 버퍼 핸들러."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            _WEB_LOG_BUFFER.append(msg)
        except Exception:
            pass


def _install_web_log_handler() -> None:
    root = logging.getLogger()
    # 중복 설치 방지
    for h in root.handlers:
        if isinstance(h, _WebUILogHandler):
            return
    h = _WebUILogHandler(level=logging.INFO)
    h.setFormatter(logging.Formatter("%(asctime)s | %(name)s | %(levelname)s | %(message)s"))
    root.addHandler(h)
    if root.level > logging.INFO:
        root.setLevel(logging.INFO)


def _log_tail(lines: int = 12) -> str:
    if not _WEB_LOG_BUFFER:
        return ""
    tail = list(_WEB_LOG_BUFFER)[-max(1, int(lines)) :]
    return "\n".join(tail)


def _compose_status(message: str, *, include_log_tail: bool = True) -> str:
    if not include_log_tail:
        return message
    tail = _log_tail()
    if not tail:
        return message
    return message + "\n\n---\n최근 로그:\n" + tail


# =============================================================================
# New "Engine" (Session/Project) - No FSM, No ComfyVideoAgent
# =============================================================================


@dataclass
class WebProject:
    project_name: str
    audio_file_path: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    # Lyrics
    lyrics_text: str = ""
    lyrics_segments: List[Dict[str, Any]] = field(default_factory=list)  # {"text","start_time","end_time"}

    # Plans & outputs (legacy helper 함수들과 호환)
    scene_plans: List[Dict[str, Any]] = field(default_factory=list)  # {"visual_prompt", ...}
    images: Dict[str, Any] = field(default_factory=dict)  # {"scene_0": {"path": "..."}}
    video_clips: Dict[str, Any] = field(default_factory=dict)  # {"scene_0": {"path": "..."}}
    generated_images: List[str] = field(default_factory=list)
    generated_clips: List[str] = field(default_factory=list)
    final_video_path: Optional[str] = None


@dataclass
class WebSession:
    project: WebProject
    image_service: ImageService
    video_service: VideoService
    connected: bool = False


def _load_settings() -> Dict[str, Any]:
    """repo의 config/settings.yaml (있으면)에서 comfyui/이미지 설정을 읽는다."""
    try:
        data = load_runtime_settings()
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {}


def _get_comfyui_host_port(settings: Dict[str, Any]) -> Tuple[str, int]:
    endpoint = get_comfyui_endpoint(settings)
    return str(endpoint["host"]), int(endpoint["port"])


def _get_image_defaults(settings: Dict[str, Any]) -> Dict[str, Any]:
    comfy = (settings or {}).get("comfyui", {}) if isinstance(settings, dict) else {}
    img = comfy.get("image_generation", {}) if isinstance(comfy, dict) else {}
    return {
        # 🎯 The Magic Number (SVD 호환): 1216x704 고정
        "width": 1216,
        "height": 704,
        "steps": int(img.get("steps", 20)),
        "cfg_scale": float(img.get("cfg_scale", 7.5)),
        "sampler_name": str(img.get("sampler", "euler")),
        "scheduler": str(img.get("scheduler", "normal")),
        "workflow": "flux_dev_api.json",
    }


def _motion_bucket_from_prompt(prompt_text: str, default_value: int) -> int:
    text = str(prompt_text or "").strip().lower()
    if not text:
        return int(default_value)
    if any(token in text for token in ("locked", "static", "fixed", "tripod", "lock-off", "locked-off", "고정", "정지")):
        return 1
    if any(token in text for token in ("zoom", "push", "dolly")):
        return 80
    if "pan" in text:
        return 110
    if any(token in text for token in ("handheld", "dynamic", "run", "shake", "격렬")):
        return 170
    return int(default_value)


def _normalize_loop_motion_prompt(prompt_text: str) -> str:
    text = str(prompt_text or "").strip()
    if not text:
        return "steam rises continuously from the cup, window light flickers softly, haze drifts in the back, curtain or foliage edges move gently, fixed camera, visible local motion"

    lowered = text.lower()
    camera_motion_tokens = ("pan", "tilt", "zoom", "push", "pull", "dolly", "truck", "orbit", "handheld", "whip", "shake", "locked-off", "static frame", "camera")
    if any(token in lowered for token in camera_motion_tokens):
        return "steam rises continuously from the cup, window light flickers softly, haze drifts in the back, curtain or foliage edges move gently, fixed camera, visible local motion"

    additions = []
    if "loop" not in lowered and "루프" not in lowered:
        additions.append("seamless loop")
    if not any(token in lowered for token in ("steam", "smoke", "vapor", "breath", "김", "연기")):
        additions.append("continuous steam or smoke motion")
    if not any(token in lowered for token in ("light", "glow", "beam", "shadow", "reflection", "window", "빛", "광선", "창빛", "반사")):
        additions.append("light pulse")
    if not any(token in lowered for token in ("haze", "fog", "mist", "smoke", "dust", "안개", "연무", "먼지")):
        additions.append("haze drift")
    if not any(token in lowered for token in ("fabric", "curtain", "cloth", "foliage", "leaf", "grass", "커튼", "천", "잎", "풀", "갈대")):
        additions.append("localized shimmer")
    if "visible local motion" not in lowered and "국소" not in lowered:
        additions.append("visible local motion")
    if "fixed camera" not in lowered and "고정 카메라" not in lowered:
        additions.append("fixed camera")
    if additions:
        text = ", ".join([text] + additions)
    return text


def _web_runtime_info() -> Dict[str, Any]:
    return {
        "planner_version": "visual_planner_runtime_v2",
        "policy_enforcement": "config_prompts_yaml + sanitizer + fail_safe",
        "entrypoint": "web_ui.py",
    }


def _project_provenance(project: WebProject, *, artifact_type: str, scene_index: Optional[int] = None) -> Dict[str, Any]:
    plan = {}
    if scene_index is not None and 0 <= int(scene_index) < len(project.scene_plans):
        plan = project.scene_plans[int(scene_index)] or {}
    policy_validation = dict(plan.get("policy_validation") or {})
    policy_flags = dict(plan.get("policy_flags") or {})
    semantic_scene = dict(plan.get("semantic_scene") or {})
    policy_inputs = dict(plan.get("policy_inputs") or {})
    policy_outputs = dict(plan.get("policy_outputs") or {})
    video_runtime = get_video_generation_settings(_load_settings())
    return {
        "source": {
            "project_id": project.project_name,
            "audio_path": project.audio_file_path,
            "scene_index": scene_index,
            "artifact_type": artifact_type,
        },
        "runtime": _web_runtime_info(),
        "request": {
            "strip_prompt_metadata": bool(_load_settings().get("outputs", {}).get("strip_prompt_metadata", False)),
            "policy_level": policy_validation.get("policy_level"),
            "policy_ok": policy_validation.get("ok"),
            "policy_issues": policy_validation.get("issues", []),
            "fail_safe_applied": bool(policy_flags.get("fail_safe_applied", False)),
            "semantic_scene": semantic_scene,
            "semantic_emotion": semantic_scene.get("emotion"),
            "semantic_action": semantic_scene.get("action"),
            "policy_before": policy_inputs,
            "policy_after": policy_outputs,
            "locked_camera_mode": bool(video_runtime.get("locked_camera_mode", False)),
            "stabilize_zoom_drift": bool(video_runtime.get("stabilize_zoom_drift", False)),
            "stabilization_strength": float(video_runtime.get("stabilization_strength", 0.18) or 0.18),
        },
    }


def _blank_scene_slot_updates() -> List[Any]:
    updates: List[Any] = []
    for _ in range(MAX_SCENES):
        updates.extend(
            [
                gr.update(visible=False),
                "",
                "",
                "",
                None,
                None,
            ]
        )
    return updates


def _build_scene_view_models(project: WebProject) -> List[Dict[str, Any]]:
    view_models: List[Dict[str, Any]] = []
    for i, scene in enumerate(get_scene_plans_data(project)[:MAX_SCENES]):
        start_time = float(scene.get("start_time", 0.0) or 0.0)
        end_time = float(scene.get("end_time", 0.0) or 0.0)
        lyric_text_line = str(scene.get("lyric_text", "") or "").strip()
        view_models.append(
            {
                "visible": True,
                "lyrics_md": f"### 씬 {i+1}\n**[{start_time:.1f}s - {end_time:.1f}s]**\n\n🎵 *\"{lyric_text_line}\"*",
                "image_prompt": str(scene.get("static_prompt") or scene.get("visual_prompt") or "").strip(),
                "video_prompt": str(scene.get("motion_prompt") or "").strip(),
                "image_output": None,
                "video_output": None,
            }
        )
    return view_models


def _scene_slot_updates_from_models(scene_models: List[Dict[str, Any]]) -> List[Any]:
    updates: List[Any] = []
    padded = list(scene_models[:MAX_SCENES])
    while len(padded) < MAX_SCENES:
        padded.append(
            {
                "visible": False,
                "lyrics_md": "",
                "image_prompt": "",
                "video_prompt": "",
                "image_output": None,
                "video_output": None,
            }
        )

    for scene in padded:
        updates.extend(
            [
                gr.update(visible=bool(scene.get("visible", False))),
                scene.get("lyrics_md", ""),
                scene.get("image_prompt", ""),
                scene.get("video_prompt", ""),
                scene.get("image_output"),
                scene.get("video_output"),
            ]
        )
    return updates


async def _plan_project_scenes(project: WebProject) -> List[Dict[str, Any]]:
    from mellow_link.services.visual_planner import PlannerConfig, VisualPlanner

    planner = VisualPlanner(
        config=PlannerConfig(max_scenes=MAX_SCENES, width=1216, height=704)
    )
    planned = await planner.plan_scenes_async(
        lyrics_segments=project.lyrics_segments[:MAX_SCENES],
        metadata=project.metadata,
        base_seed=None,
    )
    for scene in planned:
        scene["visual_prompt"] = str(scene.get("static_prompt", "") or "").strip()
    return planned


def _default_prompt_template(meta: Dict[str, Any], lyric_text: str) -> str:
    """
    어머니용 기본 프롬프트 템플릿 (너무 어려운 옵션 노출 없이).
    """
    mood = (meta or {}).get("mood") or ""
    story = (meta or {}).get("story") or ""
    artist = (meta or {}).get("artist") or ""
    title = (meta or {}).get("title") or ""
    parts = [
        "cinematic music video still",
        f"song: {artist} - {title}".strip(" -"),
        f"mood: {mood}" if mood else "",
        f"story: {story}" if story else "",
        f"lyrics: {lyric_text}" if lyric_text else "",
        "high quality, sharp focus, film still, soft lighting",
    ]
    return ", ".join([p for p in parts if p])


_TS_LINE_RE = re.compile(
    r"^\[(?P<start>\d+(?:\.\d+)?)\s*-\s*(?P<end>\d+(?:\.\d+)?)\]\s*(?P<text>.+?)\s*$"
)


def _scrub_lyric_echo(desc: str, lyric_text: str) -> str:
    """
    (Wiring Check) 가사가 프롬프트에 직접 섞여 들어가는 현상 차단.
    - 해당 씬의 가사 원문 라인이 그대로 포함되면 제거한다.
    """
    d = (desc or "").strip()
    lt = (lyric_text or "").strip()
    if not d or not lt:
        return d
    if lt in d:
        d = d.replace(lt, "").strip()
    return d


def _parse_lyrics_text(lyrics_text: str) -> List[Dict[str, Any]]:
    """
    웹 UI용 가사 파서:
      1) [start - end] text 포맷이면 타임스탬프 유지
      2) 아니면 줄 단위로 segment 생성 (타임스탬프는 0으로 둠)
    """
    text = (lyrics_text or "").strip()
    if not text:
        return []

    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    segments: List[Dict[str, Any]] = []

    # 타임스탬프 포맷 여부 판단
    has_ts = any(_TS_LINE_RE.match(ln) for ln in lines)
    if has_ts:
        for ln in lines:
            m = _TS_LINE_RE.match(ln)
            if not m:
                # 섞여 들어온 일반 라인은 타임 0으로 처리
                segments.append({"text": ln, "start_time": 0.0, "end_time": 0.0})
                continue
            segments.append(
                {
                    "text": m.group("text").strip(),
                    "start_time": float(m.group("start")),
                    "end_time": float(m.group("end")),
                }
            )
        return segments

    # 일반 텍스트: 타임 정보는 없으므로 0으로
    for ln in lines:
        segments.append({"text": ln, "start_time": 0.0, "end_time": 0.0})
    return segments


async def _connect_services(session: WebSession) -> None:
    if session.connected:
        return
    await assert_media_generation_ready(_load_settings())
    await session.image_service.connect()
    await session.video_service.connect()
    session.connected = True


async def _disconnect_services(session: WebSession) -> None:
    try:
        await session.video_service.disconnect()
    except Exception:
        pass
    try:
        await session.image_service.disconnect()
    except Exception:
        pass
    session.connected = False


async def _maybe_transcribe_audio(audio_file: str, *, progress_cb) -> str:
    """
    전체 가사 입력이 없을 때의 옵션: 음성 인식으로 타임라인 가사를 자동 추출한다.
    (실패해도 UI는 계속 진행 가능)
    """
    try:
        segments, _duration = await _transcribe_audio_segments(audio_file, progress_cb=progress_cb)
        return _segments_to_timestamped_text(segments)
    except Exception as e:
        logger.warning("Auto transcription skipped/failed: %s", e)
        return ""


def _segments_to_timestamped_text(segments: List[Dict[str, Any]]) -> str:
    lines: List[str] = []
    for seg in segments:
        try:
            start = float(seg.get("start_time", 0.0) or 0.0)
            end = float(seg.get("end_time", 0.0) or 0.0)
            text = str(seg.get("text", "") or "").strip()
        except Exception:
            continue
        if not text:
            continue
        lines.append(f"[{start:.2f} - {end:.2f}] {text}")
    return "\n".join(lines).strip()


def _has_timestamps(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return False
    return any(_TS_LINE_RE.match(ln.strip()) for ln in t.splitlines() if ln.strip())


def _split_lyrics_lines(text: str) -> List[str]:
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    # 너무 짧은 노이즈 라인은 제거(선택적으로)
    return [ln for ln in lines if ln]


def _align_pasted_lyrics_to_segments(
    pasted_lyrics: str,
    segments: List[Dict[str, Any]],
    *,
    duration: float = 0.0,
) -> str:
    """
    (복구) "붙여넣은 가사"를 "오디오에서 얻은 타임라인"에 맞춰 정렬한다.

    전략:
    - 타임라인은 Whisper(segments)의 start/end를 사용
    - 텍스트는 pasted_lyrics의 줄을 순서대로 넣는다
    - segments 수가 더 많으면 줄 길이(문자수) 비율로 segments를 묶어서 한 줄에 배정
    - 줄 수가 더 많으면 duration을 기준으로 균등 분배(가능한 경우)
    """
    lyric_lines = _split_lyrics_lines(pasted_lyrics)
    if not lyric_lines:
        return ""

    if not segments:
        # 타임라인을 못 얻으면 타임 0으로라도 포맷 유지
        return "\n".join([f"[0.00 - 0.00] {ln}" for ln in lyric_lines])

    n_lines = len(lyric_lines)
    n_segs = len(segments)

    # 줄 수가 segments보다 많으면: duration 기반 균등 분배
    if n_lines > n_segs and duration and duration > 0:
        step = duration / float(n_lines)
        out = []
        for i, ln in enumerate(lyric_lines):
            s = i * step
            e = min(duration, (i + 1) * step)
            out.append(f"[{s:.2f} - {e:.2f}] {ln}")
        return "\n".join(out).strip()

    # segments를 줄 수만큼 그룹핑
    seg_text_lens = [max(1, len(str(s.get("text", "") or "").strip())) for s in segments]
    total_seg_len = float(sum(seg_text_lens))
    weights = [max(1, len(ln)) for ln in lyric_lines]
    total_weight = float(sum(weights))

    out_lines: List[str] = []
    seg_i = 0
    seg_len_acc = 0.0
    target_acc = 0.0

    for line_i, ln in enumerate(lyric_lines):
        # 남은 segments/lines 고려해서 최소 1개씩 배정되도록
        remaining_lines = n_lines - line_i
        remaining_segs = n_segs - seg_i
        must_leave = max(0, remaining_lines - 1)
        max_take = max(1, remaining_segs - must_leave)  # 이 줄에서 최대 가져갈 수 있는 seg 개수

        target_acc += (weights[line_i] / total_weight) * total_seg_len

        start_time = float(segments[seg_i].get("start_time", 0.0) or 0.0)
        end_time = float(segments[seg_i].get("end_time", 0.0) or 0.0)

        take = 0
        while seg_i < n_segs and take < max_take:
            end_time = float(segments[seg_i].get("end_time", end_time) or end_time)
            seg_len_acc += float(seg_text_lens[seg_i])
            seg_i += 1
            take += 1
            # 목표 길이를 넘기면 다음 줄로 넘김(단, 최소 1개는 이미 take됨)
            if seg_len_acc >= target_acc:
                break

        out_lines.append(f"[{start_time:.2f} - {end_time:.2f}] {ln}")

        if seg_i >= n_segs:
            # 남은 가사 줄이 있으면 마지막 타임을 반복
            for rest in lyric_lines[line_i + 1 :]:
                out_lines.append(f"[{end_time:.2f} - {end_time:.2f}] {rest}")
            break

    return "\n".join(out_lines).strip()


async def _transcribe_audio_segments(audio_file: str, *, progress_cb) -> Tuple[List[Dict[str, Any]], float]:
    """
    오디오에서 타임라인 segments를 얻는다.
    반환:
      - segments: [{"text","start_time","end_time"}...]
      - duration: float (가능하면)
    """
    def _run() -> Tuple[List[Dict[str, Any]], float]:
        """
        backend.audio_engine.LyricAligner를 우선 사용한다.
        (복구된 backend를 기준으로 동작하도록)
        """
        # 1) backend.audio_engine (preferred)
        try:
            from backend.audio_engine import LyricAligner  # type: ignore

            # (안정화) 웹 UI에서 음성인식이 90%대에서 프로세스를 죽이는 케이스가 있어
            # 기본은 CPU로 둔다. GPU를 쓰려면 환경변수로 강제한다:
            # - MELLOW_LYRICS_DEVICE=cuda
            # - MELLOW_LYRICS_MODEL=large-v3 (옵션)
            req_device = (os.getenv("MELLOW_LYRICS_DEVICE", "cpu") or "cpu").strip().lower()
            req_model = (os.getenv("MELLOW_LYRICS_MODEL", "large-v3") or "large-v3").strip()

            device = "cpu"
            if req_device == "cuda":
                try:
                    import torch

                    device = "cuda" if torch.cuda.is_available() else "cpu"
                except Exception:
                    device = "cpu"

            compute_type = "float16" if device == "cuda" else "int8"

            try:
                progress_cb(0.01, f"음성 인식 엔진 준비 중… ({device}, {compute_type}, {req_model})")
            except Exception:
                pass

            aligner = LyricAligner(device=device, compute_type=compute_type)

            # (안정화) Gradio share/tunnel 환경에서 너무 잦은 progress 업데이트가
            # 연결 오류를 유발할 수 있어, 빈도를 제한한다.
            import time as _time

            last_t = 0.0
            last_p = -1.0

            def cb(p: float, msg: str) -> None:
                nonlocal last_t, last_p
                try:
                    now = _time.time()
                    fp = float(p)
                    if (now - last_t) < 0.35 and abs(fp - last_p) < 0.01:
                        return
                    last_t = now
                    last_p = fp
                    progress_cb(fp, str(msg))
                except Exception:
                    pass

            raw = aligner.transcribe(
                audio_path=audio_file,
                model_size=req_model,
                language=None,
                initial_prompt=None,
                progress_callback=cb,
            )

            segs: List[Dict[str, Any]] = []
            max_end = 0.0
            for seg in raw or []:
                # backend format: {"start":..., "end":..., "text":...}
                start = float(seg.get("start", 0.0) or 0.0)
                end = float(seg.get("end", 0.0) or 0.0)
                text = str(seg.get("text", "") or "").strip()
                if not text:
                    continue
                max_end = max(max_end, end)
                segs.append({"text": text, "start_time": start, "end_time": end})
            return segs, max_end

        except Exception as e:
            logger.warning("[WebUI] backend.audio_engine unavailable; fallback to faster-whisper: %s", e)

        # 2) fallback: faster-whisper 직접 호출
        from faster_whisper import WhisperModel  # type: ignore

        try:
            import torch

            device = "cuda" if torch.cuda.is_available() else "cpu"
        except Exception:
            device = "cpu"

        compute_type = "float16" if device == "cuda" else "int8"

        try:
            progress_cb(0.01, f"음성 인식 모델 로딩 중… ({device}, {compute_type})")
        except Exception:
            pass

        model = WhisperModel("large-v3", device=device, compute_type=compute_type)

        try:
            progress_cb(0.05, "가사(타임라인) 자동 생성 중…")
        except Exception:
            pass

        segments_iter, info = model.transcribe(audio_file)
        duration = float(getattr(info, "duration", 0.0) or 0.0)

        segs: List[Dict[str, Any]] = []
        for seg in segments_iter:
            start = float(getattr(seg, "start", 0.0) or 0.0)
            end = float(getattr(seg, "end", 0.0) or 0.0)
            text = str(getattr(seg, "text", "") or "").strip()
            if not text:
                continue
            segs.append({"text": text, "start_time": start, "end_time": end})
            if duration > 0:
                try:
                    progress_cb(min(0.95, max(0.05, end / duration)), f"가사 생성 중… ({end:.1f}s/{duration:.1f}s)")
                except Exception:
                    pass

        try:
            progress_cb(1.0, "가사(타임라인) 자동 생성 완료")
        except Exception:
            pass
        return segs, duration

    return await asyncio.to_thread(_run)


async def start_processing(audio_file, full_lyrics, artist, title, mood, story, progress=gr.Progress()):
    """
    1단계: 세션 생성 + 서비스 연결 + 가사 입력(또는 자동 추출 시도) 후
    '가사 확인' 화면으로 이동.
    """
    _install_web_log_handler()
    settings = _load_settings()
    host, port = _get_comfyui_host_port(settings)

    default_scene_updates = _blank_scene_slot_updates()

    if not audio_file:
        yield (
            _compose_status("노래 파일을 먼저 올려주세요!\n\n왼쪽 위에서 파일을 선택해주세요."),
            None,  # engine_state (WebSession)
            None,  # audio_review
            "",  # lyrics_input
            gr.update(visible=False),  # lyric_review_group
            gr.update(visible=False),  # scene_workspace
            gr.update(visible=True),  # start_btn
            None,  # final video
            *default_scene_updates,
        )
        return

    output_dirs = get_output_directories(_load_settings())
    img_dir = output_dirs["images"]
    vid_dir = output_dirs["videos"]

    project_name = Path(audio_file).stem
    meta = {"artist": artist, "title": title, "mood": mood, "story": story}

    # Create session + connect services
    readiness = await get_runtime_readiness(settings)
    if not readiness["media_ai_enabled"]:
        yield (
            _compose_status(
                "이미지/영상 생성 기능이 비활성화되어 있어요.\n\n"
                "현재 ENABLE_MEDIA_AI=0 입니다. ENABLE_MEDIA_AI=1로 설정한 뒤 다시 시작해주세요."
            ),
            None,
            None,
            "",
            gr.update(visible=False),
            gr.update(visible=False),
            gr.update(visible=True),
            None,
            *default_scene_updates,
        )
        return

    yield (
        _compose_status(
            "시스템을 준비하고 있어요...\n\n"
            f"ComfyUI readiness 확인 중입니다. ({host}:{port})"
        ),
        None,
        None,
        "",
        gr.update(visible=False),
        gr.update(visible=False),
        gr.update(visible=False),
        None,
        *default_scene_updates,
    )

    session = WebSession(
        project=WebProject(project_name=project_name, audio_file_path=str(audio_file), metadata=meta),
        image_service=ImageService(host=host, port=port, output_dir=img_dir),
        video_service=VideoService(host=host, port=port, output_dir=vid_dir),
        connected=False,
    )

    try:
        await _connect_services(session)
    except Exception as e:
        yield (
            _compose_status(
                "연결에 실패했어요.\n\n"
                f"ComfyUI readiness 오류: {e}\n\n"
                f"설정 endpoint: {host}:{port}\n"
                f"ENABLE_MEDIA_AI={'1' if is_media_ai_enabled() else '0'}"
            ),
            None,
            None,
            "",
            gr.update(visible=False),
            gr.update(visible=False),
            gr.update(visible=True),
            None,
            *default_scene_updates,
        )
        return

    # Lyrics:
    # - 사용자가 전체 가사를 붙여넣은 경우에도, (타임스탬프가 없으면) 오디오 타임라인에 맞춰 정렬한다.
    # - 사용자가 가사를 비워두면, 오디오에서 자동으로 타임라인 가사를 생성한다.
    lyrics_text = (full_lyrics or "").strip()
    if lyrics_text and not _has_timestamps(lyrics_text):
        progress(0.0, desc="붙여넣은 가사를 타임라인에 맞춰 정렬 중…")
        segs, dur = await _transcribe_audio_segments(str(audio_file), progress_cb=lambda p, m: progress(p, desc=m))
        if segs:
            lyrics_text = _align_pasted_lyrics_to_segments(lyrics_text, segs, duration=dur)

    if not lyrics_text:
        progress(0.0, desc="가사를 자동으로 생성 중(타임라인)…")
        segs, _dur = await _transcribe_audio_segments(str(audio_file), progress_cb=lambda p, m: progress(p, desc=m))
        lyrics_text = _segments_to_timestamped_text(segs) or ""

    if not lyrics_text:
        lyrics_text = (
            "여기에 전체 가사를 붙여넣어주세요.\n"
            "(가사가 있어야 장면을 만들 수 있어요)\n"
        )

    session.project.lyrics_text = lyrics_text

    yield (
        _compose_status(
            "준비 완료!\n\n가사를 확인해주세요.\n틀린 부분이 있으면 고치고, 아래 버튼을 눌러주세요."
        ),
        session,  # engine_state
        str(audio_file),  # audio_review (playback)
        lyrics_text,
        gr.update(visible=True),  # lyric_review_group
        gr.update(visible=False),  # scene_workspace
        gr.update(visible=False),  # start button
        None,  # final video
        *default_scene_updates,
    )


async def confirm_lyrics_and_continue(engine_state, lyrics_text, progress=gr.Progress()):
    """
    2단계: 가사 확정 -> 씬 플랜(간단 자동 생성) -> 씬별 작업대 표시.
    """
    _install_web_log_handler()

    default_scene_updates = _blank_scene_slot_updates()

    if engine_state is None:
        yield (
            _compose_status("세션이 끊어졌어요.\n\n처음부터 다시 시작해주세요!"),
            None,
            None,
            "",
            gr.update(visible=False),
            gr.update(visible=False),
            gr.update(visible=True),
            None,
            *default_scene_updates,
        )
        return

    session: WebSession = engine_state
    session.project.lyrics_text = (lyrics_text or "").strip()
    session.project.lyrics_segments = _parse_lyrics_text(session.project.lyrics_text)

    if not session.project.lyrics_segments:
        yield (
            _compose_status("가사가 비어있어요.\n\n가사를 입력한 뒤 다시 눌러주세요."),
            session,
            str(session.project.audio_file_path) if session.project.audio_file_path else None,
            lyrics_text,
            gr.update(visible=True),
            gr.update(visible=False),
            gr.update(visible=False),
            None,
            *default_scene_updates,
        )
        return

    session.project.scene_plans = await _plan_project_scenes(session.project)
    scene_models = _build_scene_view_models(session.project)
    plan_count = len(scene_models)
    scene_updates = _scene_slot_updates_from_models(scene_models)

    yield (
        _compose_status(
            f"장면 {plan_count}개가 준비됐어요!\n\n이제 각 씬의 버튼을 눌러 이미지/영상을 만들 수 있어요."
        ),
        session,
        str(session.project.audio_file_path) if session.project.audio_file_path else None,
        session.project.lyrics_text,
        gr.update(visible=False),  # hide lyric review
        gr.update(visible=True),  # show scene workspace
        gr.update(visible=False),  # hide start
        None,
        *scene_updates,
    )


async def generate_single_scene_image(
    session: WebSession,
    scene_index: int,
    static_prompt: str,
    *,
    report_progress: Optional[Callable[[float, str], None]] = None,
    progress=gr.Progress(),
) -> str:
    """서비스 기반 이미지 생성 (Flux workflow)."""
    await _connect_services(session)
    settings = _load_settings()
    img_defaults = _get_image_defaults(settings)

    if not isinstance(static_prompt, str):
        raise TypeError(f"static_prompt must be str, got {type(static_prompt).__name__}: {static_prompt!r}")

    req = ImageRequest(
        static_prompt=static_prompt.strip(),
        prompt=static_prompt.strip(),
        negative_prompt=str(
            (session.project.scene_plans[scene_index].get("negative_prompt", "") if scene_index < len(session.project.scene_plans) else "")
            or ""
        ).strip(),
        width=img_defaults["width"],
        height=img_defaults["height"],
        steps=img_defaults["steps"],
        cfg_scale=img_defaults["cfg_scale"],
        seed=int(
            (session.project.scene_plans[scene_index].get("seed", -1) if scene_index < len(session.project.scene_plans) else -1)
        ),
        batch_size=1,
        model=None,
        workflow=img_defaults["workflow"],
        sampler_name=img_defaults["sampler_name"],
        scheduler=img_defaults["scheduler"],
        denoise=1.0,
        provenance=_project_provenance(session.project, artifact_type="image", scene_index=scene_index),
    )

    async def on_progress(p: float, msg: str) -> None:
        # ImageService는 0~100 또는 0~? 형태로 올 수 있어 방어적으로 처리
        pct = float(p)
        if pct > 1.0:
            progress(min(1.0, pct / 100.0), desc=f"씬 {scene_index+1} 이미지: {msg}")
            if report_progress:
                try:
                    report_progress(min(100.0, max(0.0, pct)), msg)
                except Exception:
                    pass
        else:
            progress(min(1.0, pct), desc=f"씬 {scene_index+1} 이미지: {msg}")
            if report_progress:
                try:
                    report_progress(min(100.0, max(0.0, pct * 100.0)), msg)
                except Exception:
                    pass

    path = await session.image_service.generate_image(req, on_progress=on_progress)
    return str(path)


async def generate_single_scene_video(
    session: WebSession,
    scene_index: int,
    image_path: str,
    motion_prompt: str,
    *,
    report_progress: Optional[Callable[[float, str], None]] = None,
    progress=gr.Progress(),
) -> str:
    """서비스 기반 비디오 생성 (LOCAL_MOTION_LOOP default)."""
    await _connect_services(session)
    if not isinstance(motion_prompt, str):
        raise TypeError(f"motion_prompt must be str, got {type(motion_prompt).__name__}: {motion_prompt!r}")
    mp = _normalize_loop_motion_prompt(motion_prompt.strip())
    video_runtime = get_video_generation_settings(_load_settings())
    motion_spike = get_motion_video_spike_settings(_load_settings())
    req = VideoRequest(
        image_path=str(image_path),
        motion_prompt=mp,
        prompt=mp,
        mode="LOCAL_MOTION_LOOP",
        motion_bucket_id=1,
        workflow=str(video_runtime.get("local_motion_workflow") or motion_spike.get("workflow") or "ltx_2b_v0_9_ckpt_i2v_lowmem.json"),
        width=int(video_runtime.get("local_motion_width", motion_spike.get("width", 576)) or 576),
        height=int(video_runtime.get("local_motion_height", motion_spike.get("height", 320)) or 320),
        target_duration=float(video_runtime.get("local_motion_duration", motion_spike.get("duration_seconds", 2.83)) or 2.83),
        loop_mode="crossfade",
        overlap_seconds=0.35,
        fps=int(video_runtime.get("local_motion_fps", motion_spike.get("fps", 6)) or 6),
        provenance=_project_provenance(session.project, artifact_type="video", scene_index=scene_index),
    )

    pct_state = {"pct": 0.0, "msg": "Queued"}

    async def on_vid_progress(pct: float, msg: str) -> None:
        pct_state["pct"] = float(pct)
        pct_state["msg"] = str(msg)
        if report_progress:
            try:
                report_progress(float(pct), str(msg))
            except Exception:
                pass

    progress(0.05, desc=f"씬 {scene_index+1} 영상: 입력 준비 중…")
    task = asyncio.create_task(session.video_service.generate_video(req, on_progress=on_vid_progress))
    t0 = time.time()
    while not task.done():
        st = session.video_service.get_status().name if hasattr(session.video_service, "get_status") else "WORKING"
        elapsed = int(time.time() - t0)
        # 서비스 progress 이벤트가 없을 때도 상태를 보여준다
        p = pct_state["pct"]
        msg = pct_state["msg"]
        progress(0.2, desc=f"씬 {scene_index+1} 영상 생성 중… ({st}, {elapsed}s, {int(p)}%) {msg}")
        await asyncio.sleep(1.0)
    progress(0.9, desc=f"씬 {scene_index+1} 후처리/저장 중…")
    out = await task
    progress(1.0, desc=f"씬 {scene_index+1} 완료")
    return str(out)


async def finalize_video(session: WebSession, progress=gr.Progress()) -> str:
    """
    (CRITICAL) 최종 영상 합치기 (Merge Clips)
    - 생성된 클립(.mp4)을 순서대로 병합
    - 배경음악(BGM)과 길이 맞춤(가능한 경우: 오디오 길이에 맞춰 비디오를 트림/반복)
    - outputs/final/ 에 고유 파일명으로 저장
    """

    def _which(exe: str) -> str:
        p = shutil.which(exe)
        if not p:
            raise RuntimeError(
                f"{exe}를 찾을 수 없습니다. ffmpeg/ffprobe가 PATH에 설치되어 있어야 합니다.\n"
                f"- Windows: winget/choco로 ffmpeg 설치 후 새 터미널에서 재실행\n"
                f"- 또는 ffmpeg.exe가 있는 폴더를 PATH에 추가"
            )
        return p

    ffmpeg = _which("ffmpeg")
    ffprobe = shutil.which("ffprobe")  # optional

    clips = [Path(p) for p in (session.project.generated_clips or []) if str(p).strip()]
    clips = [p for p in clips if p.exists()]
    if not clips:
        raise RuntimeError("먼저 영상 클립을 생성해주세요. (generated_clips가 비어있음)")

    audio_path = Path(session.project.audio_file_path or "")
    if not audio_path.exists():
        raise RuntimeError("오디오 파일이 없습니다. (BGM 경로가 유효하지 않음)")

    out_dir = get_output_directories(_load_settings())["final"]
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = out_dir / f"{session.project.project_name}_{ts}.mp4"
    concat_list = out_dir / f".concat_{session.project.project_name}_{ts}.txt"

    def _probe_duration(p: Path) -> Optional[float]:
        if not ffprobe:
            return None
        try:
            r = subprocess.run(
                [
                    ffprobe,
                    "-v",
                    "error",
                    "-show_entries",
                    "format=duration",
                    "-of",
                    "default=nw=1:nk=1",
                    str(p),
                ],
                capture_output=True,
                text=True,
                check=True,
            )
            s = (r.stdout or "").strip()
            return float(s) if s else None
        except Exception:
            return None

    # 오디오 길이에 맞춰 “마지막 클립 반복”으로 비디오 길이를 늘릴 수 있으면 늘린다.
    audio_dur = _probe_duration(audio_path)
    clip_durs = [(_probe_duration(p) or 0.0) for p in clips]
    total_clip_dur = sum(clip_durs) if all(d > 0 for d in clip_durs) else None
    if audio_dur and total_clip_dur and audio_dur > total_clip_dur + 0.1:
        last = clips[-1]
        last_dur = clip_durs[-1] if clip_durs else 0.0
        if last_dur > 0.0:
            while total_clip_dur < audio_dur:
                clips.append(last)
                total_clip_dur += last_dur

    # concat demuxer 리스트 파일 작성 (절대경로 + forward slash)
    lines = [f"file '{p.resolve().as_posix()}'" for p in clips]
    concat_list.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def _run_merge() -> None:
        # 모든 클립을 재인코딩해서 concat 안정성 확보
        cmd = [
            ffmpeg,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_list),
            "-i",
            str(audio_path),
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-vf",
            "scale=1216:704:flags=lanczos,fps=30,format=yuv420p",
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "18",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-movflags",
            "+faststart",
        ]
        # 가능하면 최종 길이를 “오디오 길이”로 고정
        if audio_dur and audio_dur > 0:
            cmd += ["-t", f"{audio_dur:.3f}"]
        else:
            cmd += ["-shortest"]
        cmd.append(str(out_path))

        logger.info("[WebUI] Final merge cmd: %s", " ".join(cmd))
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            err = (r.stderr or "").strip()[-2000:]
            raise RuntimeError(f"ffmpeg merge failed (code={r.returncode}):\n{err}")

    progress(0.1, desc="🎉 최종 합치기: ffmpeg 준비 중…")
    await asyncio.to_thread(_run_merge)
    progress(1.0, desc="🎉 최종 합치기 완료!")

    if not out_path.exists():
        raise RuntimeError("최종 파일이 생성되지 않았습니다. (outputs/final에 결과 없음)")

    session.project.final_video_path = str(out_path)
    write_sidecar_best_effort(
        out_path,
        artifact_type="final_video",
        source={
            "project_id": session.project.project_name,
            "audio_path": session.project.audio_file_path,
            "clip_count": len(clips),
        },
        runtime=_web_runtime_info(),
        request={"strip_prompt_metadata": bool(_load_settings().get("outputs", {}).get("strip_prompt_metadata", False))},
    )
    ensure_sidecar(
        out_path,
        artifact_type="final_video",
        source={
            "project_id": session.project.project_name,
            "audio_path": session.project.audio_file_path,
            "clip_count": len(clips),
        },
        runtime=_web_runtime_info(),
        request={"strip_prompt_metadata": bool(_load_settings().get("outputs", {}).get("strip_prompt_metadata", False))},
    )
    return str(out_path)


# =============================================================================
# Helper Functions for Scene Plan Display
# =============================================================================

def format_scene_plans_for_display(project) -> str:
    """
    Format scene plans as editable JSON text for user review.
    (Legacy function - kept for backward compatibility)

    Returns:
        Formatted JSON string of scene plans
    """
    if not project:
        return "[]"

    scene_plans = []

    # Try different attribute names
    if hasattr(project, 'scene_plans') and project.scene_plans:
        scene_plans = project.scene_plans
    elif hasattr(project, 'visual_plans') and project.visual_plans:
        scene_plans = project.visual_plans

    if not scene_plans:
        return "[]"

    # Format as pretty JSON for easy editing
    try:
        return json.dumps(scene_plans, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Failed to format scene plans: {e}")
        return "[]"


def get_scene_plans_data(project) -> List[Dict[str, Any]]:
    """
    Get scene plans data with lyrics for storyboard display.

    Returns:
        List of scene plan dictionaries with lyrics attached
    """
    if not project:
        return []

    scene_plans = []

    # Get scene plans
    if hasattr(project, 'scene_plans') and project.scene_plans:
        scene_plans = project.scene_plans
    elif hasattr(project, 'visual_plans') and project.visual_plans:
        scene_plans = project.visual_plans

    if not scene_plans:
        return []

    # Get lyrics segments for matching
    lyrics_segments = []
    if hasattr(project, 'lyrics_segments') and project.lyrics_segments:
        lyrics_segments = project.lyrics_segments

    # Combine scene plans with lyrics
    result = []
    for i, plan in enumerate(scene_plans):
        if isinstance(plan, dict):
            scene_data = plan.copy()

            # Match with lyrics segment by index or segment_id
            lyric_text = ""
            start_time = 0.0
            end_time = 0.0
            if i < len(lyrics_segments):
                seg = lyrics_segments[i]
                if hasattr(seg, 'text'):
                    lyric_text = seg.text
                    start_time = getattr(seg, 'start_time', 0.0)
                    end_time = getattr(seg, 'end_time', 0.0)
                elif isinstance(seg, dict):
                    lyric_text = seg.get('text', '')
                    start_time = seg.get('start_time', 0.0)
                    end_time = seg.get('end_time', 0.0)

            # If segment_id exists, try to match
            if not lyric_text and 'segment_id' in scene_data:
                for seg in lyrics_segments:
                    seg_id = getattr(seg, 'id', None) if hasattr(seg, 'id') else (seg.get('id') if isinstance(seg, dict) else None)
                    if seg_id == scene_data.get('segment_id'):
                        lyric_text = getattr(seg, 'text', '') if hasattr(seg, 'text') else (seg.get('text', '') if isinstance(seg, dict) else '')
                        start_time = getattr(seg, 'start_time', 0.0) if hasattr(seg, 'start_time') else (seg.get('start_time', 0.0) if isinstance(seg, dict) else 0.0)
                        end_time = getattr(seg, 'end_time', 0.0) if hasattr(seg, 'end_time') else (seg.get('end_time', 0.0) if isinstance(seg, dict) else 0.0)
                        break

            scene_data['lyric_text'] = lyric_text
            scene_data['start_time'] = start_time
            scene_data['end_time'] = end_time
            scene_data['scene_index'] = i + 1
            result.append(scene_data)

    return result


def format_scene_plans_as_table(project) -> List[List[str]]:
    """
    Format scene plans as a table for gr.Dataframe display.

    Returns:
        List of rows: [[index, visual_prompt, camera_movement, lighting], ...]
    """
    if not project:
        return []

    scene_plans = []

    if hasattr(project, 'scene_plans') and project.scene_plans:
        scene_plans = project.scene_plans
    elif hasattr(project, 'visual_plans') and project.visual_plans:
        scene_plans = project.visual_plans

    if not scene_plans:
        return []

    rows = []
    for i, plan in enumerate(scene_plans):
        if isinstance(plan, dict):
            rows.append([
                str(i + 1),
                plan.get('visual_prompt', '')[:100] + '...' if len(plan.get('visual_prompt', '')) > 100 else plan.get('visual_prompt', ''),
                plan.get('camera_movement', 'static'),
                plan.get('lighting', 'soft'),
            ])

    return rows


def parse_edited_scene_plans(plans_json: str, project) -> bool:
    """
    Parse edited scene plans JSON back into project.

    Args:
        plans_json: JSON string of edited scene plans
        project: Project state object

    Returns:
        True if successful
    """
    if not plans_json.strip() or plans_json.strip() == "[]":
        return False

    try:
        parsed_plans = json.loads(plans_json)

        if not isinstance(parsed_plans, list):
            return False

        # Update project
        if hasattr(project, 'scene_plans'):
            project.scene_plans = parsed_plans
        if hasattr(project, 'visual_plans'):
            project.visual_plans = parsed_plans

        return True

    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse scene plans JSON: {e}")
        return False
    except Exception as e:
        logger.error(f"Failed to update scene plans: {e}")
        return False


def get_generated_images(project) -> List[str]:
    """
    Get list of generated image paths from project.

    Returns:
        List of image file paths
    """
    if not project:
        return []

    images = []

    # Try generated_images attribute
    if hasattr(project, 'generated_images') and project.generated_images:
        images = project.generated_images

    # Try images dictionary
    elif hasattr(project, 'images') and project.images:
        for key, img_data in project.images.items():
            if isinstance(img_data, dict) and 'path' in img_data:
                images.append(img_data['path'])
            elif isinstance(img_data, str):
                images.append(img_data)

    # Filter to only existing files
    existing = [p for p in images if Path(p).exists()]

    return existing


def get_scene_image(project, scene_index: int) -> Optional[str]:
    """
    Get generated image path for a specific scene.

    Args:
        project: Project state
        scene_index: 0-based scene index

    Returns:
        Image file path or None
    """
    if not project:
        return None

    # Try images dictionary with scene index key
    if hasattr(project, 'images') and project.images:
        key = f"scene_{scene_index}"
        if key in project.images:
            img_data = project.images[key]
            if isinstance(img_data, dict) and 'path' in img_data:
                path = img_data['path']
            elif isinstance(img_data, str):
                path = img_data
            else:
                return None
            if Path(path).exists():
                return path

    # Try generated_images list
    if hasattr(project, 'generated_images') and project.generated_images:
        if scene_index < len(project.generated_images):
            path = project.generated_images[scene_index]
            if Path(path).exists():
                return path

    return None


def get_scene_video(project, scene_index: int) -> Optional[str]:
    """
    Get generated video clip path for a specific scene.

    Args:
        project: Project state
        scene_index: 0-based scene index

    Returns:
        Video file path or None
    """
    if not project:
        return None

    # Try video_clips dictionary
    if hasattr(project, 'video_clips') and project.video_clips:
        key = f"scene_{scene_index}"
        if key in project.video_clips:
            clip_data = project.video_clips[key]
            if isinstance(clip_data, dict) and 'path' in clip_data:
                path = clip_data['path']
            elif isinstance(clip_data, str):
                path = clip_data
            else:
                return None
            if Path(path).exists():
                return path

    # Try generated_clips list
    if hasattr(project, 'generated_clips') and project.generated_clips:
        if scene_index < len(project.generated_clips):
            path = project.generated_clips[scene_index]
            if Path(path).exists():
                return path

    return None


# ============================================================================
# UI Layout - Director's Control Panel (감독 컨트롤 패널)
# ============================================================================

# 어르신 친화적 테마 (큰 글씨, 명확한 색상)
custom_css = """
.gradio-container { font-size: 18px !important; }
.gr-button { font-size: 18px !important; padding: 12px 24px !important; }
.gr-input, .gr-textbox textarea { font-size: 16px !important; }
h1 { font-size: 32px !important; }
h2 { font-size: 26px !important; }
h3 { font-size: 22px !important; }
label { font-size: 16px !important; font-weight: bold !important; }

/* Scene Row Styling */
.scene-row {
    border: 2px solid #e1e8ed;
    border-radius: 12px;
    padding: 16px;
    margin-bottom: 16px;
    background: linear-gradient(135deg, #f8f9fa 0%, #ffffff 100%);
}
.scene-row:hover {
    border-color: #667eea;
    box-shadow: 0 4px 12px rgba(102, 126, 234, 0.15);
}

/* Batch buttons */
.batch-btn {
    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%) !important;
    color: white !important;
    font-weight: bold !important;
}
"""

with gr.Blocks(
    title="감독 컨트롤 패널 - 뮤직비디오 만들기",
) as demo:
    gr.Markdown("""
    # 🎬 감독 컨트롤 패널
    ### 씬 단위로 뮤직비디오를 만들어보세요
    **개별 제어**: 각 씬의 버튼을 눌러 이미지/영상을 생성합니다. (일괄 자동 생성 없음)
    """)

    # Hidden state to persist engine between interactions
    engine_state = gr.State(value=None)

    # Resource lock state: prevents concurrent GPU operations (5070 Ti idle until requested)
    is_processing = gr.State(value=False)

    # =========================================================================
    # Top Section: Input Controls
    # =========================================================================
    with gr.Row():
        # Left Column - Audio & Lyrics Input
        with gr.Column(scale=1):
            audio_input = gr.Audio(
                type="filepath",
                label="🎧 1단계: 노래 파일 올리기",
            )

            # Full Lyrics Input (NEW)
            full_lyrics_input = gr.Textbox(
                label="📜 전체 가사 원문 (선택사항)",
                lines=10,
                placeholder="여기에 전체 가사를 붙여넣으면 타임라인을 더 정확하게 잡아줍니다.\n\n예:\n사랑은 늘 도망가\n너를 만나 행복했어\n그 시절이 그리워\n...",
                info="가사를 직접 입력하면 자동 인식 대신 입력한 가사를 사용합니다."
            )

            with gr.Accordion("📝 노래 정보 입력 (선택사항)", open=False):
                artist_input = gr.Textbox(
                    label="가수 이름",
                    placeholder="예: 임영웅, 송가인"
                )
                title_input = gr.Textbox(
                    label="노래 제목",
                    placeholder="예: 사랑은 늘 도망가"
                )
                mood_input = gr.Textbox(
                    label="느낌/분위기",
                    placeholder="예: 따뜻한, 그리운, 봄날"
                )
                story_input = gr.Textbox(
                    label="원하는 장면 설명",
                    lines=3,
                    placeholder="예: 꽃이 피는 봄날, 푸른 바다, 노을지는 하늘..."
                )

            btn_start = gr.Button(
                "🎬 분석 시작!",
                variant="primary",
                size="lg"
            )

        # Right Column - Status
        with gr.Column(scale=1):
            status_output = gr.Textbox(
                label="📢 진행 상황",
                lines=8,
                interactive=False,
                value="왼쪽에서 노래 파일을 올리고\n'분석 시작' 버튼을 눌러주세요!\n\n전체 가사를 알고 있다면\n가사 입력란에 붙여넣으면\n더 정확한 결과를 얻을 수 있어요."
            )

            # Final Export & Preview
            video_output = gr.Video(label="Final Export & Preview (최종 검수실)")
            final_files = gr.Dropdown(
                label="outputs/final/ 결과 목록",
                choices=[],
                value=None,
                interactive=True,
            )
            final_hint = gr.Markdown(
                "최종 합치기 결과는 `outputs/final/` 폴더에 저장됩니다. (자동 갱신)",
            )
            final_timer = gr.Timer(2.0)

    # =========================================================================
    # Lyrics Review Section (shown after audio analysis)
    # =========================================================================
    with gr.Group(visible=False) as lyric_review_group:
        gr.Markdown("### ✏️ 가사 확인하기")
        gr.Markdown(
            "아래에 노래 가사가 나왔어요. 틀린 부분이 있으면 수정해주세요.\n"
            "다 확인했으면 **'가사 확인 완료'** 버튼을 눌러주세요!"
        )

        # (UX 복구) 가사를 "들으면서" 입력할 수 있도록,
        # 가사 확인 화면 안에도 재생기를 고정 배치한다.
        audio_review = gr.Audio(
            type="filepath",
            label="🎧 노래 듣기 (가사 입력용)",
            interactive=False,  # 업로드는 위의 audio_input에서만
        )

        lyrics_input = gr.Textbox(
            label="🎤 가사",
            lines=15,
            max_lines=30,
            interactive=True,
        )

        btn_confirm_lyrics = gr.Button(
            "✅ 가사 확인 완료! 장면 생성하기",
            variant="primary",
            size="lg",
        )

    # =========================================================================
    # Scene Workspace - Director's Control Panel (Human-in-the-Loop)
    # =========================================================================
    with gr.Group(visible=False) as scene_workspace:
        gr.Markdown("## 🎬 씬별 작업대 (Scene-by-Scene Production)")
        gr.Markdown(
            "각 씬의 **[🎨 이미지 생성]** 버튼을 눌러 개별적으로 이미지를 생성하세요.\n"
            "이미지가 생성되면 **[🎬 영상 생성]** 버튼이 활성화됩니다.\n\n"
            "⚠️ **한 번에 하나의 작업만 가능합니다** (GPU 리소스 보호)"
        )

        # Processing indicator
        processing_indicator = gr.Markdown(
            "🟢 **대기 중** - 버튼을 눌러 작업을 시작하세요",
            elem_id="processing_indicator"
        )

        # Final assembly button (only after clips are ready)
        with gr.Row():
            btn_finalize = gr.Button(
                "🎉 최종 영상 합치기 (모든 클립 완성 후)",
                variant="primary",
                size="lg",
            )

        gr.Markdown("---")

        # Scene Rows - Pre-generate MAX_SCENES rows (controlled by visibility)
        scene_groups = []
        scene_lyrics_mds = []
        scene_image_prompt_inputs = []
        scene_video_prompt_inputs = []
        scene_image_outputs = []
        scene_video_outputs = []
        scene_gen_image_btns = []
        scene_gen_video_btns = []

        for i in range(MAX_SCENES):
            with gr.Group(visible=False, elem_classes="scene-row") as scene_group:
                with gr.Row():
                    # Left Column: Planning (기획)
                    with gr.Column(scale=1):
                        lyrics_md = gr.Markdown(
                            value=f"### 씬 {i+1}\n*가사가 여기에 표시됩니다*",
                        )
                        image_prompt_input = gr.Textbox(
                            label="[이미지 묘사]",
                            lines=4,
                            interactive=True,
                            placeholder="예: cinematic music video still, 벽에 달린 뿌연 거울, soft lighting, high quality, 16:9 composition",
                        )
                        video_prompt_input = gr.Textbox(
                            label="[국소 모션 루프 연출]",
                            lines=3,
                            interactive=True,
                            placeholder="예: 커피잔의 김이 계속 올라오고, 창빛이 은은하게 흔들리며, 커튼 끝과 앞쪽 풀만 조금 더 또렷하게 움직인다.",
                        )
                        btn_gen_image = gr.Button(
                            f"🎨 씬 {i+1} 이미지 생성",
                            variant="primary",
                            size="sm",
                        )

                    # Center Column: Visualization (시각화)
                    with gr.Column(scale=1):
                        # CRITICAL: type="filepath" 로 설정해야
                        # 다음 단계(영상 생성)에서 실제 파일 경로가 backend로 전달된다.
                        image_output = gr.Image(
                            label="생성된 이미지",
                            type="filepath",
                            interactive=False,
                            height=256,
                        )
                        btn_gen_video = gr.Button(
                            f"🎬 씬 {i+1} 영상 생성",
                            variant="primary",
                            size="sm",
                        )

                    # Right Column: Motion (영상화)
                    with gr.Column(scale=1):
                        video_output = gr.Video(
                            label="완성된 클립",
                            height=256,
                        )

                scene_groups.append(scene_group)
                scene_lyrics_mds.append(lyrics_md)
                scene_image_prompt_inputs.append(image_prompt_input)
                scene_video_prompt_inputs.append(video_prompt_input)
                scene_image_outputs.append(image_output)
                scene_video_outputs.append(video_output)
                scene_gen_image_btns.append(btn_gen_image)
                scene_gen_video_btns.append(btn_gen_video)

    # =========================================================================
    # Event Handlers (Human-in-the-Loop Control System)
    # =========================================================================

    # Build output list for start_processing
    start_outputs = [
        status_output,
        engine_state,
        audio_review,
        lyrics_input,
        lyric_review_group,
        scene_workspace,
        btn_start,
        video_output,
    ]
    # Add scene row components
    for i in range(MAX_SCENES):
        start_outputs.extend([
            scene_groups[i],
            scene_lyrics_mds[i],
            scene_image_prompt_inputs[i],
            scene_video_prompt_inputs[i],
            scene_image_outputs[i],
            scene_video_outputs[i],
        ])

    # Start button -> run until AUDIO_REVIEW
    btn_start.click(
        fn=start_processing,
        inputs=[audio_input, full_lyrics_input, artist_input, title_input, mood_input, story_input],
        outputs=start_outputs,
        api_name="start_processing",
    )

    # Confirm lyrics button -> run until VISUAL_SCRIPTING_REVIEW, then show scene workspace
    btn_confirm_lyrics.click(
        fn=confirm_lyrics_and_continue,
        inputs=[engine_state, lyrics_input],
        outputs=start_outputs,
        api_name="confirm_lyrics_and_continue",
    )

    # =========================================================================
    # On-Demand Scene Generation (Individual Buttons with Resource Lock)
    # =========================================================================

    # Helper to update processing indicator
    def get_processing_indicator(is_busy: bool) -> str:
        if is_busy:
            return "🔴 **작업 중** - GPU가 이미지/영상을 생성하고 있습니다. 잠시 기다려주세요..."
        else:
            return "🟢 **대기 중** - 버튼을 눌러 작업을 시작하세요"

    # Individual scene IMAGE generation buttons (with resource lock)
    for i in range(MAX_SCENES):
        def make_image_handler(idx):
            async def handler(engine_state, proc_state, image_prompt_text):
                """
                On-demand image generation for a single scene.
                Implements resource lock to prevent concurrent GPU operations.
                """
                _install_web_log_handler()
                if engine_state is None:
                    yield (
                        _compose_status("세션이 끊어졌습니다. 새로 시작해주세요."),
                        None,
                        False,
                        get_processing_indicator(False),
                        gr.update(value=f"🎨 씬 {idx+1} 이미지 생성", interactive=True),
                    )
                    return

                session: WebSession = engine_state

                # Resource lock check
                if proc_state:
                    yield (
                        _compose_status("⏳ 다른 작업이 진행 중입니다. 잠시 기다려주세요."),
                        None,
                        True,
                        get_processing_indicator(True)
                        ,
                        gr.update(value=f"🎨 씬 {idx+1} 이미지 생성", interactive=False),
                    )
                    return

                # Lock on (immediate UI update)
                yield (
                    _compose_status(f"🎨 씬 {idx+1} 이미지 생성 시작…"),
                    None,
                    True,
                    get_processing_indicator(True),
                    gr.update(value="Generating... (0%)", interactive=False),
                )

                try:
                    # Keep prompt in plan
                    if idx < len(session.project.scene_plans):
                        # UI 텍스트박스는 "정적 장면"을 편집한다고 가정
                        session.project.scene_plans[idx]["static_scene_description"] = str(image_prompt_text or "")

                    pct = 0.0
                    msg = "Queued"

                    def report(p: float, m: str) -> None:
                        nonlocal pct, msg
                        pct = float(p)
                        msg = str(m)

                    # ImageService: [이미지 묘사] 칸 텍스트를 그대로 사용 (가사 혼입은 scrub)
                    lyric_line = str(session.project.scene_plans[idx].get("lyric_text", "") or "").strip() if idx < len(session.project.scene_plans) else ""
                    static_prompt = _scrub_lyric_echo(str(image_prompt_text or "").strip(), lyric_line)
                    logger.info("[WebUI] scene_%s static_prompt=%r", idx + 1, static_prompt[:220])

                    task = asyncio.create_task(
                        generate_single_scene_image(
                            session,
                            idx,
                            static_prompt,
                            report_progress=report,
                        )
                    )
                    while not task.done():
                        yield (
                            _compose_status(f"🎨 씬 {idx+1} 이미지 생성 중… ({msg})"),
                            None,
                            True,
                            get_processing_indicator(True),
                            gr.update(value=f"Generating... ({int(max(0.0, min(100.0, pct)))}%)", interactive=False),
                        )
                        await asyncio.sleep(1.0)
                    img_path = await task
                    if img_path and Path(img_path).exists():
                        session.project.images[f"scene_{idx}"] = {"path": str(img_path)}
                        # keep list form too
                        while len(session.project.generated_images) <= idx:
                            session.project.generated_images.append("")
                        session.project.generated_images[idx] = str(img_path)

                        yield (
                            _compose_status(f"✅ 씬 {idx+1} 이미지 생성 완료!"),
                            str(img_path),
                            False,
                            get_processing_indicator(False),
                            gr.update(value=f"🎨 씬 {idx+1} 이미지 생성", interactive=True),
                        )
                    else:
                        yield (
                            _compose_status("⚠️ 이미지 파일을 찾을 수 없습니다."),
                            None,
                            False,
                            get_processing_indicator(False),
                            gr.update(value=f"🎨 씬 {idx+1} 이미지 생성", interactive=True),
                        )
                except Exception as e:
                    yield (
                        _compose_status(f"⚠️ 이미지 생성 실패: {e}"),
                        None,
                        False,
                        get_processing_indicator(False),
                        gr.update(value=f"🎨 씬 {idx+1} 이미지 생성", interactive=True),
                    )
            return handler

        scene_gen_image_btns[i].click(
            fn=make_image_handler(i),
            inputs=[engine_state, is_processing, scene_image_prompt_inputs[i]],
            outputs=[status_output, scene_image_outputs[i], is_processing, processing_indicator, scene_gen_image_btns[i]],
            api_name=f"scene_{i+1}_generate_image",
        )

    # Individual scene VIDEO generation buttons (with resource lock)
    # NOTE: Video button should only work when image exists
    for i in range(MAX_SCENES):
        def make_video_handler(idx):
            async def handler(engine_state, proc_state, video_prompt_text, image):
                """
                On-demand video generation for a single scene.
                Requires image to exist first.
                """
                _install_web_log_handler()
                if engine_state is None:
                    yield (
                        _compose_status("세션이 끊어졌습니다. 새로 시작해주세요."),
                        None,
                        False,
                        get_processing_indicator(False),
                        gr.update(value=f"🎬 씬 {idx+1} 영상 생성", interactive=True),
                    )
                    return

                session: WebSession = engine_state

                # Resource lock check
                if proc_state:
                    yield (
                        _compose_status("⏳ 다른 작업이 진행 중입니다. 잠시 기다려주세요."),
                        None,
                        True,
                        get_processing_indicator(True)
                        ,
                        gr.update(value=f"🎬 씬 {idx+1} 영상 생성", interactive=False),
                    )
                    return

                # Check if image exists
                if image is None:
                    yield (
                        _compose_status("🖼️ 이미지가 없습니다. 먼저 [🎨 이미지 생성] 버튼을 눌러주세요."),
                        None,
                        False,
                        get_processing_indicator(False)
                        ,
                        gr.update(value=f"🎬 씬 {idx+1} 영상 생성", interactive=True),
                    )
                    return

                # Get image path
                image_path = None
                if isinstance(image, str):
                    image_path = image
                elif isinstance(image, dict):
                    # Gradio 6.x FileData 형태: {"path": "...", "orig_name": "...", ...}
                    image_path = image.get("path") or image.get("name")
                else:
                    image_path = getattr(image, "name", None) or getattr(image, "path", None)

                logger.info("[WebUI] scene_%s video handler got image=%r -> image_path=%r", idx + 1, image, image_path)
                if not image_path or not Path(str(image_path)).exists():
                    yield (
                        _compose_status("🖼️ 이미지 파일 경로가 올바르지 않습니다. 다시 생성해주세요."),
                        None,
                        False,
                        get_processing_indicator(False),
                        gr.update(value=f"🎬 씬 {idx+1} 영상 생성", interactive=True),
                    )
                    return

                # Lock on (immediate UI update)
                yield (
                    _compose_status(f"🎬 씬 {idx+1} 영상 생성 시작…"),
                    None,
                    True,
                    get_processing_indicator(True),
                    gr.update(value="Generating... (0%)", interactive=False),
                )

                try:
                    # LOCAL_MOTION_LOOP 모드에서는 이 입력을 "무엇이 미세하게 움직일지"로 해석한다.
                    if idx < len(session.project.scene_plans):
                        session.project.scene_plans[idx]["dynamic_action_description"] = str(video_prompt_text or "")

                    # VideoService: [국소 모션 루프 연출] 칸 텍스트를 그대로 사용 (가사 혼입은 scrub)
                    lyric_line = str(session.project.scene_plans[idx].get("lyric_text", "") or "").strip() if idx < len(session.project.scene_plans) else ""
                    motion_prompt = _scrub_lyric_echo(str(video_prompt_text or "").strip(), lyric_line)
                    if not motion_prompt:
                        motion_prompt = _normalize_loop_motion_prompt("")
                    else:
                        motion_prompt = _normalize_loop_motion_prompt(motion_prompt)
                    logger.info("[WebUI] scene_%s motion_prompt=%r", idx + 1, motion_prompt[:220])

                    pct = 0.0
                    msg = "Queued"

                    def report(p: float, m: str) -> None:
                        nonlocal pct, msg
                        pct = float(p)
                        msg = str(m)

                    task = asyncio.create_task(
                        generate_single_scene_video(
                            session,
                            idx,
                            str(image_path),
                            motion_prompt,
                            report_progress=report,
                        )
                    )
                    while not task.done():
                        yield (
                            _compose_status(f"🎬 씬 {idx+1} 영상 생성 중… ({msg})"),
                            None,
                            True,
                            get_processing_indicator(True),
                            gr.update(value=f"Generating... ({int(max(0.0, min(100.0, pct)))}%)", interactive=False),
                        )
                        await asyncio.sleep(1.0)
                    video_path = await task
                    if video_path and Path(video_path).exists():
                        session.project.video_clips[f"scene_{idx}"] = {"path": str(video_path)}
                        while len(session.project.generated_clips) <= idx:
                            session.project.generated_clips.append("")
                        session.project.generated_clips[idx] = str(video_path)

                        yield (
                            _compose_status(f"✅ 씬 {idx+1} 영상 생성 완료!"),
                            str(video_path),
                            False,
                            get_processing_indicator(False),
                            gr.update(value=f"🎬 씬 {idx+1} 영상 생성", interactive=True),
                        )
                    else:
                        yield (
                            _compose_status("⚠️ 영상 파일을 찾을 수 없습니다."),
                            None,
                            False,
                            get_processing_indicator(False),
                            gr.update(value=f"🎬 씬 {idx+1} 영상 생성", interactive=True),
                        )
                except Exception as e:
                    logger.exception("[WebUI] scene_%s video generation failed", idx + 1)
                    yield (
                        _compose_status(
                            f"⚠️ 영상 생성 실패: {e}\n\n"
                            "콘솔 로그에서 [VideoService] 줄을 확인해주세요."
                        ),
                        None,
                        False,
                        get_processing_indicator(False),
                        gr.update(value=f"🎬 씬 {idx+1} 영상 생성", interactive=True),
                    )
            return handler

        scene_gen_video_btns[i].click(
            fn=make_video_handler(i),
            inputs=[engine_state, is_processing, scene_video_prompt_inputs[i], scene_image_outputs[i]],
            outputs=[status_output, scene_video_outputs[i], is_processing, processing_indicator, scene_gen_video_btns[i]],
            api_name=f"scene_{i+1}_generate_video",
        )

    # =========================================================================
    # Final Video Assembly (with resource lock)
    # =========================================================================

    async def finalize_with_lock(engine_state, proc_state):
        """Wrapper for finalize_video with resource lock and indicator update."""
        _install_web_log_handler()
        if engine_state is None:
            yield (
                _compose_status("세션이 끊어졌습니다. 새로 시작해주세요."),
                None,
                False,
                get_processing_indicator(False),
                gr.update(value="🎉 최종 영상 합치기 (모든 클립 완성 후)", interactive=True),
            )
            return

        if proc_state:
            yield (
                _compose_status("⏳ 다른 작업이 진행 중입니다. 잠시 기다려주세요."),
                None,
                True,
                get_processing_indicator(True)
                ,
                gr.update(value="🎉 최종 영상 합치기 (모든 클립 완성 후)", interactive=False),
            )
            return

        session: WebSession = engine_state
        yield (
            _compose_status("🎉 최종 영상 합치기 시작…"),
            None,
            True,
            get_processing_indicator(True),
            gr.update(value="Generating... (0%)", interactive=False),
        )

        try:
            task = asyncio.create_task(finalize_video(session))
            while not task.done():
                yield (
                    _compose_status("🎉 최종 영상 합치는 중…"),
                    None,
                    True,
                    get_processing_indicator(True),
                    gr.update(value="Generating... (0%)", interactive=False),
                )
                await asyncio.sleep(1.0)
            path = await task
            yield (
                _compose_status("✅ 최종 영상이 완성되었어요!"),
                str(path),
                False,
                get_processing_indicator(False),
                gr.update(value="🎉 최종 영상 합치기 (모든 클립 완성 후)", interactive=True),
            )
        except Exception as e:
            yield (
                _compose_status(f"⚠️ 최종 합치기 실패: {e}"),
                None,
                False,
                get_processing_indicator(False),
                gr.update(value="🎉 최종 영상 합치기 (모든 클립 완성 후)", interactive=True),
            )

    btn_finalize.click(
        fn=finalize_with_lock,
        inputs=[engine_state, is_processing],
        outputs=[status_output, video_output, is_processing, processing_indicator, btn_finalize],
        api_name="finalize_video",
    )

    # =========================================================================
    # Final Export Monitor (poll outputs/final/)
    # =========================================================================

    # Gradio에는 gr.Update 타입이 없어서(=gr.update dict 반환),
    # 타입힌트로 gr.Update를 쓰면 이벤트 등록 시점에 크래시가 난다.
    def _scan_final_outputs() -> Tuple[Any, Any]:
        out_dir = get_output_directories(_load_settings())["final"]
        files = sorted(out_dir.glob("*.mp4"), key=lambda p: p.stat().st_mtime, reverse=True)
        choices = [str(p.resolve()) for p in files]
        latest = choices[0] if choices else None
        return gr.update(choices=choices, value=latest), latest

    def _pick_final_file(path: Optional[str]) -> Any:
        p = str(path or "").strip()
        if p and Path(p).exists():
            return p
        return None

    final_timer.tick(fn=_scan_final_outputs, outputs=[final_files, video_output])
    final_files.change(fn=_pick_final_file, inputs=[final_files], outputs=[video_output])


if __name__ == "__main__":
    # 실행 옵션 (환경변수로 제어 가능)
    # - MELLOW_WEBUI_SHARE=0/1
    # - MELLOW_WEBUI_INBROWSER=0/1
    # - MELLOW_WEBUI_HOST=0.0.0.0 (기본)
    # - MELLOW_WEBUI_PORT=7860 (기본)
    def _env_bool(name: str, default: bool) -> bool:
        v = (os.getenv(name, "") or "").strip().lower()
        if not v:
            return default
        return v not in {"0", "false", "no", "off"}

    # (중요) share(공개 링크)는 네트워크/HTTP2 환경에 따라
    # ERR_HTTP2_PROTOCOL_ERROR / 404 (file fetch) 문제를 유발할 수 있다.
    # 기본은 로컬 실행으로 두고, 필요할 때만 MELLOW_WEBUI_SHARE=1로 켜게 한다.
    share = _env_bool("MELLOW_WEBUI_SHARE", False)
    inbrowser = _env_bool("MELLOW_WEBUI_INBROWSER", True)
    # 기본은 127.0.0.1 (로컬 안정)
    host = (os.getenv("MELLOW_WEBUI_HOST", "127.0.0.1") or "127.0.0.1").strip()
    try:
        port = int((os.getenv("MELLOW_WEBUI_PORT", "7860") or "7860").strip())
    except Exception:
        port = 7860

    demo.queue().launch(
        inbrowser=inbrowser,
        share=share,
        server_name=host,
        server_port=port,
        theme=gr.themes.Soft(),
        css=custom_css,
        # (중요) 로컬 파일(이미지/클립/최종 mp4) 경로를 UI에 표시할 때
        # Gradio가 파일을 서빙할 수 있도록 허용 경로를 명시한다.
        allowed_paths=[str(get_output_directories(_load_settings())["root"])],
    )
