"""
Mellow-Link - Media Generation Router

Endpoints: /generate-image
"""

import logging

from fastapi import APIRouter, HTTPException, Depends

from mellow_link import app_state
from mellow_link.core import SystemState, TransitionResult, ImageRequest
from mellow_link.dependencies import get_current_user_optional

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/generate-image", tags=["Image"])
async def generate_image(
    request: ImageRequest,
    user=Depends(get_current_user_optional),
):
    """
    Generate an image using ComfyUI.

    Delegates to Orchestrator which manages GPU state transitions.
    Blocks until image generation is complete.
    """
    if not app_state.image_service or not app_state.image_service.is_available():
        raise HTTPException(status_code=503, detail="Image Service unavailable")
    if not app_state.orchestrator:
        raise HTTPException(status_code=503, detail="Orchestrator not initialized")

    result = await app_state.orchestrator.request_state_change(
        SystemState.IMAGE,
        reason="Image generation request"
    )

    if result == TransitionResult.INVALID_TRANSITION:
        raise HTTPException(
            status_code=409,
            detail=f"Cannot generate image: system in {app_state.orchestrator.get_state().name} state"
        )

    try:
        img_request = ImageRequest(
            prompt=request.prompt,
            negative_prompt=request.negative_prompt,
            width=request.width,
            height=request.height,
            steps=request.steps,
            cfg_scale=request.cfg_scale,
            seed=request.seed,
            model=request.model or (app_state.settings.default_checkpoint if app_state.settings else "")
        )

        img_result = await app_state.image_service.generate(img_request)

        return {
            "success": True,
            "images": [str(p) for p in img_result.images],
            "prompt_id": img_result.prompt_id,
            "seed": img_result.seed_used,
            "duration_ms": img_result.generation_time_ms
        }

    except TimeoutError as e:
        raise HTTPException(status_code=504, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        await app_state.orchestrator.request_state_change(
            SystemState.IDLE, reason="Image generation complete"
        )
