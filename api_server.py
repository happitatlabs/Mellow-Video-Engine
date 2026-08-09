from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
import uuid
import socket
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

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

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

import mellow_link
from mellow_link.services.runtime_config import (
    get_comfyui_endpoint,
    get_motion_video_spike_settings,
    get_output_directories,
    get_video_generation_settings,
    load_settings as load_runtime_settings,
)
from mellow_link.services.output_provenance import ensure_sidecar, write_sidecar_best_effort
from mellow_link.services.runtime_readiness import assert_media_generation_ready, get_runtime_readiness

_MELLOW_LINK_FILE = Path(getattr(mellow_link, "__file__", "")).absolute()
if _REPO_ROOT not in _MELLOW_LINK_FILE.parents:
    raise RuntimeError(
        f"Imported mellow_link from unexpected location: {_MELLOW_LINK_FILE}. "
        f"Expected package under {_REPO_ROOT}. Run from D:/Mellow-Video-Engine using the local .venv."
    )


def _load_settings() -> Dict[str, Any]:
    try:
        return load_runtime_settings()
    except Exception:
        return {}


def _planner_runtime_info() -> Dict[str, Any]:
    return {
        "planner_version": "visual_planner_runtime_v2",
        "policy_enforcement": "config_prompts_yaml + sanitizer + fail_safe",
    }


def _normalize_api_motion_prompt(prompt_text: str) -> str:
    text = str(prompt_text or "").strip()
    if not text:
        return "steam rises continuously from the cup, window light flickers softly, haze drifts in the back, curtain or foliage edges move gently, fixed camera, visible local motion"
    lowered = text.lower()
    if any(token in lowered for token in ("pan", "tilt", "zoom", "push", "pull", "dolly", "truck", "orbit", "handheld", "whip", "shake")):
        return "steam rises continuously from the cup, window light flickers softly, haze drifts in the back, curtain or foliage edges move gently, fixed camera, visible local motion"
    for suffix in ("seamless loop", "continuous steam or smoke motion", "light pulse", "haze drift", "localized shimmer", "visible local motion", "fixed camera"):
        if suffix not in lowered:
            text = f"{text}, {suffix}"
            lowered = text.lower()
    return text


def _request_provenance_for_session(s: "Session", *, artifact_type: str, scene_index: Optional[int] = None) -> Dict[str, Any]:
    plan = {}
    if scene_index is not None and 0 <= int(scene_index) - 1 < len(s.scene_plans):
        plan = s.scene_plans[int(scene_index) - 1] or {}
    policy_validation = dict(plan.get("policy_validation") or {})
    policy_flags = dict(plan.get("policy_flags") or {})
    semantic_scene = dict(plan.get("semantic_scene") or {})
    policy_inputs = dict(plan.get("policy_inputs") or {})
    policy_outputs = dict(plan.get("policy_outputs") or {})
    video_runtime = get_video_generation_settings(_load_settings())
    return {
        "source": {
            "session_id": s.id,
            "audio_path": s.audio_path,
            "scene_index": scene_index,
            "artifact_type": artifact_type,
        },
        "runtime": _planner_runtime_info(),
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


def _ffmpeg_path(settings: Dict[str, Any]) -> str:
    cfg = (settings or {}).get("ffmpeg", {}) if isinstance(settings, dict) else {}
    exe = str(cfg.get("path") or "ffmpeg")
    found = shutil.which(exe) if exe else None
    if found:
        return found
    # allow absolute path in config
    if exe and Path(exe).exists():
        return exe
    raise RuntimeError("ffmpeg not found. Set ffmpeg.path in config/settings.yaml or add ffmpeg to PATH.")


def _ffprobe_path(settings: Dict[str, Any]) -> Optional[str]:
    cfg = (settings or {}).get("ffmpeg", {}) if isinstance(settings, dict) else {}
    # if ffmpeg.path is a full folder, user can also set ffprobe.path but we keep simple
    exe = "ffprobe"
    found = shutil.which(exe)
    return found


def _segments_to_timestamped_text(segments: List[Dict[str, Any]]) -> str:
    lines: List[str] = []
    for seg in segments:
        start = float(seg.get("start_time", 0.0) or 0.0)
        end = float(seg.get("end_time", 0.0) or 0.0)
        text = str(seg.get("text", "") or "").strip()
        if not text:
            continue
        lines.append(f"[{start:.2f} - {end:.2f}] {text}")
    return "\n".join(lines).strip()


def _split_lyrics_lines(text: str) -> List[str]:
    return [ln.strip() for ln in (text or "").splitlines() if ln.strip()]


def _align_pasted_lyrics_to_segments(pasted: str, segments: List[Dict[str, Any]], duration: float = 0.0) -> str:
    lyric_lines = _split_lyrics_lines(pasted)
    if not lyric_lines:
        return ""
    if not segments:
        return "\n".join([f"[0.00 - 0.00] {ln}" for ln in lyric_lines])

    # simple proportional grouping by text length (robust and deterministic)
    seg_lens = [max(1, len(str(s.get("text", "") or "").strip())) for s in segments]
    total_seg = float(sum(seg_lens))
    weights = [max(1, len(ln)) for ln in lyric_lines]
    total_w = float(sum(weights))

    out: List[str] = []
    seg_i = 0
    acc = 0.0
    target = 0.0
    for li, ln in enumerate(lyric_lines):
        remain_lines = len(lyric_lines) - li
        remain_segs = len(segments) - seg_i
        must_leave = max(0, remain_lines - 1)
        max_take = max(1, remain_segs - must_leave)
        target += (weights[li] / total_w) * total_seg

        start = float(segments[seg_i].get("start_time", 0.0) or 0.0)
        end = float(segments[seg_i].get("end_time", 0.0) or 0.0)
        take = 0
        while seg_i < len(segments) and take < max_take:
            end = float(segments[seg_i].get("end_time", end) or end)
            acc += float(seg_lens[seg_i])
            seg_i += 1
            take += 1
            if acc >= target:
                break
        out.append(f"[{start:.2f} - {end:.2f}] {ln}")
        if seg_i >= len(segments):
            for rest in lyric_lines[li + 1 :]:
                out.append(f"[{end:.2f} - {end:.2f}] {rest}")
            break
    return "\n".join(out).strip()


async def _run_transcribe_worker(audio_path: Path, *, model: str, device: str) -> Dict[str, Any]:
    """
    (CRITICAL) 음성인식은 별도 프로세스로 실행한다.
    - worker가 크래시해도 FastAPI 서버 프로세스는 살아야 한다.
    """
    out_dir = get_output_directories()["transcripts"]
    out_json = out_dir / f"{audio_path.stem}_{uuid.uuid4().hex[:8]}.json"

    cmd = [
        sys.executable,
        "-u",
        "-m",
        "backend.transcribe_worker",
        "--audio",
        str(audio_path),
        "--out",
        str(out_json),
        "--model",
        model,
        "--device",
        device,
    ]

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(Path(".").resolve()),
    )
    # consume outputs (keep tail)
    tail: List[str] = []
    if proc.stdout:
        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            s = line.decode("utf-8", "ignore").rstrip()
            tail.append(s)
            tail = tail[-50:]
    stderr = b""
    if proc.stderr:
        stderr = await proc.stderr.read()
    rc = await proc.wait()

    if rc != 0:
        raise RuntimeError(
            f"transcribe worker failed (code={rc}).\n"
            f"stdout_tail:\n" + "\n".join(tail[-20:]) + "\n"
            f"stderr_tail:\n" + stderr.decode("utf-8", "ignore")[-2000:]
        )

    if not out_json.exists():
        raise RuntimeError("transcribe worker finished but output JSON missing")

    return json.loads(out_json.read_text(encoding="utf-8"))


