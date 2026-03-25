"""
Visual Planner (Story & Context)

목표:
  - 전체 가사(lyrics)를 "장면(Scenes)" 단위로 변환한다.
  - 각 장면은:
      - 정적(Image) 프롬프트: 피사체/배경/스타일 중심 (Static Prompt)
      - 모션(Video) 프롬프트: 카메라 워킹/움직임 중심 (Motion Prompt)
    으로 이원화한다.
  - 전 장면이 일관된 스타일을 유지하도록 seed를 공유한다.

NOTE:
  - LLM(예: Ollama)이 없거나 느린 환경에서도 동작하도록, 기본은 규칙 기반(휴리스틱)으로 동작.
  - 추후 LLM을 붙이기 쉬운 구조(메서드/필드)를 유지.
"""

from __future__ import annotations

import asyncio
import json
import re
import random
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from mellow_link.services.prompt_policy import (
    build_runtime_system_prompt,
    enforce_scene_policy,
)
from mellow_link.services.semantic_scene_extractor import extract_semantic_scenes


_KOREAN_STOPWORDS = {
    "그", "이", "저", "것", "수", "듯", "처럼", "보다",
    "그리고", "하지만", "그래서", "또", "더", "가", "이", "을", "를", "은", "는", "에", "의", "와", "과",
    "에서", "에게", "한테", "으로", "로", "만", "도", "까지", "부터", "마저", "조차",
}


def _tokenize(text: str) -> List[str]:
    # 단순 토크나이저: 한/영/숫자 단어 추출
    words = re.findall(r"[A-Za-z0-9가-힣]{2,}", text or "")
    out: List[str] = []
    for w in words:
        ww = w.strip()
        if not ww:
            continue
        if ww in _KOREAN_STOPWORDS:
            continue
        out.append(ww)
    return out


def _unique_preserve(seq: Iterable[str]) -> List[str]:
    seen = set()
    out = []
    for s in seq:
        if s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out


def _pick_motion_bucket(camera_hint: str) -> int:
    """
    카메라 워킹 힌트 -> motion_bucket_id 매핑(대략).
    """
    h = (camera_hint or "").lower()
    if "static" in h or "locked" in h:
        return 16
    if "slow" in h and ("zoom" in h or "push" in h):
        return 80
    if "pan" in h:
        return 110
    if "handheld" in h or "dynamic" in h or "run" in h:
        return 170
    return 127


def _infer_camera_and_motion(line: str) -> Tuple[str, str]:
    """
    가사 한 줄에서 "카메라/모션"을 추론(휴리스틱).
    """
    t = (line or "").lower()
    # 한국어 동사/키워드 기반
    dynamic_markers = ("달려", "뛰", "춤", "폭풍", "바람", "울부", "불꽃", "전쟁", "추격", "격렬", "소용돌이")
    calm_markers = ("기억", "그리", "눈물", "조용", "새벽", "밤", "달", "별", "고요", "따뜻", "포근")

    if any(m in t for m in dynamic_markers):
        return ("static locked shot", "ambient motion only, wind through the environment, cloth flutter, gentle parallax, seamless loop")
    if any(m in t for m in calm_markers):
        return ("static locked shot", "subtle ambient motion, light haze, gentle fabric motion, seamless loop")
    # 기본값: 고정 프레임
    return ("static locked shot", "subtle ambient motion, soft light fluctuation, seamless loop")


@dataclass(frozen=True)
class PlannerConfig:
    max_scenes: int = 20
    # 🎯 The Magic Number (SVD 호환)
    width: int = 1216
    height: int = 704


# =============================================================================
# LLM Persona (The Cinematographer)
# =============================================================================

