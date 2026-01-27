"""
ComfyUI Video Agent
====================
ComfyUI API를 통해 LTX-Video 및 SVD 모델을 구동하는 자동화 에이전트.

Features:
- WebSocket 기반 실시간 상태 모니터링
- LTX-Video / SVD 워크플로우 동적 조작
- 시드 자동 랜덤화 (캐시 버그 방지)
- VRAM 자동 정리
- 결과물 자동 다운로드

Author: Mellow-Video-Engine Team
"""

from __future__ import annotations

import json
import logging
import random
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from threading import Thread, Event
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

import requests
from websocket import WebSocket, create_connection, WebSocketException

# Type aliases
JsonDict = Dict[str, Any]
NodeId = str

logger = logging.getLogger(__name__)


# =============================================================================
# Configuration & Enums
# =============================================================================

class WorkflowType(str, Enum):
    """지원되는 워크플로우 유형."""
    LTX_VIDEO = "ltx_video"
    SVD = "svd"


class ExecutionStatus(str, Enum):
    """실행 상태."""
    QUEUED = "queued"
    EXECUTING = "executing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class ComfyConfig:
    """ComfyUI 서버 설정."""
    host: str = "127.0.0.1"
    port: int = 8188
    use_ssl: bool = False

    # 워크플로우 파일 경로
    ltx_workflow_path: Optional[Path] = None
    svd_workflow_path: Optional[Path] = None

    # 타임아웃 설정 (초)
    connection_timeout: float = 10.0
    execution_timeout: float = 600.0  # 10분

    # 재시도 설정
    max_retries: int = 3
    retry_delay: float = 2.0

    @property
    def http_base_url(self) -> str:
        protocol = "https" if self.use_ssl else "http"
        return f"{protocol}://{self.host}:{self.port}"

    @property
    def ws_url(self) -> str:
        protocol = "wss" if self.use_ssl else "ws"
        return f"{protocol}://{self.host}:{self.port}/ws"


@dataclass
class VideoGenerationParams:
    """비디오 생성 파라미터."""
    # 공통
    prompt: str = ""
    negative_prompt: str = ""
    seed: Optional[int] = None  # None이면 랜덤 생성

    # LTX-Video 전용
    num_frames: int = 97  # 약 4초 @ 24fps
    fps: int = 24
    width: int = 768
    height: int = 512

    # SVD 전용
    source_image_path: Optional[Path] = None
    motion_bucket_id: int = 127  # 1-255, 높을수록 움직임 증가
    augmentation_level: float = 0.0

    # 출력 설정
    output_prefix: str = "mellow"
    output_dir: Optional[Path] = None


@dataclass
class GenerationResult:
    """생성 결과."""
    success: bool
    prompt_id: str = ""
    output_files: List[Path] = field(default_factory=list)
    execution_time: float = 0.0
    error_message: str = ""
    node_errors: Dict[str, str] = field(default_factory=dict)


# =============================================================================
# Node Mapping Configuration
# =============================================================================

"""
=============================================================================
워크플로우 노드 매핑 가이드 (JSON에서 확인해야 할 Node ID)
=============================================================================

ComfyUI 워크플로우 JSON(API Format)에서 각 노드는 고유한 ID(문자열)를 가집니다.
아래는 일반적인 노드 구조와 수정해야 할 속성입니다.

[LTX-Video 워크플로우 예시]
{
    "3": {                                    # <- 이것이 Node ID
        "class_type": "KSampler",             # <- 노드 타입
        "inputs": {
            "seed": 123456789,                # <- 수정 대상: 매번 랜덤화
            "steps": 20,
            "cfg": 7.5,
            ...
        }
    },
    "6": {
        "class_type": "CLIPTextEncode",       # 또는 "LTXVGemmaEnhancePrompt"
        "inputs": {
            "text": "your prompt here"        # <- 수정 대상: 사용자 프롬프트
        }
    },
    "25": {
        "class_type": "SaveVideo",
        "inputs": {
            "filename_prefix": "output"       # <- 수정 대상: 고유 파일명
        }
    }
}

[SVD 워크플로우 예시]
{
    "1": {
        "class_type": "LoadImage",
        "inputs": {
            "image": "input.png"              # <- 수정 대상: 업로드된 이미지 경로
        }
    },
    "5": {
        "class_type": "SVD_img2vid_Conditioning",
        "inputs": {
            "motion_bucket_id": 127,          # <- 수정 대상: 움직임 강도 (1-255)
            "augmentation_level": 0.0
        }
    }
}

[노드 ID 찾는 방법]
1. ComfyUI에서 워크플로우 로드
2. 설정 > Enable Dev Mode 활성화
3. Save (API Format) 버튼 클릭
4. 저장된 JSON 파일에서 class_type으로 노드 검색
=============================================================================
"""