@dataclass
class Session:
    id: str
    audio_path: str
    lyrics_text: str = ""
    raw_lyrics: str = ""
    segments: List[Dict[str, Any]] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    scene_plans: List[Dict[str, Any]] = field(default_factory=list)
    generated_images: Dict[int, str] = field(default_factory=dict)
    generated_clips: Dict[int, str] = field(default_factory=dict)


SESSIONS: Dict[str, Session] = {}

app = FastAPI(title="Mellow API Server", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# serve unified outputs for local playback
_OUTPUT_DIRS = get_output_directories()
app.mount("/outputs", StaticFiles(directory=str(_OUTPUT_DIRS["root"])), name="outputs")
app.mount("/output", StaticFiles(directory=str(_OUTPUT_DIRS["root"])), name="output_deprecated")


@app.get("/", response_class=HTMLResponse)
def home() -> str:
    # Minimal local-only UI (큰 글씨). Gradio 없이 동작.
    return """
<!doctype html>
<html lang="ko">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Mellow Local Server</title>
    <style>
      body { font-family: system-ui, sans-serif; font-size: 18px; padding: 20px; }
      h1 { font-size: 28px; margin: 0 0 12px; }
      textarea { width: 100%; min-height: 160px; font-size: 18px; }
      input, button { font-size: 18px; padding: 10px; }
      .row { margin: 10px 0; }
      pre { background: #111; color: #eee; padding: 12px; overflow: auto; }
    </style>
  </head>
  <body>
    <h1>🎬 Mellow Local Server (Gradio 없이)</h1>
    <div class="row">
      <label>오디오 파일: <input id="audio" type="file" accept="audio/*" /></label>
    </div>
    <div class="row">
      <label>전체 가사(선택, 타임스탬프 없어도 됨):</label>
      <textarea id="lyrics" placeholder="여기에 가사를 붙여넣으세요"></textarea>
    </div>
    <div class="row">
      <button id="btn">타임라인 가사 생성/정렬</button>
    </div>
    <div class="row">
      <label>결과:</label>
      <textarea id="out" readonly></textarea>
    </div>
    <div class="row">
      <pre id="log"></pre>
    </div>
    <script>
      const log = (s) => { document.getElementById('log').textContent += s + "\\n"; };
      document.getElementById('btn').onclick = async () => {
        const f = document.getElementById('audio').files[0];
        const lyrics = document.getElementById('lyrics').value;
        if (!f) { alert('오디오 파일을 선택하세요'); return; }
        const fd = new FormData();
        fd.append('audio', f);
        fd.append('lyrics', lyrics);
        log('요청 중...');
        const r = await fetch('/api/lyrics', { method: 'POST', body: fd });
        const j = await r.json();
        if (!r.ok) { log('ERROR: ' + JSON.stringify(j)); alert('실패'); return; }
        document.getElementById('out').value = j.lyrics_text || '';
        log('OK. session=' + j.session_id);
        // 씬별 작업대로 이동
        window.location.href = '/workspace?session_id=' + encodeURIComponent(j.session_id);
      };
    </script>
  </body>
</html>
"""


@app.get("/api/health")
async def health() -> Dict[str, Any]:
    return {"ok": True, "runtime": await get_runtime_readiness(_load_settings())}


@app.post("/api/lyrics")
async def make_lyrics(
    audio: UploadFile = File(...),
    lyrics: str = Form(""),
    model: str = Form("large-v3"),
    device: str = Form("cpu"),
) -> JSONResponse:
    """
    - 오디오 업로드
    - backend/audio_engine 기반 타임라인 세그먼트 생성(서브프로세스)
    - 붙여넣은 가사가 있으면 타임라인에 맞춰 정렬
    """
    settings = _load_settings()

    uploads = get_output_directories()["uploads"]
    uid = uuid.uuid4().hex[:10]
    safe_name = Path(audio.filename or f"audio_{uid}").name
    audio_path = uploads / f"{uid}_{safe_name}"
    audio_bytes = await audio.read()
    audio_path.write_bytes(audio_bytes)

    try:
        data = await _run_transcribe_worker(audio_path, model=model, device=device)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    segments = data.get("segments") or []
    duration = float(data.get("duration", 0.0) or 0.0)

    pasted = (lyrics or "").strip()
    if pasted:
        lyrics_text = _align_pasted_lyrics_to_segments(pasted, segments, duration=duration)
    else:
        lyrics_text = _segments_to_timestamped_text(segments)

    sess_id = uuid.uuid4().hex[:12]
    SESSIONS[sess_id] = Session(
        id=sess_id,
        audio_path=str(audio_path),
        lyrics_text=lyrics_text,
        raw_lyrics=pasted,
        segments=segments,
        metadata={},
    )

    return JSONResponse(
        {
            "session_id": sess_id,
            "audio_path": str(audio_path),
            "duration": duration,
            "lyrics_text": lyrics_text,
            "segments": segments,
        }
    )


def _to_served_url(p: Path) -> str:
    p = p.resolve()
    out_root = get_output_directories()["root"]
    try:
        if p.is_relative_to(out_root):
            return "/outputs/" + p.relative_to(out_root).as_posix()
    except Exception:
        pass
    # fallback: return absolute (may not be playable in browser)
    return str(p)


@app.get("/api/ffmpeg_status")
def ffmpeg_status() -> JSONResponse:
    settings = _load_settings()
    try:
        p = _ffmpeg_path(settings)
        return JSONResponse({"ok": True, "path": p})
    except Exception as e:
        return JSONResponse({"ok": False, "message": str(e)})


@app.post("/api/session/{session_id}/lyrics/set")
def set_raw_lyrics(session_id: str, body: Dict[str, Any]) -> JSONResponse:
    s = SESSIONS.get(session_id)
    if not s:
        raise HTTPException(status_code=404, detail="session not found")
    raw = body.get("raw_lyrics", "")
    if raw is None:
        raw = ""
    if not isinstance(raw, str):
        raise HTTPException(status_code=400, detail="raw_lyrics must be a string")
    s.raw_lyrics = raw
    return JSONResponse({"ok": True})


@app.post("/api/session/{session_id}/lyrics/realign")
async def realign_lyrics(session_id: str) -> JSONResponse:
    """
    전체 가사 타임라인 정렬:
    - 오디오에서 segments를 재추출(서브프로세스)
    - raw_lyrics가 있으면 타임라인에 맞춰 정렬
    - 없으면 타임라인 가사 자동 생성
    """
    s = SESSIONS.get(session_id)
    if not s:
        raise HTTPException(status_code=404, detail="session not found")
    audio_path = Path(s.audio_path)
    if not audio_path.exists():
        raise HTTPException(status_code=400, detail="audio file missing for this session")
    try:
        data = await _run_transcribe_worker(audio_path, model="large-v3", device="cpu")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    segments = data.get("segments") or []
    duration = float(data.get("duration", 0.0) or 0.0)
    s.segments = segments
    if s.raw_lyrics.strip():
        s.lyrics_text = _align_pasted_lyrics_to_segments(s.raw_lyrics, segments, duration=duration)
    else:
        s.lyrics_text = _segments_to_timestamped_text(segments)
    # scenes need re-plan after timeline changes
    s.scene_plans = []
    return JSONResponse({"ok": True, "lyrics_text": s.lyrics_text, "segments": segments})


def _get_comfyui_host_port(settings: Dict[str, Any]) -> Tuple[str, int]:
    endpoint = get_comfyui_endpoint(settings)
    return str(endpoint["host"]), int(endpoint["port"])


_GEN_LOCK = asyncio.Lock()
_SVC: Dict[str, Any] = {"image": None, "video": None, "connected": False}


async def _ensure_services() -> Tuple[Any, Any]:
    """
    mellow_link ImageService/VideoService 연결(지연 생성).
    """
    if _SVC.get("connected") and _SVC.get("image") and _SVC.get("video"):
        return _SVC["image"], _SVC["video"]

    settings = _load_settings()
    await assert_media_generation_ready(settings)
    host, port = _get_comfyui_host_port(settings)
    endpoint = get_comfyui_endpoint(settings)
    output_dirs = get_output_directories(settings)

    from mellow_link.services.image_service import ImageService  # type: ignore
    from mellow_link.services.video_service import VideoService  # type: ignore

    img_dir = output_dirs["images"]
    vid_dir = output_dirs["videos"]

    img = ImageService(host=host, port=port, timeout=float(endpoint["timeout"]), output_dir=img_dir)
    vid = VideoService(host=host, port=port, timeout=float(endpoint["timeout"]), output_dir=vid_dir)
    await img.connect()
    await vid.connect()

    _SVC["image"] = img
    _SVC["video"] = vid
    _SVC["connected"] = True
    return img, vid


@app.get("/workspace", response_class=HTMLResponse)
def workspace(session_id: str) -> str:
    if session_id not in SESSIONS:
        return "<h2>세션을 찾을 수 없습니다.</h2>"
    # Mellow-Deck: FastAPI 기반 초경량 씬별 작업대 (Gradio 없이)
    return f"""
<!doctype html>
<html lang="ko">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Mellow-Deck (씬별 작업대)</title>
    <style>
      /* Aventurine-style (dark, modern). No external CSS. */
      :root {{
        --bg: #071012;
        --panel: rgba(255,255,255,.03);
        --panel2: rgba(0,0,0,.22);
        --border: rgba(255,255,255,.10);
        --text: rgba(255,255,255,.92);
        --muted: rgba(255,255,255,.62);
        --accent: #33d6c7;
        --accent2: #1ea79b;
        --danger: #ff5a79;
        --warn: #ffcc66;
      }}

      * {{ box-sizing: border-box; }}
      html, body {{ height: 100%; }}
      body {{
        margin: 0;
        font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif;
        color: var(--text);
        background:
          radial-gradient(1200px 800px at 18% 10%, rgba(51,214,199,.14), transparent 60%),
          radial-gradient(900px 650px at 88% 25%, rgba(30,167,155,.10), transparent 55%),
          var(--bg);
      }}

      header {{
        position: sticky;
        top: 0;
        z-index: 10;
        background: rgba(7,16,18,.82);
        backdrop-filter: blur(10px);
        border-bottom: 1px solid var(--border);
        padding: 12px 14px;
        display: flex;
        align-items: center;
        gap: 12px;
      }}
      header .title {{ font-weight: 900; letter-spacing: .3px; }}
      header .pill {{
        padding: 4px 10px;
        border: 1px solid var(--border);
        border-radius: 999px;
        color: var(--muted);
        font-size: 12px;
      }}
      header .spacer {{ flex: 1; }}
      .top-actions {{ display: flex; gap: 10px; flex-wrap: wrap; }}

      button {{
        border-radius: 12px;
        border: 1px solid rgba(51,214,199,.35);
        background: linear-gradient(180deg, rgba(51,214,199,.18), rgba(51,214,199,.10));
        color: var(--text);
        padding: 10px 12px;
        cursor: pointer;
        font-size: 14px;
      }}
      button.secondary {{
        background: rgba(255,255,255,.04);
        border: 1px solid var(--border);
      }}
      button.danger {{
        background: rgba(255,90,121,.12);
        border: 1px solid rgba(255,90,121,.35);
      }}
      button:disabled {{ opacity: .55; cursor: not-allowed; }}

      .layout {{
        display: grid;
        grid-template-columns: 280px 1fr;
        min-height: calc(100vh - 58px);
      }}
      .sidebar {{
        border-right: 1px solid var(--border);
        background: linear-gradient(180deg, rgba(255,255,255,.03), rgba(255,255,255,.01));
        padding: 14px;
      }}
      .sidebar h2 {{
        margin: 0 0 10px;
        font-size: 13px;
        color: var(--muted);
        font-weight: 800;
        letter-spacing: .4px;
      }}
      .scene-list {{ display: flex; flex-direction: column; gap: 8px; }}
      .scene-item {{
        border: 1px solid var(--border);
        background: rgba(255,255,255,.03);
        padding: 10px 10px;
        border-radius: 14px;
        cursor: pointer;
      }}
      .scene-item.active {{
        border-color: rgba(51,214,199,.55);
        background: rgba(51,214,199,.10);
      }}
      .scene-item .row {{ display:flex; align-items:center; justify-content:space-between; gap:8px; }}
      .badge {{
        font-size: 11px;
        padding: 3px 8px;
        border-radius: 999px;
        border: 1px solid var(--border);
        color: var(--muted);
      }}
      .badge.ok {{ border-color: rgba(51,214,199,.45); color: rgba(51,214,199,.95); }}
      .badge.warn {{ border-color: rgba(255,204,102,.45); color: rgba(255,204,102,.95); }}
      .kpi {{ margin-top: 10px; display:flex; gap:8px; flex-wrap:wrap; }}

      .main {{ padding: 14px; }}
      .panel {{
        border: 1px solid var(--border);
        background: linear-gradient(180deg, rgba(255,255,255,.03), rgba(255,255,255,.01));
        border-radius: 18px;
        padding: 14px;
      }}
      .grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }}
      .field label {{
        display: block;
        font-size: 12px;
        color: var(--muted);
        margin: 8px 0 6px;
      }}
      input, textarea {{
        width: 100%;
        padding: 10px 12px;
        border-radius: 12px;
        border: 1px solid var(--border);
        background: rgba(0,0,0,.20);
        color: var(--text);
        font-size: 14px;
      }}
      textarea {{ min-height: 92px; resize: vertical; }}
      .preview {{
        border: 1px solid var(--border);
        background: rgba(0,0,0,.22);
        border-radius: 14px;
        padding: 10px;
        margin-top: 8px;
      }}
      img, video {{
        width: 100%;
        border-radius: 12px;
        border: 1px solid var(--border);
        background: rgba(0,0,0,.25);
      }}
      .muted {{ color: var(--muted); }}
      .status {{
        margin-top: 10px;
        padding: 10px 12px;
        border-radius: 12px;
        border: 1px solid var(--border);
        background: rgba(0,0,0,.18);
        font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
        font-size: 12px;
        white-space: pre-wrap;
        min-height: 74px;
      }}
    </style>
  </head>
  <body>
    <header>
      <div class="title">Mellow-Deck</div>
      <div class="pill">session: <b>{session_id}</b></div>
      <div class="spacer"></div>
      <div class="top-actions">
        <button class="secondary" id="btn-align">전체 가사 타임라인 정렬</button>
        <button class="danger" id="btn-merge">최종 영상 합치기</button>
        <button class="secondary" id="btn-plan">씬 생성</button>
        <button class="secondary" id="btn-refresh">새로고침</button>
      </div>
    </header>

    <div class="layout">
      <aside class="sidebar">
        <h2>Scene Navigator</h2>
        <div class="scene-list" id="sceneList"></div>
        <div class="kpi" id="kpi"></div>
      </aside>

      <main class="main">
        <div class="panel">
          <div class="field">
            <label>전체 가사 원문(선택) — 타임라인 정렬 기준</label>
            <textarea id="rawLyrics" placeholder="여기에 전체 가사를 붙여넣고 [전체 가사 타임라인 정렬]을 누르세요"></textarea>
          </div>

          <div class="grid" style="margin-top: 10px;">
            <div class="panel" style="background: rgba(255,255,255,.02);">
              <div><b>가사/타임라인</b> <span class="muted">씬 단위 수정</span></div>
              <div class="field"><label>가사 텍스트</label><textarea id="lyricText"></textarea></div>
              <div class="grid">
                <div class="field"><label>시작(s)</label><input id="startTime" type="number" step="0.01" /></div>
                <div class="field"><label>종료(s)</label><input id="endTime" type="number" step="0.01" /></div>
              </div>
              <button class="secondary" id="btn-save">씬 정보 저장</button>
            </div>
            <div class="panel" style="background: rgba(255,255,255,.02);">
              <div><b>규격</b> <span class="muted">고정</span></div>
              <div class="muted">이미지/영상 생성은 항상 <b>1216×704</b>로 강제됩니다.</div>
              <div class="muted" style="margin-top:10px;">ComfyUI 통신은 서버에서 직통 수행 (Gradio 없음)</div>
            </div>
          </div>

          <div class="grid" style="margin-top: 12px;">
            <div class="panel" style="background: rgba(255,255,255,.02);">
              <div><b>이미지 생성</b> <span class="muted">/api/image</span></div>
              <div class="field"><label>static_scene_description</label><textarea id="staticDesc"></textarea></div>
              <button id="btn-image">이미지 굽기</button>
              <div class="preview"><img id="imagePreview" src="" alt="image preview"></div>
            </div>
            <div class="panel" style="background: rgba(255,255,255,.02);">
              <div><b>영상 생성</b> <span class="muted">/api/video</span></div>
              <div class="field"><label>local_motion_loop_direction</label><textarea id="motionPrompt" placeholder="예: 커피잔의 김이 계속 올라오고, 창빛이 은은하게 흔들리며, 커튼 끝만 살짝 움직임"></textarea></div>
              <button id="btn-video">영상 굽기</button>
              <div class="preview"><video id="videoPreview" src="" controls></video></div>
            </div>
          </div>

          <div class="status" id="status"></div>
        </div>
      </main>
    </div>

    <script>
      const sid = {json.dumps(session_id)};
      const statusEl = document.getElementById('status');
      const sceneListEl = document.getElementById('sceneList');
      const kpiEl = document.getElementById('kpi');
      const rawLyricsEl = document.getElementById('rawLyrics');
      const lyricTextEl = document.getElementById('lyricText');
      const startTimeEl = document.getElementById('startTime');
      const endTimeEl = document.getElementById('endTime');
      const staticDescEl = document.getElementById('staticDesc');
      const motionPromptEl = document.getElementById('motionPrompt');
      const imgPrev = document.getElementById('imagePreview');
      const vidPrev = document.getElementById('videoPreview');

      let scenes = [];
      let activeIdx = 1;

      const log = (s) => {{
        const next = (statusEl.textContent + (statusEl.textContent ? "\\n" : "") + s);
        statusEl.textContent = next.slice(-6000);
      }};

      const api = async (url, opts={{}}) => {{
        const r = await fetch(url, opts);
        const j = await r.json().catch(()=> ({{detail: 'invalid json'}}));
        if (!r.ok) throw new Error((j.detail || j.message || JSON.stringify(j)));
        return j;
      }};

      function badgeFor(scene) {{
        if (scene.video_url) return '<span class="badge ok">VIDEO</span>';
        if (scene.image_url) return '<span class="badge warn">IMAGE</span>';
        return '<span class="badge">EMPTY</span>';
      }}

      function renderSidebar() {{
        sceneListEl.innerHTML = '';
        for (const s of scenes) {{
          const idx = s.scene_index || 0;
          const item = document.createElement('div');
          item.className = 'scene-item' + (idx === activeIdx ? ' active' : '');
          const t = (s.lyric_text || '').trim();
          const subtitle = t ? (t.slice(0, 26) + (t.length > 26 ? '…' : '')) : '(가사 없음)';
          item.innerHTML = `
            <div class="row">
              <div><b>Scene ${'{'}idx{'}'}</b></div>
              ${'{'}badgeFor(s){'}'}
            </div>
            <div class="muted" style="margin-top:6px;">${'{'}subtitle{'}'}</div>
            <div class="muted" style="margin-top:6px;">[${'{'}(s.start_time||0).toFixed(2){'}'} - ${'{'}(s.end_time||0).toFixed(2){'}'}]</div>
          `;
          item.onclick = () => {{ activeIdx = idx; renderWorkspace(); renderSidebar(); }};
          sceneListEl.appendChild(item);
        }}
        const imgCount = scenes.filter(s => !!s.image_url).length;
        const vidCount = scenes.filter(s => !!s.video_url).length;
        kpiEl.innerHTML = `
          <span class="badge">Scenes: ${'{'}scenes.length{'}'}</span>
          <span class="badge">Images: ${'{'}imgCount{'}'}</span>
          <span class="badge">Videos: ${'{'}vidCount{'}'}</span>
        `;
      }}

      function renderWorkspace() {{
        const s = scenes.find(x => (x.scene_index||0) === activeIdx) || scenes[0];
        if (!s) return;
        lyricTextEl.value = s.lyric_text || '';
        startTimeEl.value = (s.start_time || 0);
        endTimeEl.value = (s.end_time || 0);
        staticDescEl.value = s.static_scene_description || '';
        motionPromptEl.value = s.motion_prompt || '';
        imgPrev.src = s.image_url || '';
        vidPrev.src = s.video_url || '';
      }}

      async function refresh() {{
        const data = await api('/api/session/' + sid + '/scenes');
        scenes = data.scenes || [];
        if (!scenes.length) {{
          await api('/api/session/' + sid + '/scenes/plan', {{ method: 'POST' }});
          const data2 = await api('/api/session/' + sid + '/scenes');
          scenes = data2.scenes || [];
        }}
        if (!activeIdx && scenes.length) activeIdx = scenes[0].scene_index || 1;
        renderSidebar();
        renderWorkspace();
      }}

      async function plan() {{
        log('planning scenes...');
        await api('/api/session/' + sid + '/scenes/plan', {{ method: 'POST' }});
        await refresh();
        log('plan OK');
      }}

      async function saveScene() {{
        await api('/api/session/' + sid + '/scene/' + activeIdx + '/update', {{
          method: 'POST',
          headers: {{ 'content-type': 'application/json' }},
          body: JSON.stringify({{
            lyric_text: lyricTextEl.value,
            start_time: parseFloat(startTimeEl.value || '0'),
            end_time: parseFloat(endTimeEl.value || '0'),
            static_scene_description: staticDescEl.value,
            motion_prompt: motionPromptEl.value,
          }})
        }});
        log('scene saved');
        await refresh();
      }}

      async function bakeImage() {{
        const btn = document.getElementById('btn-image');
        btn.disabled = true;
        try {{
          log('image baking...');
          const j = await api('/api/image', {{
            method: 'POST',
            headers: {{ 'content-type': 'application/json' }},
            body: JSON.stringify({{
              session_id: sid,
              scene_index: activeIdx,
              static_scene_description: staticDescEl.value,
            }})
          }});
          log('image OK: ' + (j.path || ''));
          await refresh();
        }} catch(e) {{
          log('ERR image: ' + e.message);
          alert(e.message);
        }} finally {{
          btn.disabled = false;
        }}
      }}

      async function bakeVideo() {{
        const btn = document.getElementById('btn-video');
        btn.disabled = true;
        try {{
          log('video baking...');
          const j = await api('/api/video', {{
            method: 'POST',
            headers: {{ 'content-type': 'application/json' }},
            body: JSON.stringify({{
              session_id: sid,
              scene_index: activeIdx,
              motion_prompt: motionPromptEl.value,
            }})
          }});
          log('video OK: ' + (j.path || ''));
          await refresh();
        }} catch(e) {{
          log('ERR video: ' + e.message);
          alert(e.message);
        }} finally {{
          btn.disabled = false;
        }}
      }}

      async function alignAll() {{
        await api('/api/session/' + sid + '/lyrics/set', {{
          method: 'POST',
          headers: {{ 'content-type': 'application/json' }},
          body: JSON.stringify({{ raw_lyrics: rawLyricsEl.value || '' }})
        }});
        log('realigning timeline...');
        await api('/api/session/' + sid + '/lyrics/realign', {{ method: 'POST' }});
        log('realign OK (replan)');
        await plan();
      }}

      async function mergeFinal() {{
        const st = await api('/api/ffmpeg_status');
        if (!st.ok) {{
          alert('FFmpeg가 필요합니다.\\n\\n' + (st.message || ''));
          log('ffmpeg missing');
          return;
        }}
        log('merging final...');
        const j = await api('/api/session/' + sid + '/merge', {{ method: 'POST' }});
        log('final OK: ' + (j.output || ''));
        alert('완료: ' + (j.output || ''));
      }}

      document.getElementById('btn-refresh').onclick = refresh;
      document.getElementById('btn-plan').onclick = plan;
      document.getElementById('btn-save').onclick = saveScene;
      document.getElementById('btn-image').onclick = bakeImage;
      document.getElementById('btn-video').onclick = bakeVideo;
      document.getElementById('btn-align').onclick = alignAll;
      document.getElementById('btn-merge').onclick = mergeFinal;

      refresh().catch(e => log('ERR: ' + e.message));
    </script>
  </body>
</html>
"""


@app.get("/api/session/{session_id}/scenes")
def get_scenes(session_id: str) -> JSONResponse:
    s = SESSIONS.get(session_id)
    if not s:
        raise HTTPException(status_code=404, detail="session not found")
    scenes = s.scene_plans or []
    # attach preview urls if exist
    for it in scenes:
        idx = int(it.get("scene_index", 0) or 0)
        ip = s.generated_images.get(idx)
        vp = s.generated_clips.get(idx)
        if ip:
            it["image_path"] = ip
            it["image_url"] = _to_served_url(Path(ip))
        if vp:
            it["video_path"] = vp
            it["video_url"] = _to_served_url(Path(vp))
    return JSONResponse({"session_id": s.id, "scenes": scenes})


@app.post("/api/session/{session_id}/scenes/plan")
async def plan_scenes(session_id: str) -> JSONResponse:
    s = SESSIONS.get(session_id)
    if not s:
        raise HTTPException(status_code=404, detail="session not found")
    if not s.segments:
        raise HTTPException(status_code=400, detail="no segments available; run /api/lyrics first")

    from mellow_link.services.visual_planner import VisualPlanner, PlannerConfig  # type: ignore

    planner = VisualPlanner(config=PlannerConfig(max_scenes=20, width=1216, height=704))
    # heuristic (stable) by default; LLM path is optional and can be slow
    scenes = planner.plan_scenes(lyrics_segments=s.segments, metadata=s.metadata, base_seed=None)
    s.scene_plans = scenes
    return JSONResponse({"ok": True, "count": len(scenes)})


@app.post("/api/session/{session_id}/scene/{scene_index}/image")
async def gen_scene_image(session_id: str, scene_index: int, body: Dict[str, Any]) -> JSONResponse:
    s = SESSIONS.get(session_id)
    if not s:
        raise HTTPException(status_code=404, detail="session not found")
    idx = int(scene_index)
    static_prompt = body.get("static_prompt")
    if not isinstance(static_prompt, str) or not static_prompt.strip():
        raise HTTPException(status_code=400, detail="static_prompt must be a non-empty string")

    # ensure scenes exist
    if not s.scene_plans:
        await plan_scenes(session_id)

    async with _GEN_LOCK:
        try:
            img_svc, _vid_svc = await _ensure_services()
        except Exception as e:
            raise HTTPException(status_code=503, detail=str(e))
        from mellow_link.core.schemas import ImageRequest  # type: ignore

        req = ImageRequest(
            static_prompt=static_prompt.strip(),
            prompt=static_prompt.strip(),
            negative_prompt=str(
                (s.scene_plans[idx - 1].get("negative_prompt", "") if idx - 1 < len(s.scene_plans) else "") or ""
            ).strip(),
            width=1216,
            height=704,
            steps=20,
            cfg_scale=7.0,
            seed=int(s.scene_plans[idx - 1].get("seed", -1)) if idx - 1 < len(s.scene_plans) else -1,
            batch_size=1,
            model=None,
            workflow="flux_dev_api.json",
            sampler_name="euler",
            scheduler="normal",
            denoise=1.0,
            provenance=_request_provenance_for_session(s, artifact_type="image", scene_index=idx),
        )
        path = await img_svc.generate_image(req)
        p = Path(path).resolve()
        s.generated_images[idx] = str(p)
        return JSONResponse({"path": str(p), "url": _to_served_url(p)})


@app.post("/api/session/{session_id}/scene/{scene_index}/video")
async def gen_scene_video(session_id: str, scene_index: int, body: Dict[str, Any]) -> JSONResponse:
    s = SESSIONS.get(session_id)
    if not s:
        raise HTTPException(status_code=404, detail="session not found")
    idx = int(scene_index)
    motion_prompt = body.get("motion_prompt")
    if not isinstance(motion_prompt, str) or not motion_prompt.strip():
        raise HTTPException(status_code=400, detail="motion_prompt must be a non-empty string")

    if not s.scene_plans:
        await plan_scenes(session_id)

    image_path = s.generated_images.get(idx)
    if not image_path:
        raise HTTPException(status_code=400, detail="no image yet; generate image first")

    async with _GEN_LOCK:
        try:
            _img_svc, vid_svc = await _ensure_services()
        except Exception as e:
            raise HTTPException(status_code=503, detail=str(e))
        from mellow_link.core.schemas import VideoRequest  # type: ignore

        plan = s.scene_plans[idx - 1] if idx - 1 < len(s.scene_plans) else {}
        video_runtime = get_video_generation_settings(_load_settings())
        motion_spike = get_motion_video_spike_settings(_load_settings())
        use_h3 = str(video_runtime.get("backend") or "").lower() == "minimax_h3"
        normalized_motion_prompt = _normalize_api_motion_prompt(motion_prompt.strip())
        req = VideoRequest(
            image_path=str(image_path),
            motion_prompt=normalized_motion_prompt,
            prompt=normalized_motion_prompt,
            mode="LOCAL_MOTION_LOOP",
            motion_bucket_id=1,
            workflow=str(video_runtime.get("local_motion_workflow") or motion_spike.get("workflow") or "ltx_2b_v0_9_ckpt_i2v_lowmem.json"),
            width=int(video_runtime.get("local_motion_width", motion_spike.get("width", 576)) or 576),
            height=int(video_runtime.get("local_motion_height", motion_spike.get("height", 320)) or 320),
            target_duration=float(
                video_runtime.get("minimax_h3_duration")
                if use_h3
                else video_runtime.get("local_motion_duration", motion_spike.get("duration_seconds", 2.83))
                or 2.83
            ),
            loop_mode="crossfade",
            overlap_seconds=0.35,
            fps=int(video_runtime.get("local_motion_fps", motion_spike.get("fps", 6)) or 6),
            provenance=_request_provenance_for_session(s, artifact_type="video", scene_index=idx),
        )
        path = await vid_svc.generate_video(req)
        p = Path(path).resolve()
        s.generated_clips[idx] = str(p)
        return JSONResponse({"path": str(p), "url": _to_served_url(p)})


@app.post("/api/session/{session_id}/scene/{scene_index}/update")
def update_scene(session_id: str, scene_index: int, body: Dict[str, Any]) -> JSONResponse:
    s = SESSIONS.get(session_id)
    if not s:
        raise HTTPException(status_code=404, detail="session not found")
    idx = int(scene_index)
    if not s.scene_plans:
        raise HTTPException(status_code=400, detail="no scenes yet; plan scenes first")
    plan = next((x for x in s.scene_plans if int(x.get("scene_index", 0) or 0) == idx), None)
    if not plan:
        raise HTTPException(status_code=404, detail="scene not found")

    if "lyric_text" in body:
        plan["lyric_text"] = str(body.get("lyric_text") or "")
    if "start_time" in body:
        try:
            plan["start_time"] = float(body.get("start_time") or 0.0)
        except Exception:
            pass
    if "end_time" in body:
        try:
            plan["end_time"] = float(body.get("end_time") or 0.0)
        except Exception:
            pass
    if "static_scene_description" in body:
        plan["static_scene_description"] = str(body.get("static_scene_description") or "")
    if "motion_prompt" in body:
        plan["motion_prompt"] = str(body.get("motion_prompt") or "")
    return JSONResponse({"ok": True})


@app.post("/api/image")
async def api_image(body: Dict[str, Any]) -> JSONResponse:
    """
    Mellow-Deck 요구사항: /api/image
    - static_scene_description을 받아 이미지 굽기
    """
    session_id = body.get("session_id")
    scene_index = body.get("scene_index")
    static_desc = body.get("static_scene_description", "")
    if not isinstance(session_id, str) or not session_id:
        raise HTTPException(status_code=400, detail="session_id required")
    try:
        idx = int(scene_index)
    except Exception:
        raise HTTPException(status_code=400, detail="scene_index must be int")
    if not isinstance(static_desc, str):
        raise HTTPException(status_code=400, detail="static_scene_description must be str")
    s = SESSIONS.get(session_id)
    if not s:
        raise HTTPException(status_code=404, detail="session not found")
    if not s.scene_plans:
        await plan_scenes(session_id)

    plan = next((x for x in s.scene_plans if int(x.get("scene_index", 0) or 0) == idx), None) or {}
    plan["static_scene_description"] = static_desc.strip()
    static_prompt = ", ".join(
        [p for p in ["cinematic music video still", static_desc.strip(), "soft lighting", "high quality", "16:9 composition"] if str(p).strip()]
    )

    return await gen_scene_image(session_id, idx, {"static_prompt": static_prompt})


@app.post("/api/video")
async def api_video(body: Dict[str, Any]) -> JSONResponse:
    """
    Mellow-Deck 요구사항: /api/video
    - motion_prompt를 받아 영상 굽기
    """
    session_id = body.get("session_id")
    scene_index = body.get("scene_index")
    motion_prompt = body.get("motion_prompt", "")
    if not isinstance(session_id, str) or not session_id:
        raise HTTPException(status_code=400, detail="session_id required")
    try:
        idx = int(scene_index)
    except Exception:
        raise HTTPException(status_code=400, detail="scene_index must be int")
    if not isinstance(motion_prompt, str) or not motion_prompt.strip():
        raise HTTPException(status_code=400, detail="motion_prompt must be non-empty string")
    return await gen_scene_video(session_id, idx, {"motion_prompt": motion_prompt})


@app.post("/api/session/{session_id}/merge")
async def merge_session(session_id: str) -> JSONResponse:
    """
    세션의 생성된 클립을 씬 순서대로 병합.
    (FFmpeg 가드: 없으면 500 / UI는 /api/ffmpeg_status로 선체크)
    """
    s = SESSIONS.get(session_id)
    if not s:
        raise HTTPException(status_code=404, detail="session not found")

    settings = _load_settings()
    ffmpeg = _ffmpeg_path(settings)  # raises if missing

    audio_path = Path(s.audio_path)
    if not audio_path.exists():
        raise HTTPException(status_code=400, detail="audio missing")
    if not s.scene_plans:
        raise HTTPException(status_code=400, detail="no scenes")

    ordered = [int(x.get("scene_index", 0) or 0) for x in s.scene_plans]
    clips: List[Path] = []
    for i in ordered:
        p = s.generated_clips.get(i)
        if p and Path(p).exists():
            clips.append(Path(p))
    if not clips:
        raise HTTPException(status_code=400, detail="no generated clips to merge")

    out_dir = get_output_directories(settings)["final"]
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = out_dir / f"{session_id}_{ts}.mp4"
    concat = out_dir / f".concat_{session_id}_{ts}.txt"
    concat.write_text("\n".join([f"file '{p.resolve().as_posix()}'" for p in clips]) + "\n", encoding="utf-8")

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
        str(concat),
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
        "-shortest",
        "-movflags",
        "+faststart",
        str(out),
    ]
    r = await asyncio.to_thread(lambda: subprocess.run(cmd, capture_output=True, text=True))
    if r.returncode != 0:
        raise HTTPException(status_code=500, detail=(r.stderr or "")[-2000:])
    write_sidecar_best_effort(
        out,
        artifact_type="final_video",
        source={"session_id": s.id, "audio_path": s.audio_path, "clip_count": len(clips)},
        runtime=_planner_runtime_info(),
        request={"strip_prompt_metadata": bool(_load_settings().get("outputs", {}).get("strip_prompt_metadata", False))},
    )
    ensure_sidecar(
        out,
        artifact_type="final_video",
        source={"session_id": s.id, "audio_path": s.audio_path, "clip_count": len(clips)},
        runtime=_planner_runtime_info(),
        request={"strip_prompt_metadata": bool(_load_settings().get("outputs", {}).get("strip_prompt_metadata", False))},
    )
    return JSONResponse({"output": str(out), "url": _to_served_url(out)})


