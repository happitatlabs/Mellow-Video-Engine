"""
Video Service - ComfyUI Integration (Blind Build)

This module mirrors the structure of ImageService, but targets "image -> video"
pipelines (e.g., SVD / Stable Video Diffusion) in ComfyUI.

Important notes:
  - This is intentionally a "path opener": it establishes the service, connects,
    queues a workflow, waits for execution_success, and downloads output files.
  - Node IDs / class_type names for SVD vary by ComfyUI setup.
    If no workflow JSON is found in mellow_link/data/workflows, a minimal
    placeholder workflow is used (may require adjustment later).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, Iterable

import aiohttp

from mellow_link.media.schemas import VideoRequest
from mellow_link.media.services.video_processor import (
    create_ambient_loop_from_image,
    extend_video_if_needed,
    probe_duration_seconds,
    stabilize_video_drift,
)
from mellow_link.services.output_provenance import ensure_sidecar, write_sidecar_best_effort
from mellow_link.services.runtime_config import (
    get_motion_video_spike_settings,
    get_output_directories,
    get_video_generation_settings,
)

logger = logging.getLogger(__name__)

# 🎯 The Magic Number (SVD 호환): 해상도 고정
MAGIC_WIDTH = 1216
MAGIC_HEIGHT = 704

class VideoStatus(Enum):
    DISCONNECTED = auto()
    CONNECTED = auto()
    GENERATING = auto()
    QUEUED = auto()
    ERROR = auto()


class VideoGenerationError(Exception):
    pass


@dataclass
class VideoResult:
    videos: List[Path]
    prompt_id: str
    generation_time_ms: float = 0.0


class VideoService:
    DEFAULT_HOST: str = "localhost"
    DEFAULT_PORT: int = 8188
    DEFAULT_TIMEOUT: float = 900.0  # videos can take longer

    def __init__(
        self,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        timeout: float = DEFAULT_TIMEOUT,
        output_dir: Optional[Path] = None,
    ):
        self.host = host
        self.port = port
        self.timeout = float(timeout)
        self.output_dir = (output_dir or get_output_directories()["videos"]).resolve()
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self._status: VideoStatus = VideoStatus.DISCONNECTED
        self._base_url: str = f"http://{host}:{port}"
        self._ws_url: str = f"ws://{host}:{port}/ws"
        self._client_id: str = str(uuid.uuid4())

        self._session: Optional[aiohttp.ClientSession] = None
        self._ws: Optional[aiohttp.ClientWebSocketResponse] = None
        self._ws_listener_task: Optional[asyncio.Task] = None

        self._current_prompt_id: Optional[str] = None
        self._execution_complete: asyncio.Event = asyncio.Event()
        self._execution_error: Optional[str] = None
        self._execution_outputs: Dict[str, Any] = {}

        self._workflows: Dict[str, Dict[str, Any]] = {}

        # Progress callbacks (best-effort)
        self._progress_callbacks: List[Any] = []

    # ---------------------------
    # Connection
    # ---------------------------

    async def connect(self) -> bool:
        try:
            logger.info(f"[VideoService] Connecting to ComfyUI at {self._base_url}")
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=self.timeout))

            async with self._session.get(f"{self._base_url}/system_stats") as resp:
                if resp.status != 200:
                    raise ConnectionError(f"ComfyUI returned status {resp.status}")
                _ = await resp.json()

            ws_url = f"{self._ws_url}?clientId={self._client_id}"
            self._ws = await self._session.ws_connect(ws_url)
            self._ws_listener_task = asyncio.create_task(self._ws_listener())

            self._status = VideoStatus.CONNECTED
            logger.info(f"[VideoService] Connected successfully (client_id: {self._client_id})")
            return True
        except Exception as e:
            self._status = VideoStatus.ERROR
            logger.error(f"[VideoService] Connection failed: {e}")
            raise ConnectionError(f"Failed to connect to ComfyUI at {self._base_url}: {e}") from e

    async def disconnect(self) -> None:
        if self._ws_listener_task:
            self._ws_listener_task.cancel()
            try:
                await self._ws_listener_task
            except asyncio.CancelledError:
                pass
            self._ws_listener_task = None

        if self._ws and not self._ws.closed:
            await self._ws.close()
        self._ws = None

        if self._session and not self._session.closed:
            await self._session.close()
        self._session = None

        self._status = VideoStatus.DISCONNECTED

    def is_available(self) -> bool:
        return self._status in (VideoStatus.CONNECTED, VideoStatus.GENERATING, VideoStatus.QUEUED)

    def get_status(self) -> VideoStatus:
        return self._status

    async def health_check(self) -> bool:
        if not self._session:
            return False
        try:
            async with self._session.get(
                f"{self._base_url}/system_stats",
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                return resp.status == 200
        except Exception:
            return False

    async def interrupt(self) -> bool:
        """
        Best-effort interrupt for current generation.

        ComfyUI provides /interrupt to stop current execution.
        """
        if not self._session:
            return False
        try:
            async with self._session.post(f"{self._base_url}/interrupt") as resp:
                ok = resp.status == 200
            if ok:
                self._execution_error = "Interrupted by user"
                self._execution_complete.set()
            return ok
        except Exception:
            return False

    # ---------------------------
    # WebSocket listener
    # ---------------------------

    async def _ws_listener(self) -> None:
        try:
            async for msg in self._ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    try:
                        data = json.loads(msg.data)
                    except Exception:
                        continue
                    await self._handle_ws_message(data)
                elif msg.type in (aiohttp.WSMsgType.ERROR, aiohttp.WSMsgType.CLOSED):
                    break
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"[VideoService] WebSocket listener error: {e}")

    async def _handle_ws_message(self, message: Dict[str, Any]) -> None:
        msg_type = message.get("type", "")
        data = message.get("data", {})

        prompt_id = data.get("prompt_id")
        if prompt_id and self._current_prompt_id and prompt_id != self._current_prompt_id:
            return

        if msg_type == "execution_start":
            self._status = VideoStatus.GENERATING
            for cb in list(self._progress_callbacks):
                try:
                    res = cb(10.0, "Execution started")
                    if asyncio.iscoroutine(res):
                        await res
                except Exception:
                    pass
        elif msg_type == "progress":
            # Some ComfyUI nodes emit progress events; best-effort support.
            value = data.get("value", 0)
            max_value = data.get("max", 1)
            pct = (float(value) / float(max_value)) * 100.0 if float(max_value) > 0 else 0.0
            for cb in list(self._progress_callbacks):
                try:
                    res = cb(pct, f"Step {value}/{max_value}")
                    if asyncio.iscoroutine(res):
                        await res
                except Exception:
                    pass
        elif msg_type == "executed":
            node = data.get("node")
            output = data.get("output", {})
            if node and output:
                self._execution_outputs[str(node)] = output
        elif msg_type == "execution_success":
            self._execution_error = None
            self._execution_complete.set()
            for cb in list(self._progress_callbacks):
                try:
                    res = cb(90.0, "Execution success")
                    if asyncio.iscoroutine(res):
                        await res
                except Exception:
                    pass
        elif msg_type == "execution_error":
            self._execution_error = data.get("exception_message", "Unknown error")
            self._execution_complete.set()
            for cb in list(self._progress_callbacks):
                try:
                    res = cb(0.0, "Execution error")
                    if asyncio.iscoroutine(res):
                        await res
                except Exception:
                    pass
        elif msg_type == "status":
            queue = data.get("status", {}).get("exec_info", {})
            if queue.get("queue_remaining", 0) > 0:
                self._status = VideoStatus.QUEUED
                for cb in list(self._progress_callbacks):
                    try:
                        res = cb(5.0, "Queued")
                        if asyncio.iscoroutine(res):
                            await res
                    except Exception:
                        pass

    # ---------------------------
    # Workflow loading
    # ---------------------------

    async def load_workflow(self, workflow_path: Path) -> str:
        if not workflow_path.exists():
            raise FileNotFoundError(f"Workflow not found: {workflow_path}")
        workflow = json.loads(workflow_path.read_text(encoding="utf-8"))
        name = workflow_path.stem
        self._workflows[name] = workflow
        logger.info(f"[VideoService] Loaded workflow: {name}")
        return name

    def _workflow_dir(self) -> Path:
        # repo structure: mellow_link/data/workflows
        return (Path(__file__).resolve().parents[2] / "data" / "workflows").resolve()

    def _find_video_workflow_file(self) -> Optional[Path]:
        d = self._workflow_dir()
        if not d.exists():
            return None
        candidates = sorted(d.glob("*.json"))
        # prefer svd/video keywords
        for p in candidates:
            n = p.name.lower()
            if "svd" in n or "video" in n:
                return p
        return None

    # ---------------------------
    # ComfyUI helpers
    # ---------------------------

    async def _queue_prompt(self, prompt: Dict[str, Any]) -> str:
        if not self._session:
            raise ConnectionError("Not connected to ComfyUI")
        payload = {"prompt": prompt, "client_id": self._client_id}
        try:
            logger.info(
                "[VideoService] Queueing workflow to %s (nodes=%s, client_id=%s)",
                f"{self._base_url}/prompt",
                len(prompt) if isinstance(prompt, dict) else "unknown",
                self._client_id,
            )
        except Exception:
            pass
        async with self._session.post(f"{self._base_url}/prompt", json=payload) as resp:
            if resp.status != 200:
                raise VideoGenerationError(f"Failed to queue prompt: {await resp.text()}")
            result = await resp.json()
            return result.get("prompt_id", "")

    async def _upload_image_to_input(self, image_path: Path) -> str:
        """
        Upload local image to ComfyUI input directory.

        Returns:
            ComfyUI-side filename to be referenced by LoadImage node.
        """
        if not self._session:
            raise ConnectionError("Not connected to ComfyUI")
        if not image_path.exists() or not image_path.is_file():
            raise FileNotFoundError(f"image_path not found: {image_path}")

        form = aiohttp.FormData()
        form.add_field("image", image_path.read_bytes(), filename=image_path.name, content_type="application/octet-stream")
        form.add_field("overwrite", "true")

        async with self._session.post(f"{self._base_url}/upload/image", data=form) as resp:
            if resp.status != 200:
                raise VideoGenerationError(f"Failed to upload image: {await resp.text()}")
            data = await resp.json()
            # Common ComfyUI response: {"name":"xxx.png","subfolder":"","type":"input"}
            name = data.get("name") or data.get("filename") or image_path.name
            return str(name)

    async def _get_generated_videos(self, prompt_id: str) -> List[Path]:
        if not self._session:
            return []
        async with self._session.get(f"{self._base_url}/history/{prompt_id}") as resp:
            if resp.status != 200:
                logger.warning("[VideoService] history fetch failed: status=%s", resp.status)
                return []
            history = await resp.json()

        out: List[Path] = []
        prompt_history = history.get(prompt_id, {})
        outputs = prompt_history.get("outputs", {})
        try:
            logger.info(
                "[VideoService] History fetched for prompt_id=%s (top_keys=%s, output_nodes=%s)",
                prompt_id,
                list(prompt_history.keys()) if isinstance(prompt_history, dict) else [],
                list(outputs.keys()) if isinstance(outputs, dict) else [],
            )
        except Exception:
            pass
        if not outputs:
            # 디버깅 포인트: workflow 노드/출력 키가 다르면 여기서 빈 결과가 나옴
            try:
                keys = list(prompt_history.keys()) if isinstance(prompt_history, dict) else []
                logger.warning(
                    "[VideoService] No outputs in history for prompt_id=%s (prompt_history keys=%s). "
                    "워크플로우 노드명/출력 노드를 확인하세요.",
                    prompt_id,
                    keys,
                )
            except Exception:
                logger.warning("[VideoService] No outputs in history for prompt_id=%s", prompt_id)

        for _node_id, node_output in outputs.items():
            # ComfyUI video outputs vary: "gifs", "videos", "animations", or SaveVideo under "images".
            animated_flags = node_output.get("animated")
            is_animated = bool(animated_flags[0]) if isinstance(animated_flags, list) and animated_flags else bool(animated_flags)
            for key in ("videos", "gifs", "animations", "images"):
                items = node_output.get(key)
                if not isinstance(items, list):
                    continue
                logger.info(
                    "[VideoService] Found candidate output list on node=%s key=%s count=%s",
                    _node_id,
                    key,
                    len(items),
                )
                for info in items:
                    if not isinstance(info, dict):
                        continue
                    filename = info.get("filename", "")
                    subfolder = info.get("subfolder", "")
                    vtype = info.get("type", "output")
                    if not filename:
                        continue
                    if key == "images":
                        suffix = Path(filename).suffix.lower()
                        if suffix not in {".mp4", ".webm", ".mkv", ".mov"} and not is_animated:
                            continue
                    p = await self._download_file(filename, subfolder=subfolder, file_type=vtype)
                    if p:
                        out.append(p)

        return out

    async def _download_file(self, filename: str, *, subfolder: str = "", file_type: str = "output") -> Optional[Path]:
        if not self._session:
            return None
        params = {"filename": filename, "subfolder": subfolder, "type": file_type}
        async with self._session.get(f"{self._base_url}/view", params=params) as resp:
            if resp.status != 200:
                try:
                    logger.warning(
                        "[VideoService] download failed status=%s params=%s body=%s",
                        resp.status,
                        params,
                        (await resp.text())[:300],
                    )
                except Exception:
                    logger.warning("[VideoService] download failed status=%s params=%s", resp.status, params)
                return None
            content = await resp.read()
        local_path = self.output_dir / filename
        local_path.write_bytes(content)
        provenance = getattr(self, "_current_request_provenance", None)
        if provenance:
            write_sidecar_best_effort(
                local_path,
                artifact_type="video",
                source=provenance.get("source", {}),
                runtime=provenance.get("runtime", {}),
                request=provenance.get("request", {}),
            )
            ensure_sidecar(
                local_path,
                artifact_type="video",
                source=provenance.get("source", {}),
                runtime=provenance.get("runtime", {}),
                request=provenance.get("request", {}),
            )
        return local_path

    # ---------------------------
    # Prompt building (fallback)
    # ---------------------------

    def _build_svd_placeholder_prompt(self, *, comfy_input_image_name: str, motion_bucket_id: int) -> Dict[str, Any]:
        """
        Minimal placeholder workflow. Node IDs/class_type will likely need adjustment.
        """
        return {
            # Load image
            "1": {
                "class_type": "LoadImage",
                "inputs": {"image": comfy_input_image_name},
            },
            # SVD / img2vid (placeholder)
            "2": {
                "class_type": "SVD_img2vid",
                "inputs": {
                    "image": ["1", 0],
                    "motion_bucket_id": int(motion_bucket_id),
                },
            },
            # Save video (placeholder)
            "3": {
                "class_type": "SaveVideo",
                "inputs": {"video": ["2", 0], "filename_prefix": "mellow_video"},
            },
        }

    def _inject_image_and_motion(
        self,
        workflow: Dict[str, Any],
        *,
        comfy_input_image_name: str,
        motion_bucket_id: int,
        prompt: Optional[str] = None,
        width: Optional[int] = None,
        height: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Best-effort injection for typical nodes:
          - LoadImage.inputs.image
          - any node input named motion_bucket_id / motion_bucket
          - any node input named text (when it contains %PROMPT% / %NEGATIVE_PROMPT%)
        """
        resolved_width = int(width) if width else int(MAGIC_WIDTH)
        resolved_height = int(height) if height else int(MAGIC_HEIGHT)
        load_image_node_id: Optional[str] = None
        for _nid, node in workflow.items():
            if not isinstance(node, dict):
                continue
            inputs = node.get("inputs")
            if not isinstance(inputs, dict):
                continue
            class_type = (node.get("class_type") or "").lower()
            if class_type == "loadimage":
                inputs["image"] = comfy_input_image_name
                load_image_node_id = str(_nid)
            if prompt and isinstance(inputs.get("text"), str) and "%PROMPT%" in inputs["text"]:
                inputs["text"] = inputs["text"].replace("%PROMPT%", prompt)
            if isinstance(inputs.get("text"), str) and "%NEGATIVE_PROMPT%" in inputs["text"]:
                inputs["text"] = inputs["text"].replace("%NEGATIVE_PROMPT%", "")
            if "motion_bucket_id" in inputs:
                inputs["motion_bucket_id"] = int(motion_bucket_id)
            if "motion_bucket" in inputs:
                inputs["motion_bucket"] = int(motion_bucket_id)
            # Width/height follow the request for local-motion workflows; legacy SVD falls back to MAGIC_*.
            if "width" in inputs:
                inputs["width"] = resolved_width
            if "height" in inputs:
                inputs["height"] = resolved_height

        # --- (요구사항) SVD 직접 연결 보장 ---
        #  - 14번 노드(또는 SVD 관련 노드)의 init_image를 강제로 1번 LoadImage에 연결
        #  - ComfyUI 연결 포맷: ["<node_id>", <output_index>]
        #  - 1번 노드가 LoadImage가 아닐 수 있으므로, 가능하면 실제 LoadImage 노드를 찾는다.
        load_id: Optional[str] = None
        if "1" in workflow and isinstance(workflow.get("1"), dict):
            ct1 = (workflow["1"].get("class_type") or "").lower()
            if ct1 == "loadimage":
                load_id = "1"
        if load_id is None:
            load_id = load_image_node_id

        if load_id:
            # 1) 우선순위: 14번 노드가 있으면 무조건 init_image를 꽂는다.
            if "14" in workflow and isinstance(workflow.get("14"), dict):
                n14 = workflow["14"]
                in14 = n14.get("inputs")
                if isinstance(in14, dict):
                    in14["init_image"] = [str(load_id), 0]

            # 2) 보조: init_image 입력을 가진 SVD 관련 노드에도 동일 연결을 강제한다.
            for nid, node in workflow.items():
                if not isinstance(node, dict):
                    continue
                inputs = node.get("inputs")
                if not isinstance(inputs, dict):
                    continue
                class_type = (node.get("class_type") or "").lower()
                if nid == "14":
                    continue
                if "init_image" in inputs and ("svd" in class_type or "video" in class_type):
                    inputs["init_image"] = [str(load_id), 0]

        return workflow

    def _normalize_mode(self, mode: Any) -> str:
        if mode is None or str(mode).strip() == "":
            runtime = self._runtime_video_settings()
            return str(runtime.get("default_mode") or "LOCAL_MOTION_LOOP").strip().upper()
        # Enum 지원 (mode.name)
        name = getattr(mode, "name", None)
        if isinstance(name, str) and name:
            normalized = name.strip().upper()
        else:
            normalized = str(mode).strip().upper()
        if normalized == "VIDEO_LOCKED_CAMERA":
            return "AMBIENT_STILL_LOOP"
        return normalized

    def _resolve_motion_bucket_id(self, requested_motion_bucket_id: int, prompt_text: str) -> int:
        text = str(prompt_text or "").strip().lower()
        if any(token in text for token in ("locked", "static", "fixed", "tripod", "lock-off", "locked-off", "고정", "정지")):
            return min(int(requested_motion_bucket_id), 1)
        if any(token in text for token in ("zoom", "push", "dolly")):
            return 80
        if "pan" in text:
            return 110
        if any(token in text for token in ("handheld", "dynamic", "run", "shake", "격렬")):
            return 170
        return int(requested_motion_bucket_id)

    def _ambient_loop_profile(self, prompt_text: str) -> Dict[str, Any]:
        text = str(prompt_text or "").strip()
        lowered = text.lower()

        def has_any(*tokens: str) -> bool:
            return any(token in lowered for token in tokens)

        strength = 0.56
        if has_any("subtle", "gentle", "faint", "soft", "잔잔", "미세", "약하게", "은은"):
            strength -= 0.08
        if has_any("visible", "alive", "lively", "clear", "pronounced", "strong", "살아", "더 움직", "뚜렷", "강하게"):
            strength += 0.12
        if has_any("dramatic", "intense", "격렬", "강렬"):
            strength += 0.08

        light_pulse = 0.66
        haze_drift = 0.42
        foliage_shimmer = 0.46
        fabric_shimmer = 0.40
        local_emphasis = 0.88
        global_balance = 0.10

        if has_any("light", "glow", "beam", "sunbeam", "shadow", "reflection", "window", "windowlight", "빛", "광선", "햇살", "창빛", "반사", "그림자"):
            light_pulse += 0.24
        if has_any("fog", "haze", "mist", "smoke", "dust", "안개", "연무", "먼지", "아지랑이"):
            haze_drift += 0.28
        if has_any("grass", "reed", "leaf", "leaves", "tree", "foliage", "flower", "wheat", "풀", "갈대", "잎", "나무", "꽃", "억새"):
            foliage_shimmer += 0.36
        if has_any("curtain", "fabric", "cloth", "veil", "drape", "flag", "ribbon", "커튼", "천", "직물", "옷자락", "깃발", "리본"):
            fabric_shimmer += 0.36
        if has_any("local", "localized", "isolated", "one area", "부분", "국소", "일부", "한쪽", "특정 영역"):
            local_emphasis += 0.14
            global_balance -= 0.06
        if has_any("whole frame", "overall", "everywhere", "room-wide", "전체", "전역", "장면 전체"):
            global_balance += 0.12
            local_emphasis -= 0.08

        region_style = "window_light"
        if has_any("forest", "field", "grass", "reed", "leaf", "tree", "foliage", "풀", "갈대", "잎", "숲"):
            region_style = "foliage"
        elif has_any("curtain", "fabric", "cloth", "veil", "drape", "커튼", "천", "직물"):
            region_style = "fabric"
        elif has_any("reflection", "water", "ripple", "mirror", "pond", "반사", "수면", "파문", "거울"):
            region_style = "reflection"

        profile = {
            "direction_text": text,
            "overall_strength": max(0.34, min(strength, 0.82)),
            "light_pulse": max(0.0, min(light_pulse, 1.0)),
            "haze_drift": max(0.0, min(haze_drift, 1.0)),
            "foliage_shimmer": max(0.0, min(foliage_shimmer, 1.0)),
            "fabric_shimmer": max(0.0, min(fabric_shimmer, 1.0)),
            "local_motion_emphasis": max(0.15, min(local_emphasis, 0.95)),
            "global_motion_balance": max(0.05, min(global_balance, 0.45)),
            "region_style": region_style,
            "visibility_mode": "visibility_first",
        }
        return profile

    def _runtime_video_settings(self) -> Dict[str, Any]:
        return get_video_generation_settings()

    def _runtime_motion_spike_settings(self) -> Dict[str, Any]:
        return get_motion_video_spike_settings()

    def _use_local_locked_camera_backend(self, mode: str) -> bool:
        runtime = self._runtime_video_settings()
        backend = str(runtime.get("locked_camera_backend", "ambient_loop") or "ambient_loop").strip().lower()
        return str(mode or "").strip().upper() == "AMBIENT_STILL_LOOP" and backend == "ambient_loop"

    def _select_video_workflow_name(self, requested_workflow: Optional[str], mode: str) -> Optional[str]:
        runtime = self._runtime_video_settings()
        motion_spike = self._runtime_motion_spike_settings()
        normalized = str(mode or "").strip().upper()
        if normalized == "LOCAL_MOTION_LOOP":
            return str(
                runtime.get("local_motion_workflow")
                or motion_spike.get("workflow")
                or requested_workflow
                or "ltx_2b_v0_9_ckpt_i2v_lowmem.json"
            )
        if normalized == "AMBIENT_STILL_LOOP":
            return str(runtime.get("locked_camera_workflow") or requested_workflow or "svd_xt_locked_camera.json")
        if runtime.get("locked_camera_mode"):
            if requested_workflow and str(requested_workflow).strip().lower() == "svd_xt_main.json":
                return str(runtime.get("locked_camera_workflow") or "svd_xt_locked_camera.json")
            if not requested_workflow:
                return str(runtime.get("locked_camera_workflow") or "svd_xt_locked_camera.json")
        return requested_workflow

    def _apply_locked_camera_overrides(
        self,
        workflow: Dict[str, Any],
        *,
        mode: str,
        motion_bucket_id: int,
    ) -> Dict[str, Any]:
        runtime = self._runtime_video_settings()
        normalized = str(mode or "").strip().upper()
        if not (runtime.get("locked_camera_mode") or normalized == "AMBIENT_STILL_LOOP"):
            return workflow

        locked_motion_bucket = min(int(runtime.get("motion_bucket_id", 1) or 1), int(motion_bucket_id))
        locked_frames = int(runtime.get("video_frames", 21) or 21)
        raw_fps = int(runtime.get("raw_fps", 7) or 7)
        output_fps = int(runtime.get("output_fps", 8) or 8)
        augmentation = float(runtime.get("augmentation_level", 0.0) or 0.0)
        sampler_steps = int(runtime.get("sampler_steps_locked", 14) or 14)
        sampler_cfg = float(runtime.get("sampler_cfg_locked", 1.5) or 1.5)

        if "14" in workflow and isinstance(workflow.get("14"), dict):
            inputs = workflow["14"].get("inputs")
            if isinstance(inputs, dict):
                inputs["motion_bucket_id"] = int(locked_motion_bucket)
                inputs["augmentation_level"] = float(augmentation)
                inputs["video_frames"] = int(locked_frames)
                inputs["fps"] = int(raw_fps)
                inputs["width"] = int(MAGIC_WIDTH)
                inputs["height"] = int(MAGIC_HEIGHT)

        if "19" in workflow and isinstance(workflow.get("19"), dict):
            inputs = workflow["19"].get("inputs")
            if isinstance(inputs, dict):
                inputs["steps"] = int(sampler_steps)
                inputs["cfg"] = float(sampler_cfg)

        if "24" in workflow and isinstance(workflow.get("24"), dict):
            inputs = workflow["24"].get("inputs")
            if isinstance(inputs, dict):
                inputs["frame_rate"] = int(output_fps)
                inputs["pingpong"] = False
                inputs["loop_count"] = 0

        return workflow

    def _is_video_output_node(self, node: Dict[str, Any]) -> bool:
        ct = (node.get("class_type") or "").lower()
        # typical save/combine nodes for videos/gifs/webp animations
        if "vhs_videocombine" in ct:
            return True
        if "videocombine" in ct:
            return True
        if ct.startswith("save") and any(k in ct for k in ("video", "gif", "webp", "animation")):
            return True
        if "savevideo" in ct:
            return True
        return False

    def _iter_node_deps(self, node: Dict[str, Any]) -> Iterable[str]:
        inputs = node.get("inputs")
        if not isinstance(inputs, dict):
            return []
        deps: List[str] = []
        for v in inputs.values():
            # ComfyUI connection: ["<node_id>", <output_idx>]
            if isinstance(v, (list, tuple)) and len(v) >= 1:
                src = v[0]
                if isinstance(src, (str, int)):
                    deps.append(str(src))
        return deps

    def _strip_flux_nodes_for_video_only(self, workflow: Dict[str, Any]) -> Dict[str, Any]:
        """
        VIDEO_ONLY 모드:
          - Flux 관련 노드들을 JSON에서 제거
          - 비디오 output 노드에서 역추적해 필요한 노드만 남기도록 프루닝(가능한 경우)
        """
        # 1) Flux 노드 제거 (best-effort: class_type / meta title 기반)
        to_remove: Set[str] = set()
        for nid, node in workflow.items():
            if not isinstance(node, dict):
                continue
            ct = (node.get("class_type") or "").lower()
            meta = node.get("_meta") if isinstance(node.get("_meta"), dict) else {}
            title = (meta.get("title") or "").lower() if isinstance(meta, dict) else ""
            if "flux" in ct or "flux" in title:
                to_remove.add(str(nid))

        if to_remove:
            for nid in to_remove:
                workflow.pop(nid, None)

        # 2) 비디오 output 노드를 기준으로 필요한 노드만 남기기 (없으면 프루닝 스킵)
        output_ids: List[str] = []
        for nid, node in workflow.items():
            if isinstance(node, dict) and self._is_video_output_node(node):
                output_ids.append(str(nid))

        if not output_ids:
            return workflow

        needed: Set[str] = set()
        stack: List[str] = list(output_ids)
        while stack:
            cur = stack.pop()
            if cur in needed:
                continue
            needed.add(cur)
            node = workflow.get(cur)
            if not isinstance(node, dict):
                continue
            for dep in self._iter_node_deps(node):
                if dep in workflow and dep not in needed:
                    stack.append(dep)

        # keep only needed
        for nid in list(workflow.keys()):
            if str(nid) not in needed:
                workflow.pop(nid, None)

        return workflow

    # ---------------------------
    # Public API
    # ---------------------------

    async def generate_video(self, request: VideoRequest, on_progress: Optional[Any] = None) -> str:
        """
        Generate a video. get_media_ai().generate_video()로 위임.
        ENABLE_MEDIA_AI=0이면 RuntimeError.
        """
        if self.is_available() and self._session and self._ws:
            return await self._generate_video_impl(request, on_progress=on_progress)
        from mellow_link.media.adapters.factory import get_media_ai
        return await get_media_ai().generate_video(request, on_progress=on_progress)

    async def _generate_video_impl(self, request: VideoRequest, on_progress: Optional[Any] = None) -> str:
        """
        ComfyUI 실제 동영상 생성 로직. 어댑터(ComfyMediaAIAdapter)에서만 호출.
        호출 전 connect() 완료된 상태여야 함.
        """
        mode = self._normalize_mode(getattr(request, "mode", None))
        if self._use_local_locked_camera_backend(mode):
            return await self._generate_locked_camera_ambient_loop(request, on_progress=on_progress)
        if not self.is_available() or not self._session or not self._ws:
            raise VideoGenerationError("VideoService not connected")

        # Optional progress hook (UI용)
        if on_progress:
            self._progress_callbacks.append(on_progress)

        start_time = time.time()
        try:
            # Reset execution state
            self._execution_complete.clear()
            self._execution_error = None
            self._execution_outputs = {}
            self._current_prompt_id = None
            self._current_request_provenance = getattr(request, "provenance", None)

            # Upload input image to ComfyUI
            local_image = Path(request.image_path).resolve()
            if not local_image.exists():
                raise FileNotFoundError(f"VideoRequest.image_path not found: {local_image}")
            # 디버깅: 입력 이미지 크기 로그 (PIL이 없으면 스킵)
            try:
                from PIL import Image  # type: ignore

                with Image.open(local_image) as im:
                    logger.info("[VideoService] input image size=%sx%s path=%s", im.size[0], im.size[1], local_image)
            except Exception:
                logger.info("[VideoService] input image path=%s", local_image)
            comfy_name = await self._upload_image_to_input(local_image)

            # 프롬프트 이원화: motion_prompt 우선
            motion_prompt = getattr(request, "motion_prompt", None)
            prompt_text = (
                motion_prompt
                if isinstance(motion_prompt, str) and motion_prompt.strip()
                else getattr(request, "prompt", None)
            )
            if prompt_text is None or not str(prompt_text).strip():
                raise VideoGenerationError("VideoRequest.motion_prompt 또는 VideoRequest.prompt 는 필수입니다. (빈 값/누락 불가)")
            prompt_text = str(prompt_text).strip()
            effective_motion_bucket_id = self._resolve_motion_bucket_id(
                int(getattr(request, "motion_bucket_id", 40) or 40),
                prompt_text,
            )

            # Resolve workflow
            prompt: Dict[str, Any]
            workflow_path: Optional[Path] = None
            selected_workflow = self._select_video_workflow_name(getattr(request, "workflow", None), mode)
            if selected_workflow:
                workflow_path = self._workflow_dir() / selected_workflow
                if not workflow_path.exists():
                    workflow_path = None
            if workflow_path is None:
                workflow_path = self._find_video_workflow_file()

            if workflow_path is not None:
                import copy

                workflow_name = await self.load_workflow(workflow_path)
                runtime_video = self._runtime_video_settings()
                logger.info(
                    "[VideoService] Using workflow file=%s mode=%s motion_bucket_id=%s prompt=%r locked_camera_mode=%s stabilize_zoom_drift=%s",
                    str(workflow_path),
                    mode,
                    effective_motion_bucket_id,
                    prompt_text[:220],
                    bool(runtime_video.get("locked_camera_mode", False)),
                    bool(runtime_video.get("stabilize_zoom_drift", False)),
                )
                prompt = copy.deepcopy(self._workflows[workflow_name])
                prompt = self._inject_image_and_motion(
                    prompt,
                    comfy_input_image_name=comfy_name,
                    motion_bucket_id=effective_motion_bucket_id,
                    prompt=prompt_text,
                    width=int(getattr(request, "width", MAGIC_WIDTH) or MAGIC_WIDTH),
                    height=int(getattr(request, "height", MAGIC_HEIGHT) or MAGIC_HEIGHT),
                )
                prompt = self._apply_locked_camera_overrides(
                    prompt,
                    mode=mode,
                    motion_bucket_id=effective_motion_bucket_id,
                )
                # --- (요구사항) 모드 선택: VIDEO_ONLY면 Flux 관련 노드 제거 후 실행 ---
                if mode in {"VIDEO_ONLY", "AMBIENT_STILL_LOOP"}:
                    prompt = self._strip_flux_nodes_for_video_only(prompt)
                    logger.info(
                        "[VideoService] %s workflow pruned (remaining_nodes=%s)",
                        mode,
                        len(prompt) if isinstance(prompt, dict) else "unknown",
                    )
            else:
                logger.warning(
                    "[VideoService] Workflow file not found. Falling back to placeholder SVD workflow "
                    "(mode=%s, motion_bucket_id=%s)",
                    mode,
                    effective_motion_bucket_id,
                )
                prompt = self._build_svd_placeholder_prompt(
                    comfy_input_image_name=comfy_name,
                    motion_bucket_id=effective_motion_bucket_id,
                )

            # Start / queued progress
            for cb in list(self._progress_callbacks):
                try:
                    res = cb(0.0, "Queued")
                    if asyncio.iscoroutine(res):
                        await res
                except Exception:
                    pass

            prompt_id = await self._queue_prompt(prompt)
            self._current_prompt_id = prompt_id
            for cb in list(self._progress_callbacks):
                try:
                    res = cb(8.0, "Submitted")
                    if asyncio.iscoroutine(res):
                        await res
                except Exception:
                    pass

            try:
                await asyncio.wait_for(self._execution_complete.wait(), timeout=self.timeout)
            except asyncio.TimeoutError:
                raise TimeoutError("Video generation timed out")

            if self._execution_error:
                raise VideoGenerationError(self._execution_error)

            videos = await self._get_generated_videos(prompt_id)
            if not videos:
                raise VideoGenerationError("No video outputs found in ComfyUI history")
            for cb in list(self._progress_callbacks):
                try:
                    res = cb(95.0, "Downloaded outputs")
                    if asyncio.iscoroutine(res):
                        await res
                except Exception:
                    pass

            _ = VideoResult(
                videos=videos,
                prompt_id=prompt_id,
                generation_time_ms=(time.time() - start_time) * 1000,
            )
            logger.info("[VideoService] Downloaded video outputs for prompt_id=%s -> %s", prompt_id, [str(v) for v in videos])

            # Post-process: extend to target duration if needed (best-effort)
            raw_video = videos[0]
            try:
                runtime_video = self._runtime_video_settings()
                tgt = float(getattr(request, "target_duration", 12.0))
                fps = int(getattr(request, "fps", runtime_video.get("output_fps", 8)))
                loop_mode = str(getattr(request, "loop_mode", "boomerang"))
                overlap = float(getattr(request, "overlap_seconds", 0.35))
                stabilize = bool(runtime_video.get("stabilize_zoom_drift", False))
                stabilization_strength = float(runtime_video.get("stabilization_strength", 0.18) or 0.18)

                # ffprobe/ffmpeg는 subprocess를 쓰므로 이벤트 루프 블로킹 방지
                raw_d = await asyncio.to_thread(probe_duration_seconds, raw_video)
                logger.info(
                    "[VideoService] Post-process check: raw=%s (exists=%s, dur=%s) -> target=%ss, mode=%s, fps=%s, overlap=%s",
                    str(raw_video),
                    bool(Path(raw_video).exists()),
                    raw_d,
                    tgt,
                    loop_mode,
                    fps,
                    overlap,
                )

                should_attempt = (raw_d is None) or (raw_d < tgt)
                if tgt > 0 and should_attempt:
                    looped = await asyncio.to_thread(
                        extend_video_if_needed,
                        raw_video,
                        target_duration=tgt,
                        fps=fps,
                        mode=loop_mode,
                        overlap_seconds=overlap,
                    )
                    try:
                        final_output = Path(looped).resolve()
                        if stabilize:
                            stabilized = await asyncio.to_thread(
                                stabilize_video_drift,
                                final_output,
                                strength=stabilization_strength,
                            )
                            stabilized_path = Path(stabilized).resolve()
                            if stabilized_path != final_output:
                                logger.info("[VideoService] Drift stabilization applied: %s", stabilized_path)
                                final_output = stabilized_path
                        if final_output != Path(raw_video).resolve():
                            logger.info("[VideoService] Post-process applied: %s", final_output)
                            provenance = getattr(self, "_current_request_provenance", None)
                            if provenance:
                                write_sidecar_best_effort(
                                    final_output,
                                    artifact_type="video",
                                    source=provenance.get("source", {}),
                                    runtime=provenance.get("runtime", {}),
                                    request=provenance.get("request", {}),
                                )
                                ensure_sidecar(
                                    final_output,
                                    artifact_type="video",
                                    source=provenance.get("source", {}),
                                    runtime=provenance.get("runtime", {}),
                                    request=provenance.get("request", {}),
                                )
                            return str(final_output)
                    except Exception:
                        pass

                    logger.warning(
                        "[VideoService] Post-process requested but returned original (raw_d=%s, tgt=%s, mode=%s). "
                        "ffmpeg/ffprobe PATH 또는 입력 포맷을 확인하세요.",
                        raw_d,
                        tgt,
                        loop_mode,
                    )
            except Exception as e:
                logger.exception("[VideoService] Post-process skipped: %s", e)

            # Return raw video path
            provenance = getattr(self, "_current_request_provenance", None)
            if provenance:
                write_sidecar_best_effort(
                    Path(raw_video).resolve(),
                    artifact_type="video",
                    source=provenance.get("source", {}),
                    runtime=provenance.get("runtime", {}),
                    request=provenance.get("request", {}),
                )
                ensure_sidecar(
                    Path(raw_video).resolve(),
                    artifact_type="video",
                    source=provenance.get("source", {}),
                    runtime=provenance.get("runtime", {}),
                    request=provenance.get("request", {}),
                )
            return str(Path(raw_video).resolve())
        except Exception as e:
            logger.exception("[VideoService] Video generation failed: %s", e)
            raise
        finally:
            self._current_request_provenance = None
            if on_progress and on_progress in self._progress_callbacks:
                try:
                    self._progress_callbacks.remove(on_progress)
                except Exception:
                    pass

    async def _generate_locked_camera_ambient_loop(self, request: VideoRequest, on_progress: Optional[Any] = None) -> str:
        runtime_video = self._runtime_video_settings()
        local_image = Path(request.image_path).resolve()
        if not local_image.exists():
            raise FileNotFoundError(f"VideoRequest.image_path not found: {local_image}")

        if on_progress:
            self._progress_callbacks.append(on_progress)
        self._current_request_provenance = getattr(request, "provenance", None)
        try:
            target_duration = float(getattr(request, "target_duration", 12.0))
            fps = int(getattr(request, "fps", runtime_video.get("output_fps", 8)) or runtime_video.get("output_fps", 8))
            strength = float(runtime_video.get("ambient_motion_strength", runtime_video.get("stabilization_strength", 0.18)) or 0.18)
            stabilize = False
            stabilization_strength = float(runtime_video.get("stabilization_strength", 0.18) or 0.18)
            motion_prompt = getattr(request, "motion_prompt", None) or getattr(request, "prompt", None) or ""
            motion_profile = self._ambient_loop_profile(str(motion_prompt))
            motion_profile["overall_strength"] = max(
                float(motion_profile.get("overall_strength", strength) or strength),
                strength,
            )
            motion_profile["visibility_mode"] = str(
                runtime_video.get("ambient_visibility_mode", motion_profile.get("visibility_mode", "visibility_first"))
                or "visibility_first"
            )
            motion_profile["debug_visualization"] = bool(runtime_video.get("ambient_debug_visualization", False))
            motion_profile["min_patch_alpha"] = float(runtime_video.get("ambient_min_patch_alpha", 0.14) or 0.14)
            motion_profile["min_patch_shift_px"] = float(runtime_video.get("ambient_min_patch_shift_px", 6.0) or 6.0)
            motion_profile["min_light_pulse"] = float(runtime_video.get("ambient_min_light_pulse", 0.55) or 0.55)

            base_name = f"{local_image.stem}_ambient_loop_{int(round(target_duration))}s.mp4"
            output_path = (self.output_dir / base_name).resolve()
            logger.info(
                "[VideoService] Using local ambient-still-loop backend image=%s output=%s duration=%s fps=%s profile=%s stabilize=%s (ambient backend disables deshake to preserve local motion)",
                local_image,
                output_path,
                target_duration,
                fps,
                motion_profile,
                stabilize,
            )

            for cb in list(self._progress_callbacks):
                try:
                    res = cb(5.0, "Ambient loop queued")
                    if asyncio.iscoroutine(res):
                        await res
                except Exception:
                    pass

            composed = await asyncio.to_thread(
                create_ambient_loop_from_image,
                local_image,
                output_path=output_path,
                target_duration=target_duration,
                fps=fps,
                strength=strength,
                motion_profile=motion_profile,
            )

            final_output = Path(composed).resolve()
            if stabilize:
                stabilized = await asyncio.to_thread(
                    stabilize_video_drift,
                    final_output,
                    strength=stabilization_strength,
                )
                stabilized_path = Path(stabilized).resolve()
                if stabilized_path != final_output:
                    logger.info("[VideoService] Ambient loop stabilization applied: %s", stabilized_path)
                    final_output = stabilized_path

            for cb in list(self._progress_callbacks):
                try:
                    res = cb(95.0, "Ambient loop rendered")
                    if asyncio.iscoroutine(res):
                        await res
                except Exception:
                    pass

            provenance = getattr(self, "_current_request_provenance", None)
            if provenance:
                runtime_payload = dict(provenance.get("runtime", {}))
                runtime_payload["video_backend"] = "ambient_loop"
                runtime_payload["locked_camera_mode"] = True
                request_payload = dict(provenance.get("request", {}))
                request_payload["ambient_motion_strength"] = strength
                request_payload["ambient_loop_direction"] = str(motion_prompt)
                request_payload["ambient_motion_profile"] = motion_profile
                request_payload["ambient_debug_visualization"] = bool(runtime_video.get("ambient_debug_visualization", False))
                request_payload["ambient_visibility_mode"] = str(runtime_video.get("ambient_visibility_mode", "visibility_first"))
                write_sidecar_best_effort(
                    final_output,
                    artifact_type="video",
                    source=provenance.get("source", {}),
                    runtime=runtime_payload,
                    request=request_payload,
                )
                ensure_sidecar(
                    final_output,
                    artifact_type="video",
                    source=provenance.get("source", {}),
                    runtime=runtime_payload,
                    request=request_payload,
                )
            return str(final_output)
        finally:
            self._current_request_provenance = None
            if on_progress and on_progress in self._progress_callbacks:
                try:
                    self._progress_callbacks.remove(on_progress)
                except Exception:
                    pass


def create_video_service(
    host: str = "localhost",
    port: int = 8188,
    timeout: float = 900.0,
    output_dir: Optional[Path] = None,
) -> VideoService:
    return VideoService(host=host, port=port, timeout=timeout, output_dir=output_dir)