# 노드 타입별 기본 매핑 (실제 워크플로우에 따라 조정 필요)
DEFAULT_NODE_MAPPINGS = {
    WorkflowType.LTX_VIDEO: {
        "prompt_node_types": ["CLIPTextEncode", "LTXVGemmaEnhancePrompt"],
        "negative_prompt_node_types": ["CLIPTextEncode"],
        "sampler_node_types": ["KSampler", "SamplerCustom", "KSamplerAdvanced"],
        "save_node_types": ["SaveVideo", "SaveAnimatedWEBP", "VHS_VideoCombine"],
        "frame_count_node_types": ["PrimitiveInt", "EmptyLatentImage"],
    },
    WorkflowType.SVD: {
        "image_load_node_types": ["LoadImage"],
        "conditioning_node_types": ["SVD_img2vid_Conditioning"],
        "sampler_node_types": ["KSampler", "SamplerCustom"],
        "save_node_types": ["SaveVideo", "SaveAnimatedWEBP", "VHS_VideoCombine"],
    },
}


# =============================================================================
# ComfyVideoAgent - Main Class
# =============================================================================

class ComfyVideoAgent:
    """
    ComfyUI를 통한 비디오 생성 에이전트.

    WebSocket과 HTTP를 조합하여 ComfyUI API와 통신합니다.

    사용 예시:
    ```python
    config = ComfyConfig(
        host="127.0.0.1",
        port=8188,
        ltx_workflow_path=Path("workflows/ltx_video_api.json"),
    )

    agent = ComfyVideoAgent(config)
    agent.connect()

    try:
        result = agent.generate_video(
            workflow_type=WorkflowType.LTX_VIDEO,
            params=VideoGenerationParams(
                prompt="A serene mountain landscape at sunset",
                num_frames=97,
            )
        )
        print(f"Generated: {result.output_files}")
    finally:
        agent.disconnect()
    ```
    """

    def __init__(
        self,
        config: ComfyConfig,
        node_mappings: Optional[Dict[WorkflowType, JsonDict]] = None,
    ) -> None:
        """
        에이전트 초기화.

        Args:
            config: ComfyUI 서버 설정
            node_mappings: 커스텀 노드 ID 매핑 (워크플로우별)
        """
        self.config = config
        self.node_mappings = node_mappings or {}

        # 고유 클라이언트 ID (WebSocket과 HTTP 요청 간 공유 필수)
        self.client_id = str(uuid.uuid4())

        # WebSocket 연결
        self._ws: Optional[WebSocket] = None
        self._ws_connected = Event()
        self._ws_listener_thread: Optional[Thread] = None

        # 실행 상태 추적
        self._current_prompt_id: Optional[str] = None
        self._execution_status = ExecutionStatus.QUEUED
        self._execution_result: Optional[JsonDict] = None
        self._execution_error: Optional[str] = None
        self._output_data: List[JsonDict] = []

        # 진행률 콜백
        self._progress_callback: Optional[Callable[[int, int, str], None]] = None

        # 워크플로우 캐시
        self._workflow_cache: Dict[WorkflowType, JsonDict] = {}

        self.logger = logging.getLogger(self.__class__.__name__)

    # =========================================================================
    # Connection Management
    # =========================================================================

    def connect(self) -> bool:
        """
        WebSocket 연결 수립.

        중요: 이 메서드는 반드시 generate_video() 호출 전에 실행되어야 합니다.
        WebSocket이 연결되지 않으면 서버 응답을 수신할 수 없습니다.

        Returns:
            연결 성공 여부
        """
        if self._ws and self._ws.connected:
            self.logger.debug("WebSocket already connected")
            return True

        ws_url = f"{self.config.ws_url}?clientId={self.client_id}"
        self.logger.info(f"Connecting to WebSocket: {ws_url}")

        try:
            self._ws = create_connection(
                ws_url,
                timeout=self.config.connection_timeout,
            )
            self._ws_connected.set()

            # 백그라운드 리스너 시작
            self._start_ws_listener()

            self.logger.info(f"WebSocket connected (client_id: {self.client_id})")
            return True

        except WebSocketException as e:
            self.logger.error(f"WebSocket connection failed: {e}")
            return False

    def disconnect(self) -> None:
        """WebSocket 연결 종료."""
        self._ws_connected.clear()

        if self._ws:
            try:
                self._ws.close()
            except Exception as e:
                self.logger.warning(f"WebSocket close error: {e}")
            finally:
                self._ws = None

        if self._ws_listener_thread and self._ws_listener_thread.is_alive():
            self._ws_listener_thread.join(timeout=2.0)

        self.logger.info("WebSocket disconnected")

    def _start_ws_listener(self) -> None:
        """WebSocket 메시지 리스너 시작."""
        def listener():
            while self._ws_connected.is_set() and self._ws:
                try:
                    message = self._ws.recv()
                    if message:
                        self._handle_ws_message(json.loads(message))
                except WebSocketException:
                    if self._ws_connected.is_set():
                        self.logger.warning("WebSocket connection lost")
                    break
                except json.JSONDecodeError as e:
                    self.logger.warning(f"Invalid WebSocket message: {e}")

        self._ws_listener_thread = Thread(target=listener, daemon=True)
        self._ws_listener_thread.start()

    def _handle_ws_message(self, message: JsonDict) -> None:
        """
        WebSocket 메시지 처리.

        주요 메시지 타입:
        - status: 큐 상태 업데이트
        - execution_start: 실행 시작
        - executing: 노드 실행 중
        - progress: 진행률 (KSampler 등)
        - executed: 노드 실행 완료
        - execution_success: 전체 실행 성공
        - execution_error: 실행 오류
        """
        msg_type = message.get("type", "")
        data = message.get("data", {})

        # prompt_id 필터링 (다른 클라이언트의 메시지 무시)
        prompt_id = data.get("prompt_id", "")
        if prompt_id and prompt_id != self._current_prompt_id:
            return

        if msg_type == "execution_start":
            self._execution_status = ExecutionStatus.EXECUTING
            self.logger.debug(f"Execution started: {prompt_id}")

        elif msg_type == "executing":
            node_id = data.get("node")
            if node_id:
                self.logger.debug(f"Executing node: {node_id}")

        elif msg_type == "progress":
            # KSampler 등에서 발생하는 진행률
            current = data.get("value", 0)
            total = data.get("max", 1)
            node_id = data.get("node", "")

            if self._progress_callback:
                self._progress_callback(current, total, node_id)

            self.logger.debug(f"Progress: {current}/{total} (node: {node_id})")

        elif msg_type == "executed":
            # 노드 실행 완료 - 출력 데이터 수집
            node_id = data.get("node", "")
            output = data.get("output", {})

            if output:
                self._output_data.append({
                    "node_id": node_id,
                    "output": output,
                })
                self.logger.debug(f"Node executed: {node_id}, output keys: {list(output.keys())}")

        elif msg_type == "execution_success":
            self._execution_status = ExecutionStatus.COMPLETED
            self._execution_result = data
            self.logger.info(f"Execution completed: {prompt_id}")

        elif msg_type == "execution_error":
            self._execution_status = ExecutionStatus.FAILED
            self._execution_error = data.get("exception_message", "Unknown error")
            node_id = data.get("node_id", "")
            self.logger.error(f"Execution failed at node {node_id}: {self._execution_error}")

        elif msg_type == "execution_cached":
            # 캐시된 결과 사용 (시드 변경 안 했을 때)
            self.logger.warning("Execution used cached result - seed may not have been randomized")

    # =========================================================================
    # Video Generation
    # =========================================================================

    def generate_video(
        self,
        workflow_type: WorkflowType,
        params: VideoGenerationParams,
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
    ) -> GenerationResult:
        """
        비디오 생성 실행.

        Args:
            workflow_type: 워크플로우 유형 (LTX_VIDEO 또는 SVD)
            params: 생성 파라미터
            progress_callback: 진행률 콜백 (current, total, node_id)

        Returns:
            생성 결과
        """
        start_time = time.time()
        self._progress_callback = progress_callback

        # 상태 초기화
        self._execution_status = ExecutionStatus.QUEUED
        self._execution_result = None
        self._execution_error = None
        self._output_data = []

        try:
            # 1. WebSocket 연결 확인
            if not self._ws or not self._ws.connected:
                if not self.connect():
                    return GenerationResult(
                        success=False,
                        error_message="WebSocket connection failed"
                    )

            # 2. 워크플로우 로드 및 수정
            workflow = self._prepare_workflow(workflow_type, params)
            if not workflow:
                return GenerationResult(
                    success=False,
                    error_message="Failed to prepare workflow"
                )

            # 3. SVD의 경우 이미지 업로드
            if workflow_type == WorkflowType.SVD and params.source_image_path:
                upload_result = self._upload_image(params.source_image_path)
                if not upload_result:
                    return GenerationResult(
                        success=False,
                        error_message="Image upload failed"
                    )
                # 워크플로우에 업로드된 이미지 경로 주입
                self._inject_uploaded_image(workflow, upload_result)

            # 4. 프롬프트 전송
            prompt_id = self._queue_prompt(workflow)
            if not prompt_id:
                return GenerationResult(
                    success=False,
                    error_message="Failed to queue prompt"
                )

            self._current_prompt_id = prompt_id
            self.logger.info(f"Prompt queued: {prompt_id}")

            # 5. 실행 완료 대기
            success = self._wait_for_completion()

            # 6. 결과 처리
            if success:
                output_files = self._download_outputs(params.output_dir)
                execution_time = time.time() - start_time

                return GenerationResult(
                    success=True,
                    prompt_id=prompt_id,
                    output_files=output_files,
                    execution_time=execution_time,
                )
            else:
                return GenerationResult(
                    success=False,
                    prompt_id=prompt_id,
                    error_message=self._execution_error or "Execution failed",
                    execution_time=time.time() - start_time,
                )

        except Exception as e:
            self.logger.exception(f"Video generation failed: {e}")
            return GenerationResult(
                success=False,
                error_message=str(e),
                execution_time=time.time() - start_time,
            )

        finally:
            # VRAM 정리
            self._clear_vram()
            self._progress_callback = None

    def _prepare_workflow(
        self,
        workflow_type: WorkflowType,
        params: VideoGenerationParams,
    ) -> Optional[JsonDict]:
        """
        워크플로우 로드 및 파라미터 주입.

        Args:
            workflow_type: 워크플로우 유형
            params: 생성 파라미터

        Returns:
            수정된 워크플로우 또는 None
        """
        # 워크플로우 파일 경로 결정
        if workflow_type == WorkflowType.LTX_VIDEO:
            workflow_path = self.config.ltx_workflow_path
        else:
            workflow_path = self.config.svd_workflow_path

        if not workflow_path or not workflow_path.exists():
            self.logger.error(f"Workflow file not found: {workflow_path}")
            return None

        # 워크플로우 로드 (캐시 사용)
        if workflow_type not in self._workflow_cache:
            with open(workflow_path, "r", encoding="utf-8") as f:
                self._workflow_cache[workflow_type] = json.load(f)

        # 깊은 복사 (원본 보존)
        workflow = json.loads(json.dumps(self._workflow_cache[workflow_type]))

        # 노드 매핑 가져오기
        mappings = self.node_mappings.get(workflow_type) or DEFAULT_NODE_MAPPINGS.get(workflow_type, {})

        # 파라미터 주입
        if workflow_type == WorkflowType.LTX_VIDEO:
            self._inject_ltx_params(workflow, params, mappings)
        else:
            self._inject_svd_params(workflow, params, mappings)

        # 공통: 시드 랜덤화 (중요!)
        self._randomize_seeds(workflow, params.seed, mappings)

        # 공통: 출력 파일명 설정
        self._set_output_prefix(workflow, params.output_prefix, mappings)

        return workflow

    def _inject_ltx_params(
        self,
        workflow: JsonDict,
        params: VideoGenerationParams,
        mappings: JsonDict,
    ) -> None:
        """LTX-Video 파라미터 주입."""
        prompt_types = mappings.get("prompt_node_types", [])
        negative_types = mappings.get("negative_prompt_node_types", [])

        for node_id, node_data in workflow.items():
            class_type = node_data.get("class_type", "")
            inputs = node_data.get("inputs", {})

            # 프롬프트 주입
            if class_type in prompt_types and "text" in inputs:
                # 첫 번째 프롬프트 노드에만 주입 (positive)
                if params.prompt and class_type in prompt_types:
                    inputs["text"] = params.prompt
                    self.logger.debug(f"Injected prompt to node {node_id} ({class_type})")

            # 네거티브 프롬프트 주입 (보통 두 번째 CLIPTextEncode)
            # 실제 구현 시 노드 연결 관계 분석 필요

            # 프레임 수 주입
            if class_type in mappings.get("frame_count_node_types", []):
                if "value" in inputs:
                    inputs["value"] = params.num_frames
                elif "batch_size" in inputs:  # EmptyLatentImage
                    inputs["batch_size"] = params.num_frames

    def _inject_svd_params(
        self,
        workflow: JsonDict,
        params: VideoGenerationParams,
        mappings: JsonDict,
    ) -> None:
        """SVD 파라미터 주입."""
        conditioning_types = mappings.get("conditioning_node_types", [])

        for node_id, node_data in workflow.items():
            class_type = node_data.get("class_type", "")
            inputs = node_data.get("inputs", {})

            # SVD 컨디셔닝 주입
            if class_type in conditioning_types:
                if "motion_bucket_id" in inputs:
                    inputs["motion_bucket_id"] = params.motion_bucket_id
                    self.logger.debug(f"Injected motion_bucket_id={params.motion_bucket_id} to node {node_id}")

                if "augmentation_level" in inputs:
                    inputs["augmentation_level"] = params.augmentation_level

    def _randomize_seeds(
        self,
        workflow: JsonDict,
        fixed_seed: Optional[int],
        mappings: JsonDict,
    ) -> None:
        """
        시드 랜덤화.

        중요: ComfyUI는 동일한 시드로 실행 시 캐시된 결과를 반환합니다.
        매 실행마다 시드를 변경해야 새로운 결과를 얻을 수 있습니다.
        """
        sampler_types = mappings.get("sampler_node_types", ["KSampler", "SamplerCustom"])

        # 시드 결정
        seed = fixed_seed if fixed_seed is not None else random.randint(1, 10**14)

        for node_id, node_data in workflow.items():
            class_type = node_data.get("class_type", "")
            inputs = node_data.get("inputs", {})

            if class_type in sampler_types and "seed" in inputs:
                inputs["seed"] = seed
                self.logger.debug(f"Set seed={seed} for node {node_id} ({class_type})")

    def _set_output_prefix(
        self,
        workflow: JsonDict,
        prefix: str,
        mappings: JsonDict,
    ) -> None:
        """출력 파일명 접두사 설정."""
        save_types = mappings.get("save_node_types", ["SaveVideo", "SaveAnimatedWEBP"])

        for node_id, node_data in workflow.items():
            class_type = node_data.get("class_type", "")
            inputs = node_data.get("inputs", {})

            if class_type in save_types and "filename_prefix" in inputs:
                inputs["filename_prefix"] = prefix
                self.logger.debug(f"Set filename_prefix={prefix} for node {node_id}")

    def _inject_uploaded_image(
        self,
        workflow: JsonDict,
        upload_result: JsonDict,
    ) -> None:
        """업로드된 이미지 경로를 워크플로우에 주입."""
        # 업로드 API 응답: {"name": "filename.png", "subfolder": "", "type": "input"}
        image_name = upload_result.get("name", "")
        subfolder = upload_result.get("subfolder", "")

        if subfolder:
            image_path = f"{subfolder}/{image_name}"
        else:
            image_path = image_name

        for node_id, node_data in workflow.items():
            if node_data.get("class_type") == "LoadImage":
                node_data["inputs"]["image"] = image_path
                self.logger.debug(f"Injected image path '{image_path}' to LoadImage node {node_id}")
                break

    # =========================================================================
    # HTTP API Methods
    # =========================================================================

    def _queue_prompt(self, workflow: JsonDict) -> Optional[str]:
        """
        프롬프트 큐에 추가.

        Args:
            workflow: 수정된 워크플로우

        Returns:
            prompt_id 또는 None
        """
        url = f"{self.config.http_base_url}/prompt"

        payload = {
            "prompt": workflow,
            "client_id": self.client_id,
        }

        try:
            response = requests.post(
                url,
                json=payload,
                timeout=self.config.connection_timeout,
            )
            response.raise_for_status()

            result = response.json()
            return result.get("prompt_id")

        except requests.RequestException as e:
            self.logger.error(f"Failed to queue prompt: {e}")
            return None

    def _upload_image(self, image_path: Path) -> Optional[JsonDict]:
        """
        SVD용 이미지 업로드.

        Args:
            image_path: 업로드할 이미지 경로

        Returns:
            업로드 결과 {"name": "...", "subfolder": "...", "type": "input"} 또는 None
        """
        url = f"{self.config.http_base_url}/upload/image"

        if not image_path.exists():
            self.logger.error(f"Image file not found: {image_path}")
            return None

        try:
            with open(image_path, "rb") as f:
                files = {
                    "image": (image_path.name, f, "image/png"),
                }
                data = {
                    "overwrite": "true",
                }

                response = requests.post(
                    url,
                    files=files,
                    data=data,
                    timeout=self.config.connection_timeout,
                )
                response.raise_for_status()

                result = response.json()
                self.logger.info(f"Image uploaded: {result}")
                return result

        except requests.RequestException as e:
            self.logger.error(f"Image upload failed: {e}")
            return None

    def _clear_vram(self) -> None:
        """
        VRAM 정리.

        비디오 생성은 많은 VRAM을 사용하므로, 작업 완료 후 모델을 언로드합니다.
        """
        url = f"{self.config.http_base_url}/free"

        try:
            response = requests.post(
                url,
                json={"unload_models": True},
                timeout=self.config.connection_timeout,
            )

            if response.status_code == 200:
                self.logger.info("VRAM cleared successfully")
            else:
                self.logger.warning(f"VRAM clear returned status {response.status_code}")

        except requests.RequestException as e:
            self.logger.warning(f"Failed to clear VRAM: {e}")

    def _wait_for_completion(self) -> bool:
        """
        실행 완료 대기.

        Returns:
            성공 여부
        """
        start_time = time.time()
        timeout = self.config.execution_timeout

        while time.time() - start_time < timeout:
            if self._execution_status == ExecutionStatus.COMPLETED:
                return True

            if self._execution_status == ExecutionStatus.FAILED:
                return False

            time.sleep(0.1)

        self.logger.error(f"Execution timeout after {timeout} seconds")
        self._execution_status = ExecutionStatus.FAILED
        self._execution_error = "Execution timeout"
        return False

    def _download_outputs(self, output_dir: Optional[Path] = None) -> List[Path]:
        """
        생성된 파일 다운로드.

        Args:
            output_dir: 저장 디렉토리 (None이면 현재 디렉토리)

        Returns:
            다운로드된 파일 경로 목록
        """
        downloaded_files: List[Path] = []
        output_dir = output_dir or Path.cwd()
        output_dir.mkdir(parents=True, exist_ok=True)

        for output_entry in self._output_data:
            output = output_entry.get("output", {})

            # 비디오 출력 처리
            for key in ["videos", "gifs", "images"]:
                items = output.get(key, [])

                for item in items:
                    if isinstance(item, dict):
                        filename = item.get("filename", "")
                        subfolder = item.get("subfolder", "")
                        file_type = item.get("type", "output")

                        if filename:
                            downloaded = self._download_file(
                                filename=filename,
                                subfolder=subfolder,
                                file_type=file_type,
                                output_dir=output_dir,
                            )
                            if downloaded:
                                downloaded_files.append(downloaded)

        return downloaded_files

    def _download_file(
        self,
        filename: str,
        subfolder: str,
        file_type: str,
        output_dir: Path,
    ) -> Optional[Path]:
        """
        단일 파일 다운로드.

        Args:
            filename: 파일명
            subfolder: 하위 폴더
            file_type: 파일 타입 (output, input, temp)
            output_dir: 저장 디렉토리

        Returns:
            저장된 파일 경로 또는 None
        """
        url = f"{self.config.http_base_url}/view"

        params = {
            "filename": filename,
            "subfolder": subfolder,
            "type": file_type,
        }

        try:
            response = requests.get(
                url,
                params=params,
                timeout=self.config.connection_timeout,
                stream=True,
            )
            response.raise_for_status()

            output_path = output_dir / filename

            with open(output_path, "wb") as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)

            self.logger.info(f"Downloaded: {output_path}")
            return output_path

        except requests.RequestException as e:
            self.logger.error(f"Failed to download {filename}: {e}")
            return None

    # =========================================================================
    # Utility Methods
    # =========================================================================

    def get_system_stats(self) -> Optional[JsonDict]:
        """시스템 상태 조회."""
        url = f"{self.config.http_base_url}/system_stats"

        try:
            response = requests.get(url, timeout=self.config.connection_timeout)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            self.logger.error(f"Failed to get system stats: {e}")
            return None

    def get_queue_status(self) -> Optional[JsonDict]:
        """큐 상태 조회."""
        url = f"{self.config.http_base_url}/queue"

        try:
            response = requests.get(url, timeout=self.config.connection_timeout)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            self.logger.error(f"Failed to get queue status: {e}")
            return None

    def cancel_current(self) -> bool:
        """현재 실행 취소."""
        url = f"{self.config.http_base_url}/interrupt"

        try:
            response = requests.post(url, timeout=self.config.connection_timeout)
            self._execution_status = ExecutionStatus.CANCELLED
            return response.status_code == 200
        except requests.RequestException as e:
            self.logger.error(f"Failed to cancel execution: {e}")
            return False

    def clear_queue(self) -> bool:
        """큐 비우기."""
        url = f"{self.config.http_base_url}/queue"

        try:
            response = requests.post(
                url,
                json={"clear": True},
                timeout=self.config.connection_timeout,
            )
            return response.status_code == 200
        except requests.RequestException as e:
            self.logger.error(f"Failed to clear queue: {e}")
            return False