DEFAULT_CINEMATOGRAPHER_SYSTEM_PROMPT = """
당신은 추상적인 노래 가사를 시각적이고 영화적인 촬영 지시서로 변환하는 노련한 시네마토그래퍼입니다.

규칙:
- "가사를 그대로 쓰지 마라." 가사의 문장을 그대로 복사/인용하지 마라.
- 가사의 정서를 읽고, '거울을 닦는 손'처럼 구체적인 사물/공간/행위로 번역하라.
- 사용자가 제공한 가사 구간(segments) 각각에 대해, 반드시 아래 3개 필드를 분리하여 생성하십시오.
  1) static_scene_description: 정적 이미지용. "사진 한 장"으로 찍힐 구체 사물/배경/조명/구도. 움직임/카메라 워킹 표현 금지.
  2) dynamic_action_description: 동영상용. 카메라 워킹(zoom/pan/tilt/dolly 등)과 피사체의 미세한 움직임을 포함.
  3) shared_keywords: 전체 분위기를 통일하는 공통 태그. 예: "cinematic, film still, soft lighting, 16:9 composition"

출력 규칙:
- 반드시 JSON만 출력하십시오. 마크다운, 설명 문장, 코드블록 금지.
- 출력은 JSON 배열(list)이며, 입력 segments와 동일한 길이/순서로 생성합니다.
- 각 원소는 최소한 다음 키를 포함해야 합니다:
  - segment_id
  - static_scene_description
  - dynamic_action_description
  - shared_keywords

품질 규칙:
- 가사를 그대로 복사하지 말고, 시각적으로 '찍히는 것'으로 번역하세요.
- 추상어(사랑, 이별, 그리움)는 구체 사물/공간/조명/날씨/소품으로 치환하세요.
- shared_keywords는 모든 장면에서 동일하게 유지하세요.
- 해상도는 1216x704 구도를 가정합니다.

프롬프트 포맷 가이드(결과는 아래 형식을 만족하도록 묘사하라):
- 이미지 프롬프트: "cinematic music video still, [구체적 장면 묘사], soft lighting, high quality, 16:9 composition"
- 영상 프롬프트: "cinematic music video, [카메라 워킹 및 피사체의 움직임 묘사], consistent color, no flicker"
""".strip()


def _strip_json_fence(text: str) -> str:
    t = (text or "").strip()
    if t.startswith("```"):
        # ```json ... ``` 형태 제거
        t = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", t)
        t = re.sub(r"\s*```$", "", t)
    return t.strip()


def _scrub_lyric_echo(desc: str, lyric_text: str) -> str:
    """
    (Wiring Check) 가사가 프롬프트에 직접 섞여 들어가는 현상 차단:
    - segment 원문이 그대로 포함되면 제거한다.
    """
    d = (desc or "").strip()
    lt = (lyric_text or "").strip()
    if not d or not lt:
        return d
    # 가장 강한 차단: 원문 라인 그대로 포함되면 삭제
    if lt in d:
        d = d.replace(lt, "").strip()
    return d


def _semantic_to_static_description(semantic_scene: Dict[str, Any]) -> str:
    location = dict(semantic_scene.get("location") or {})
    subject = dict(semantic_scene.get("subject") or {})
    parts: List[str] = []

    time_value = str(semantic_scene.get("time", "unspecified") or "unspecified")
    weather = str(semantic_scene.get("weather", "unspecified") or "unspecified")
    action = str(semantic_scene.get("action", "still_observation") or "still_observation")
    emotion = str(semantic_scene.get("emotion", "contemplative") or "contemplative")
    visual_elements = [str(v).replace("_", " ") for v in list(semantic_scene.get("visual_elements") or [])]

    subtype = str(location.get("subtype", "ambient_room") or "ambient_room").replace("_", " ")
    parts.append(f"{subtype} setting")
    if time_value != "unspecified":
        parts.append(f"{time_value} atmosphere")
    if weather != "unspecified":
        parts.append(f"{weather}-touched environment")

    subject_type = str(subject.get("type", "environment") or "environment")
    if subject_type == "human":
        count = int(subject.get("count", 1) or 1)
        state = str(subject.get("state", "present") or "present").replace("_", " ")
        parts.append(f"{count} solitary presence, {state}")
    else:
        parts.append("empty environment with no visible people")

    emotion_map = {
        "longing": "melancholic lighting and reflective surfaces",
        "sorrow": "soft dim light with muted contrast",
        "comfort": "warm practical lighting and gentle shadows",
        "hope": "soft glow breaking through the scene",
        "tension": "restless contrast and hard-edged shadows",
        "contemplative": "soft side lighting and quiet negative space",
    }
    parts.append(emotion_map.get(emotion, "soft side lighting and quiet negative space"))

    action_map = {
        "sitting_by_window": "window-side composition with seated silhouette near the glass",
        "watching_rain": "composition focused on the window and rain-streaked reflections",
        "walking_slowly": "long frame with a slow path through space",
        "running": "tense corridor-like depth with strong directional lines",
        "waiting_still": "still composition centered on patient stillness",
        "staring_in_reflection": "reflective surface foreground and layered depth",
        "still_observation": "locked composition with clear foreground-background separation",
    }
    parts.append(action_map.get(action, "locked composition with clear foreground-background separation"))

    parts.extend(visual_elements[:5])
    return ", ".join(_unique_preserve(parts))