@app.get("/api/session/{session_id}")
def get_session(session_id: str) -> JSONResponse:
    s = SESSIONS.get(session_id)
    if not s:
        raise HTTPException(status_code=404, detail="session not found")
    return JSONResponse({"id": s.id, "audio_path": s.audio_path, "lyrics_text": s.lyrics_text, "segments": s.segments})


@app.post("/api/merge")
async def merge_clips(
    audio_path: str = Form(...),
    clip_paths: str = Form(...),  # JSON array string
) -> JSONResponse:
    """
    unified outputs/final/ 로 병합 저장 (ffmpeg 필요)
    """
    settings = _load_settings()
    try:
        ffmpeg = _ffmpeg_path(settings)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    try:
        clips_in = json.loads(clip_paths)
        if not isinstance(clips_in, list):
            raise ValueError("clip_paths must be JSON array")
        clips = [Path(str(p)) for p in clips_in]
        clips = [p for p in clips if p.exists()]
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"invalid clip_paths: {e}")

    ap = Path(audio_path)
    if not ap.exists():
        raise HTTPException(status_code=400, detail="audio_path not found")
    if not clips:
        raise HTTPException(status_code=400, detail="no valid clips found")

    out_dir = get_output_directories(settings)["final"]
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = out_dir / f"final_{ts}.mp4"
    concat = out_dir / f".concat_{ts}.txt"
    concat.write_text("\n".join([f"file '{p.resolve().as_posix()}'" for p in clips]) + "\n", encoding="utf-8")

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
        str(concat),
        "-i",
        str(ap),
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
        "-shortest",
        "-movflags",
        "+faststart",
        str(out),
    ]
    r = await asyncio.to_thread(lambda: subprocess.run(cmd, capture_output=True, text=True))
    if r.returncode != 0:
        raise HTTPException(status_code=500, detail=(r.stderr or "")[-2000:])
    write_sidecar_best_effort(
        out,
        artifact_type="final_video",
        source={"session_id": "ad_hoc_merge", "audio_path": str(ap), "clip_count": len(clips)},
        runtime=_planner_runtime_info(),
        request={"strip_prompt_metadata": bool(_load_settings().get("outputs", {}).get("strip_prompt_metadata", False))},
    )
    ensure_sidecar(
        out,
        artifact_type="final_video",
        source={"session_id": "ad_hoc_merge", "audio_path": str(ap), "clip_count": len(clips)},
        runtime=_planner_runtime_info(),
        request={"strip_prompt_metadata": bool(_load_settings().get("outputs", {}).get("strip_prompt_metadata", False))},
    )

    return JSONResponse({"output": str(out), "count": len(clips)})


@app.get("/files")
def list_finals() -> JSONResponse:
    settings = _load_settings()
    out_dir = get_output_directories(settings)["final"]
    out_dir.mkdir(parents=True, exist_ok=True)
    files = sorted(out_dir.glob("*.mp4"), key=lambda p: p.stat().st_mtime, reverse=True)
    return JSONResponse({"files": [str(p.resolve()) for p in files]})


def _pick_free_port(host: str, start_port: int, *, max_tries: int = 50) -> int:
    for p in range(int(start_port), int(start_port) + int(max_tries)):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind((host, p))
                return p
            except OSError:
                continue
    raise RuntimeError(f"No free port found from {start_port} (tries={max_tries})")


if __name__ == "__main__":
    # 기본 포트는 8001 (8000 충돌이 자주 발생)
    host = (os.getenv("MELLOW_API_HOST", "127.0.0.1") or "127.0.0.1").strip()
    try:
        port = int((os.getenv("MELLOW_API_PORT", "8001") or "8001").strip())
    except Exception:
        port = 8001
    port = _pick_free_port(host, port)

    import uvicorn  # type: ignore

    print(f"[MellowAPI] starting on http://{host}:{port}")
    uvicorn.run(app, host=host, port=port, log_level="info")