# =============================================================================
# Async Wrapper (Optional)
# =============================================================================

class AsyncComfyVideoAgent:
    """
    비동기 래퍼.

    asyncio 환경에서 ComfyVideoAgent를 사용하기 위한 래퍼 클래스.
    실제 작업은 스레드 풀에서 실행됩니다.
    """

    def __init__(self, config: ComfyConfig) -> None:
        self._sync_agent = ComfyVideoAgent(config)

    async def connect(self) -> bool:
        import asyncio
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._sync_agent.connect)

    async def disconnect(self) -> None:
        import asyncio
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._sync_agent.disconnect)

    async def generate_video(
        self,
        workflow_type: WorkflowType,
        params: VideoGenerationParams,
    ) -> GenerationResult:
        import asyncio
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None,
            self._sync_agent.generate_video,
            workflow_type,
            params,
            None,  # progress_callback은 스레드 안전하지 않음
        )

    async def clear_vram(self) -> None:
        import asyncio
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._sync_agent._clear_vram)


# =============================================================================
# Module Exports
# =============================================================================

__all__ = [
    # Enums
    "WorkflowType",
    "ExecutionStatus",
    # Config
    "ComfyConfig",
    "VideoGenerationParams",
    "GenerationResult",
    # Main Classes
    "ComfyVideoAgent",
    "AsyncComfyVideoAgent",
]