def _semantic_to_motion_description(semantic_scene: Dict[str, Any]) -> str:
    weather = str(semantic_scene.get("weather", "unspecified") or "unspecified")
    action = str(semantic_scene.get("action", "still_observation") or "still_observation")
    emotion = str(semantic_scene.get("emotion", "contemplative") or "contemplative")
    subject = dict(semantic_scene.get("subject") or {})

    motion_parts = ["background frame stays steady", "localized motion emphasis", "seamless loop"]

    if weather == "rain":
        motion_parts.extend(["raindrops sliding on glass", "subtle reflection shimmer", "light pulse near the window"])
    elif weather == "wind":
        motion_parts.extend(["gentle fabric motion", "soft environmental drift", "foliage or curtain shimmer"])
    elif weather == "fog":
        motion_parts.extend(["slow haze drift", "soft depth breathing", "light bloom breathing softly"])
    else:
        motion_parts.append("subtle ambient motion only")

    if action == "sitting_by_window":
        motion_parts.append("quiet curtain movement near the window")
    elif action == "watching_rain":
        motion_parts.append("window reflections pulsing softly")
    elif action == "walking_slowly":
        motion_parts.append("soft clothing shimmer and drifting background haze")
    elif action == "running":
        motion_parts.append("restless light ripple and moving dust while the frame stays fixed")
    elif action == "staring_in_reflection":
        motion_parts.append("reflection shimmer and faint light fluctuation")
    else:
        motion_parts.append("soft light fluctuation")

    if str(subject.get("type", "environment") or "environment") != "human":
        motion_parts.append("environment-only movement")

    emotion_overlays = {
        "longing": "gentle melancholic pacing",
        "sorrow": "heavy stillness",
        "comfort": "calm breathing rhythm",
        "hope": "subtle brightening glow",
        "tension": "restrained nervous energy",
        "contemplative": "quiet observational mood",
    }
    motion_parts.append(emotion_overlays.get(emotion, "quiet observational mood"))
    return ", ".join(_unique_preserve(motion_parts))


