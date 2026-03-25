"""
Image Service 워크플로우: 프롬프트 주입, 해상도 override, fallback 빌더.

ComfyUI 워크플로우 준비 및 프롬프트 치환 로직.
"""
import logging
import random
from datetime import datetime
from typing import Any, Dict

from mellow_link.media.schemas import ImageRequest

from .image_schemas import ImageGenerationError, MAGIC_HEIGHT, MAGIC_WIDTH

logger = logging.getLogger(__name__)


def require_str(label: str, value: Any, *, allow_none: bool = False) -> str:
    """
    (CRITICAL) 프롬프트 주입 가드: 값이 string이 아니면 즉시 에러.
    """
    if value is None and allow_none:
        return ""
    if not isinstance(value, str):
        raise TypeError(
            f"[ImageService] {label} must be str, got {type(value).__name__}: {value!r}"
        )
    return value


def pick_static_prompt(request: ImageRequest) -> str:
    """프롬프트 이원화: static_prompt 우선."""
    sp = getattr(request, "static_prompt", None)
    if sp is not None and not isinstance(sp, str):
        raise TypeError(
            f"[ImageService] static_prompt must be str|None, got {type(sp).__name__}: {sp!r}"
        )
    if isinstance(sp, str) and sp.strip():
        return sp.strip()
    p = getattr(request, "prompt", None)
    p = require_str("prompt", p, allow_none=False)
    return p.strip()


def prepare_workflow(
    workflow: Dict[str, Any], *, prompt_text: str, negative_text: str
) -> None:
    """
    (CRITICAL) %PROMPT% 치환: 워크플로우 내 토큰 치환.
    치환 후에도 토큰이 남아있으면 즉시 에러.
    """
    prompt_text = require_str("prompt_text", prompt_text, allow_none=False)
    negative_text = require_str("negative_text", negative_text, allow_none=True)

    token_map = {
        "%PROMPT%": prompt_text,
        "%NEG_PROMPT%": negative_text,
        "%NEGATIVE_PROMPT%": negative_text,
    }

    def _walk(obj: Any) -> Any:
        if isinstance(obj, str):
            s = obj
            for tok, rep in token_map.items():
                if tok in s:
                    s = s.replace(tok, rep)
            return s
        if isinstance(obj, list):
            return [_walk(v) for v in obj]
        if isinstance(obj, dict):
            for k, v in list(obj.items()):
                obj[k] = _walk(v)
            return obj
        return obj

    _walk(workflow)

    def _contains_token(obj: Any, token: str) -> bool:
        if isinstance(obj, str):
            return token in obj
        if isinstance(obj, list):
            return any(_contains_token(v, token) for v in obj)
        if isinstance(obj, dict):
            return any(_contains_token(v, token) for v in obj.values())
        return False

    if _contains_token(workflow, "%PROMPT%"):
        raise ImageGenerationError(
            "[ImageService] Workflow still contains unreplaced %PROMPT% token after injection"
        )


def override_resolution(workflow: Dict[str, Any]) -> None:
    """
    MAGIC_NUMBER: 1216x704 고정.
    워크플로우 내 width/height 입력을 전부 덮어쓴다.
    """
    w = int(MAGIC_WIDTH)
    h = int(MAGIC_HEIGHT)
    for _nid, node in workflow.items():
        if not isinstance(node, dict):
            continue
        inputs = node.get("inputs")
        if not isinstance(inputs, dict):
            continue
        if "width" in inputs:
            inputs["width"] = w
        if "height" in inputs:
            inputs["height"] = h


def inject_prompts_into_workflow(workflow: Dict[str, Any], request: ImageRequest) -> None:
    """
    워크플로우 노드에 프롬프트/시드 주입 후 prepare_workflow 호출.
    """
    static_prompt = pick_static_prompt(request)
    negative_text = require_str(
        "negative_prompt",
        getattr(request, "negative_prompt", None),
        allow_none=True,
    )

    for _key, value in workflow.items():
        if "inputs" not in value:
            continue

        class_type = value.get("class_type", "")
        meta_title = value.get("_meta", {}).get("title", "")

        if class_type == "CLIPTextEncodeFlux":
            if "Positive" in meta_title:
                value["inputs"]["clip_l"] = static_prompt
                value["inputs"]["t5xxl"] = static_prompt
                logger.debug("[ImageService] Flux Positive injected")
            elif "Negative" in meta_title:
                value["inputs"]["clip_l"] = negative_text
                value["inputs"]["t5xxl"] = negative_text
                logger.debug("[ImageService] Flux Negative injected")

        elif class_type == "CLIPTextEncode":
            if "Positive" in meta_title or meta_title == "CLIP Text Encode (Prompt)":
                value["inputs"]["text"] = static_prompt
                logger.debug("[ImageService] SD Positive injected")
            elif "Negative" in meta_title:
                value["inputs"]["text"] = negative_text
                logger.debug("[ImageService] SD Negative injected")

        if class_type == "KSampler":
            if request.seed == -1:
                seed_val = random.randint(0, 2**32 - 1)
                value["inputs"]["seed"] = seed_val
                logger.info("[ImageService] random seed generated: %s", seed_val)
            else:
                value["inputs"]["seed"] = request.seed

    prepare_workflow(workflow, prompt_text=static_prompt, negative_text=negative_text)


def build_prompt(request: ImageRequest) -> Dict[str, Any]:
    """
    Fallback: ComfyUI txt2img 워크플로우를 코드로 생성.
    """
    seed = request.seed
    if seed < 0:
        seed = random.randint(0, 2**32 - 1)

    static_prompt = pick_static_prompt(request)

    prompt = {
        "3": {
            "class_type": "KSampler",
            "inputs": {
                "seed": seed,
                "steps": request.steps,
                "cfg": request.cfg_scale,
                "sampler_name": request.sampler_name,
                "scheduler": request.scheduler,
                "denoise": request.denoise,
                "model": ["4", 0],
                "positive": ["6", 0],
                "negative": ["7", 0],
                "latent_image": ["5", 0],
            },
        },
        "4": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {
                "ckpt_name": request.model or "flux1-dev-fp8.safetensors"
            },
        },
        "5": {
            "class_type": "EmptyLatentImage",
            "inputs": {
                "width": request.width,
                "height": request.height,
                "batch_size": request.batch_size,
            },
        },
        "6": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": static_prompt, "clip": ["4", 1]},
        },
        "7": {
            "class_type": "CLIPTextEncode",
            "inputs": {
                "text": request.negative_prompt or "",
                "clip": ["4", 1],
            },
        },
        "8": {
            "class_type": "VAEDecode",
            "inputs": {"samples": ["3", 0], "vae": ["4", 2]},
        },
        "9": {
            "class_type": "SaveImage",
            "inputs": {
                "filename_prefix": f"mellow_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
                "images": ["8", 0],
            },
        },
    }

    return prompt
