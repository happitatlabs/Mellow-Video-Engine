"""
Image Service 스키마: ImageStatus, ImageResult, ImageGenerationError, 상수.

ComfyUI 연동용 타입 및 상수 정의.
"""
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Union

# SVD 호환: 해상도 고정
MAGIC_WIDTH = 1216
MAGIC_HEIGHT = 704


class ImageStatus(Enum):
    """Image service status."""

    DISCONNECTED = auto()
    CONNECTED = auto()
    GENERATING = auto()
    QUEUED = auto()
    ERROR = auto()


@dataclass
class ImageResult:
    """
    Result structure from image generation.

    Attributes:
        images: List of generated image paths
        prompt_id: ComfyUI prompt ID
        generation_time_ms: Total generation time
        seed_used: Actual seed used (if random)
        workflow_used: Workflow that was executed
    """

    images: List[Path]
    prompt_id: str
    generation_time_ms: float = 0.0
    seed_used: int = 0
    workflow_used: str = ""
    node_outputs: Dict[str, Any] = field(default_factory=dict)


class ImageGenerationError(Exception):
    """Exception for image generation failures."""

    pass


ProgressCallback = Union[
    Callable[[float, str], None],
    Callable[[float, str], Awaitable[None]],
]