def _build_structured_scene(
    *,
    idx: int,
    lyric_line: str,
    segment_payload: Dict[str, Any],
    semantic_scene: Dict[str, Any],
    base_seed: int,
    shared_keywords: str,
    width: int,
    height: int,
) -> Dict[str, Any]:
    camera_hint, _ = _infer_camera_and_motion(str(semantic_scene.get("action") or ""))
    motion_bucket_id = _pick_motion_bucket(camera_hint)
    static_desc = _scrub_lyric_echo(_semantic_to_static_description(semantic_scene), lyric_line)
    dynamic_desc = _scrub_lyric_echo(_semantic_to_motion_description(semantic_scene), lyric_line)
    scene = {
        "scene_index": idx + 1,
        "segment_id": str(segment_payload.get("segment_id", idx)),
        "lyric_text": lyric_line,
        "start_time": float(segment_payload.get("start_time", 0.0) or 0.0),
        "end_time": float(segment_payload.get("end_time", 0.0) or 0.0),
        "semantic_scene": semantic_scene,
        "semantic_summary": {
            "emotion": semantic_scene.get("emotion"),
            "action": semantic_scene.get("action"),
            "time": semantic_scene.get("time"),
            "weather": semantic_scene.get("weather"),
        },
        "static_scene_description": static_desc,
        "dynamic_action_description": dynamic_desc,
        "shared_keywords": shared_keywords,
        "style_seed": int(base_seed),
        "seed": int(base_seed) + (idx * 101),
        "motion_bucket_id": int(motion_bucket_id),
        "width": width,
        "height": height,
        "negative_prompt": "",
    }
    return scene


