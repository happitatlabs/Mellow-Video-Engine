"""
Runtime prompt policy helpers for the maintained planner path.

This module centralizes:
- loading prompt policy from config/prompts.yaml
- runtime system prompt resolution
- no-human enforcement
- planner scene sanitation/validation
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Tuple

from mellow_link.services.runtime_config import get_video_generation_settings, load_prompts_config


logger = logging.getLogger(__name__)

HUMAN_TERMS = {
    "human", "humans", "person", "people", "man", "woman", "child", "children",
    "baby", "figure", "figures", "face", "faces", "body", "hands", "fingers",
    "arms", "legs", "feet", "artist", "guitarist", "dancer", "dancers", "band",
    "singer", "performer", "soloist", "musician", "male", "female", "crowd",
    "pedestrian", "tourist", "portrait", "selfie", "silhouette", "character",
    "actor", "actress", "boy", "girl", "vocalist", "singer-songwriter",
    "singer songwriter", "frontman", "frontwoman", "bandmate", "bystander",
    "passenger", "driver", "runner", "walker", "model",
}

HUMAN_REPLACEMENTS = {
    "lone figure": "solitary silhouette-free landscape",
    "human silhouette": "misty backlit ridge",
    "silhouette": "shape of distant fog",
    "figure": "empty landscape",
    "guitarist": "abandoned guitar on stage",
    "guitar player": "abandoned guitar on stage",
    "artist": "empty performance space",
    "dancers": "patterns of light",
    "dancer": "patterns of light",
    "soloist": "single spotlight in an empty room",
    "band": "empty stage with instruments",
    "singer": "empty microphone stand",
    "performer": "empty performance space",
    "performers": "empty performance space",
    "portrait": "empty close-up composition",
    "selfie": "empty reflective surface",
    "male": "",
    "female": "",
    "boy": "",
    "girl": "",
    "vocalist": "empty microphone stand",
    "singer-songwriter": "empty stage with scattered lyric sheets",
    "singer songwriter": "empty stage with scattered lyric sheets",
    "frontman": "empty stage center",
    "frontwoman": "empty stage center",
    "bandmate": "instrument cases near an empty stage",
    "bystander": "empty background",
    "passenger": "empty seat by the window",
    "driver": "parked vehicle interior without occupants",
    "runner": "wind across an empty path",
    "walker": "empty path through drifting fog",
    "model": "still-life composition",
    "hand enters frame": "curtain stirs near the frame edge",
    "hands enter frame": "curtain stirs near the frame edge",
    "hands": "fabric folds",
    "fingers": "fabric edges",
    "people": "empty surroundings",
    "person": "empty surroundings",
    "human": "non-human",
}

POLICY_ALLOW_PHRASES = {
    "no humans",
    "empty of people",
    "no people",
    "non-human",
}


def planner_policy() -> Dict[str, Any]:
    prompts = load_prompts_config()
    visual = prompts.get("visual_planning", {}) if isinstance(prompts, dict) else {}
    if not visual:
        logger.warning("[prompt_policy] visual_planning policy missing in config/prompts.yaml. Using built-in fallback behavior.")
    return visual if isinstance(visual, dict) else {}


def build_runtime_system_prompt(default_prompt: str) -> str:
    policy = planner_policy()
    configured = str(policy.get("system_prompt") or "").strip()
    return configured or default_prompt


def extract_no_human_policy() -> Tuple[List[str], str]:
    policy = planner_policy()
    nh = policy.get("no_human_policy", {}) if isinstance(policy, dict) else {}
    additions = nh.get("positive_additions", []) if isinstance(nh, dict) else []
    positive = [str(item).strip() for item in additions if str(item).strip()]
    negative = str(nh.get("negative_prompt_base") or "").strip() if isinstance(nh, dict) else ""
    return positive, negative


def merge_csv_parts(*parts: str) -> str:
    out: List[str] = []
    seen = set()
    for part in parts:
        for token in [p.strip() for p in str(part or "").split(",") if p.strip()]:
            key = token.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(token)
    return ", ".join(out)


def contains_human_terms(text: str) -> bool:
    lowered = (text or "").lower()
    for term in HUMAN_TERMS:
        if re.search(rf"\b{re.escape(term.lower())}\b", lowered):
            return True
    return False


def neutralize_human_terms(text: str) -> str:
    cleaned = str(text or "")
    for old, new in HUMAN_REPLACEMENTS.items():
        cleaned = re.sub(re.escape(old), new, cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ,")
    return cleaned


def sanitize_scene_payload(scene: Dict[str, Any]) -> Dict[str, Any]:
    positive_additions, negative_base = extract_no_human_policy()
    current_static = str(scene.get("static_scene_description", "") or "").strip()
    current_dynamic = str(scene.get("dynamic_action_description", "") or "").strip()
    original_static = current_static
    original_dynamic = current_dynamic
    original_negative = str(scene.get("negative_prompt", "") or "").strip()

    if contains_human_terms(current_static):
        current_static = neutralize_human_terms(current_static)
    if contains_human_terms(current_dynamic):
        current_dynamic = neutralize_human_terms(current_dynamic)

    static_prompt = merge_csv_parts(
        "cinematic music video still",
        current_static,
        ", ".join(positive_additions),
        "soft lighting",
        "high quality",
        "16:9 composition",
    )
    video_runtime = get_video_generation_settings()
    if bool(video_runtime.get("locked_camera_mode", False)) and str(video_runtime.get("locked_camera_backend", "")).strip().lower() == "ambient_loop":
        motion_prompt = merge_csv_parts(
            "ambient loop direction",
            current_dynamic,
            "localized motion emphasis",
            "background frame stays steady",
            "seamless loop",
            ", ".join(positive_additions),
        )
    else:
        motion_prompt = merge_csv_parts(
            "cinematic music video",
            current_dynamic,
            "consistent color",
            "no flicker",
            ", ".join(positive_additions),
        )
    negative_prompt = merge_csv_parts(str(scene.get("negative_prompt", "") or ""), negative_base)

    scene["static_scene_description"] = current_static
    scene["dynamic_action_description"] = current_dynamic
    scene["static_prompt"] = static_prompt
    scene["motion_prompt"] = motion_prompt
    scene["negative_prompt"] = negative_prompt
    scene["visual_prompt"] = static_prompt
    scene["policy_inputs"] = {
        "pre_policy_static_scene_description": original_static,
        "pre_policy_dynamic_action_description": original_dynamic,
        "pre_policy_negative_prompt": original_negative,
        "semantic_subject_type": str(dict(scene.get("semantic_scene") or {}).get("subject", {}).get("type", "")),
    }
    scene["policy_outputs"] = {
        "post_policy_static_scene_description": current_static,
        "post_policy_dynamic_action_description": current_dynamic,
        "post_policy_negative_prompt": negative_prompt,
    }
    scene["policy_flags"] = {
        "contains_human_terms_static": contains_human_terms(str(scene.get("static_scene_description", ""))),
        "contains_human_terms_dynamic": contains_human_terms(str(scene.get("dynamic_action_description", ""))),
        "policy_source": "config/prompts.yaml",
        "fail_safe_applied": bool(scene.get("policy_flags", {}).get("fail_safe_applied", False)) if isinstance(scene.get("policy_flags"), dict) else False,
    }
    return scene


def validate_scene_payload(scene: Dict[str, Any]) -> Dict[str, Any]:
    issues: List[str] = []
    static_prompt = str(scene.get("static_prompt", "") or "")
    motion_prompt = str(scene.get("motion_prompt", "") or "")
    for phrase in POLICY_ALLOW_PHRASES:
        static_prompt = re.sub(re.escape(phrase), "", static_prompt, flags=re.IGNORECASE)
        motion_prompt = re.sub(re.escape(phrase), "", motion_prompt, flags=re.IGNORECASE)

    if contains_human_terms(static_prompt):
        issues.append("human_term_in_static_prompt")
    if contains_human_terms(motion_prompt):
        issues.append("human_term_in_motion_prompt")
    if not str(scene.get("negative_prompt", "") or "").strip():
        issues.append("missing_negative_prompt")

    validation = {
        "ok": not issues,
        "issues": issues,
        "policy_level": "best_effort_sanitized",
    }
    scene["policy_validation"] = validation
    return scene


def downgrade_scene_to_safe_empty_environment(scene: Dict[str, Any]) -> Dict[str, Any]:
    safe_scene = dict(scene)
    positive_additions, negative_base = extract_no_human_policy()
    original_static = str(safe_scene.get("static_scene_description", "") or "")
    original_dynamic = str(safe_scene.get("dynamic_action_description", "") or "")
    safe_scene["static_scene_description"] = (
        "empty atmospheric interior, soft side lighting, dust in the air, "
        "unoccupied set, shallow depth of field"
    )
    safe_scene["dynamic_action_description"] = (
        "slow cinematic push through an empty environment, drifting haze, "
        "subtle curtain movement, stable exposure"
    )
    safe_scene["static_prompt"] = merge_csv_parts(
        "cinematic music video still",
        safe_scene["static_scene_description"],
        ", ".join(positive_additions),
        "soft lighting",
        "high quality",
        "16:9 composition",
    )
    safe_scene["motion_prompt"] = merge_csv_parts(
        "cinematic music video",
        safe_scene["dynamic_action_description"],
        "consistent color",
        "no flicker",
        ", ".join(positive_additions),
    )
    safe_scene["visual_prompt"] = safe_scene["static_prompt"]
    safe_scene["negative_prompt"] = merge_csv_parts(str(safe_scene.get("negative_prompt", "") or ""), negative_base)
    policy_outputs = dict(safe_scene.get("policy_outputs") or {})
    policy_outputs.update(
        {
            "post_policy_static_scene_description": safe_scene["static_scene_description"],
            "post_policy_dynamic_action_description": safe_scene["dynamic_action_description"],
            "post_policy_negative_prompt": safe_scene["negative_prompt"],
            "downgraded_from_static_scene_description": original_static,
            "downgraded_from_dynamic_action_description": original_dynamic,
        }
    )
    safe_scene["policy_outputs"] = policy_outputs
    flags = dict(safe_scene.get("policy_flags") or {})
    flags["fail_safe_applied"] = True
    flags["fail_safe_reason"] = "human_terms_detected_after_sanitization"
    safe_scene["policy_flags"] = flags
    validation = dict(safe_scene.get("policy_validation") or {})
    validation["ok"] = True
    validation["policy_level"] = "fail_safe_downgraded"
    validation["issues"] = []
    validation["original_issues"] = list(dict(scene.get("policy_validation") or {}).get("issues") or [])
    safe_scene["policy_validation"] = validation
    return safe_scene


def enforce_scene_policy(scene: Dict[str, Any]) -> Dict[str, Any]:
    sanitized = sanitize_scene_payload(scene)
    validated = validate_scene_payload(sanitized)
    if validated.get("policy_validation", {}).get("ok"):
        return validated
    return downgrade_scene_to_safe_empty_environment(validated)