class VisualPlanner:
    """
    Story/Context 기반 Scene Planner.
    """

    def __init__(self, config: Optional[PlannerConfig] = None) -> None:
        self.config = config or PlannerConfig()

    async def plan_scenes_async(
        self,
        *,
        lyrics_segments: Sequence[Dict[str, Any]],
        metadata: Optional[Dict[str, Any]] = None,
        base_seed: Optional[int] = None,
        llm_host: str = "localhost",
        llm_port: int = 11434,
        llm_mode: str = "thinking",
    ) -> List[Dict[str, Any]]:
        """
        LLM 우선, 실패 시 휴리스틱 fallback.
        """
        try:
            # 지연 임포트: 옵션 의존성 분리
            from mellow_link.services.llm_service import LLMService

            svc = LLMService(host=str(llm_host), port=int(llm_port), timeout=120.0)
            await svc.connect()

            meta = metadata or {}
            segments_payload = []
            limited_segments = list(lyrics_segments)[: int(self.config.max_scenes)]
            semantic_scenes = extract_semantic_scenes(limited_segments)
            for i, seg in enumerate(limited_segments):
                segments_payload.append(
                    {
                        "segment_id": str(seg.get("id", i)),
                        "text": str(seg.get("text", "")),
                        "start_time": float(seg.get("start_time", 0.0) or 0.0),
                        "end_time": float(seg.get("end_time", 0.0) or 0.0),
                    }
                )

            shared_keywords = "cinematic, film still, soft lighting, 16:9 composition"
            user_prompt = json.dumps(
                {
                    "metadata": meta,
                    "shared_keywords": shared_keywords,
                    "segments": segments_payload,
                    "semantic_scenes": semantic_scenes,
                },
                ensure_ascii=False,
                indent=2,
            )

            prompt = (
                "아래 JSON 입력을 참고하여, semantic_scenes를 기반으로 segments 각각의 촬영 지시서 JSON 배열만 출력하세요.\n"
                "출력 JSON 배열의 각 원소는 입력 segment_id를 그대로 포함해야 합니다.\n\n"
                f"{user_prompt}"
            )

            result = await svc.generate(
                prompt=prompt,
                system_prompt=build_runtime_system_prompt(DEFAULT_CINEMATOGRAPHER_SYSTEM_PROMPT),
                mode=str(llm_mode),
                temperature=0.2,
                max_tokens=2048,
            )
            await svc.disconnect()

            raw = _strip_json_fence(getattr(result, "content", "") or "")
            parsed = json.loads(raw)
            if not isinstance(parsed, list):
                raise ValueError("LLM output is not a JSON list")

            # base_seed / 해상도 고정 적용 + 호환 필드 생성
            scenes: List[Dict[str, Any]] = []
            if base_seed is None:
                base_seed = random.randint(0, 2**31 - 1)

            width = int(self.config.width)
            height = int(self.config.height)

            for idx, item in enumerate(parsed[: int(self.config.max_scenes)]):
                if not isinstance(item, dict):
                    continue
                sid = str(item.get("segment_id", idx))
                lyric_line = str(segments_payload[idx].get("text", "")) if idx < len(segments_payload) else ""
                semantic_scene = semantic_scenes[idx] if idx < len(semantic_scenes) else {}
                static_desc = _scrub_lyric_echo(
                    str(item.get("static_scene_description", "")).strip() or _semantic_to_static_description(semantic_scene),
                    lyric_line,
                )
                dynamic_desc = _scrub_lyric_echo(
                    str(item.get("dynamic_action_description", "")).strip() or _semantic_to_motion_description(semantic_scene),
                    lyric_line,
                )
                sk = str(item.get("shared_keywords", shared_keywords)).strip() or shared_keywords

                scene = _build_structured_scene(
                    idx=idx,
                    lyric_line=lyric_line,
                    segment_payload={
                        "segment_id": sid,
                        "start_time": float(segments_payload[idx].get("start_time", 0.0)) if idx < len(segments_payload) else 0.0,
                        "end_time": float(segments_payload[idx].get("end_time", 0.0)) if idx < len(segments_payload) else 0.0,
                    },
                    semantic_scene=semantic_scene,
                    base_seed=int(base_seed),
                    shared_keywords=sk,
                    width=width,
                    height=height,
                )
                scene["static_scene_description"] = static_desc
                scene["dynamic_action_description"] = dynamic_desc
                scenes.append(enforce_scene_policy(scene))

            if scenes:
                return scenes
        except Exception:
            # fallback
            return self.plan_scenes(lyrics_segments=lyrics_segments, metadata=metadata, base_seed=base_seed)

        return self.plan_scenes(lyrics_segments=lyrics_segments, metadata=metadata, base_seed=base_seed)

    def plan_scenes(
        self,
        *,
        lyrics_segments: Sequence[Dict[str, Any]],
        metadata: Optional[Dict[str, Any]] = None,
        base_seed: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """
        휴리스틱 플래너: 필수 3요소(static/dynamic/shared)를 항상 생성.
        """
        meta = metadata or {}
        max_scenes = int(self.config.max_scenes)
        width = int(self.config.width)
        height = int(self.config.height)

        if base_seed is None:
            base_seed = random.randint(0, 2**31 - 1)

        limited_segments = list(lyrics_segments)[:max_scenes]
        semantic_scenes = extract_semantic_scenes(limited_segments)

        # 전체 컨텍스트(가사 전체) 기반 키워드/스타일 힌트
        full_text = "\n".join([str(s.get("text", "")).strip() for s in limited_segments if str(s.get("text", "")).strip()])
        global_keywords = _unique_preserve(_tokenize(full_text))[:12]

        mood = str(meta.get("mood") or "").strip()
        story = str(meta.get("story") or "").strip()
        artist = str(meta.get("artist") or "").strip()
        title = str(meta.get("title") or meta.get("song_title") or "").strip()

        # shared_keywords는 모든 씬에서 동일하게 유지
        shared_keywords = "cinematic, film still, soft lighting, 16:9 composition"

        scenes: List[Dict[str, Any]] = []
        for idx, seg in enumerate(limited_segments):
            lyric_line = str(seg.get("text", "")).strip()
            if not lyric_line:
                continue

            start_time = float(seg.get("start_time", 0.0) or 0.0)
            end_time = float(seg.get("end_time", 0.0) or 0.0)

            local_kw = _unique_preserve(_tokenize(lyric_line))
            keywords = _unique_preserve((local_kw + global_keywords))[:8]
            semantic_scene = semantic_scenes[idx] if idx < len(semantic_scenes) else {}
            scene = _build_structured_scene(
                idx=idx,
                lyric_line=lyric_line,
                segment_payload={
                    "segment_id": str(idx),
                    "start_time": start_time,
                    "end_time": end_time,
                },
                semantic_scene=semantic_scene,
                base_seed=int(base_seed),
                shared_keywords=shared_keywords,
                width=width,
                height=height,
            )
            scene["keywords"] = keywords
            scenes.append(enforce_scene_policy(scene))

        return scenes

