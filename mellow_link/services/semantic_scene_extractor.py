"""
Semantic scene extraction layer for lyrics/text segments.

Turns raw segment text into structured scene hints before prompt generation.
Policy enforcement does not happen here; human-related signals are preserved
so the downstream prompt policy can sanitize or downgrade them intentionally.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Sequence


def _contains_any(text: str, keywords: Iterable[str]) -> bool:
    lowered = str(text or "").lower()
    return any(keyword in lowered for keyword in keywords)


def _unique_preserve(items: Iterable[str]) -> List[str]:
    seen = set()
    out: List[str] = []
    for item in items:
        token = str(item or "").strip()
        if not token:
            continue
        key = token.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(token)
    return out


def _detect_time(text: str) -> str:
    if _contains_any(text, ("night", "midnight", "moon", "star", "새벽", "밤", "야밤", "달", "별", "어둠")):
        return "night"
    if _contains_any(text, ("sunrise", "dawn", "morning", "아침", "동틀녘", "해뜰", "새벽빛")):
        return "dawn"
    if _contains_any(text, ("sunset", "dusk", "evening", "노을", "황혼", "저녁")):
        return "dusk"
    if _contains_any(text, ("day", "sunlight", "afternoon", "낮", "햇살", "정오", "오후")):
        return "day"
    return "unspecified"


def _detect_weather(text: str) -> str:
    if _contains_any(text, ("rain", "storm", "drizzle", "wet", "비", "빗", "폭우", "소나기", "장대비")):
        return "rain"
    if _contains_any(text, ("snow", "blizzard", "눈", "설원", "눈보라")):
        return "snow"
    if _contains_any(text, ("wind", "breeze", "gust", "바람", "돌풍", "바람결")):
        return "wind"
    if _contains_any(text, ("fog", "mist", "haze", "안개", "물안개", "아지랑이")):
        return "fog"
    if _contains_any(text, ("clear sky", "sunny", "맑", "청명")):
        return "clear"
    return "unspecified"


def _detect_location(text: str) -> Dict[str, str]:
    if _contains_any(text, ("window", "curtain", "room", "bed", "hallway", "windowpane", "창", "창가", "방", "실내", "커튼", "복도")):
        return {"setting": "indoor", "subtype": "window_room"}
    if _contains_any(text, ("street", "alley", "city", "road", "거리", "골목", "도시", "도로")):
        return {"setting": "outdoor", "subtype": "street"}
    if _contains_any(text, ("sea", "ocean", "shore", "beach", "파도", "바다", "해변", "해안")):
        return {"setting": "outdoor", "subtype": "shore"}
    if _contains_any(text, ("forest", "field", "mountain", "woods", "숲", "들판", "산", "언덕")):
        return {"setting": "outdoor", "subtype": "nature"}
    if _contains_any(text, ("stage", "theater", "club", "concert", "무대", "극장", "클럽", "공연장")):
        return {"setting": "indoor", "subtype": "stage"}
    return {"setting": "indoor", "subtype": "ambient_room"}


def _detect_subject(text: str) -> Dict[str, Any]:
    if _contains_any(text, ("alone", "lonely", "혼자", "외로", "홀로", "나 혼자")):
        return {"type": "human", "count": 1, "state": "alone"}
    if _contains_any(text, ("we", "together", "crowd", "people", "우리", "함께", "군중", "사람들")):
        return {"type": "human", "count": 2, "state": "together"}
    if _contains_any(text, ("you", "your", "그대", "너", "네가")):
        return {"type": "human", "count": 1, "state": "implied"}
    return {"type": "environment", "count": 0, "state": "empty"}


def _detect_action(text: str, location: Dict[str, str], weather: str) -> str:
    if _contains_any(text, ("sit", "sitting", "앉", "기대", "머물")) and location.get("subtype") == "window_room":
        return "sitting_by_window"
    if weather == "rain":
        return "watching_rain"
    if _contains_any(text, ("walk", "walking", "wander", "걷", "헤매", "거닐")):
        return "walking_slowly"
    if _contains_any(text, ("run", "running", "달려", "뛰")):
        return "running"
    if _contains_any(text, ("wait", "waiting", "기다", "멈춰")):
        return "waiting_still"
    if _contains_any(text, ("remember", "memory", "기억", "추억", "되새")):
        return "staring_in_reflection"
    return "still_observation"


def _detect_emotion(text: str) -> str:
    if _contains_any(text, ("longing", "miss", "yearn", "그리", "그립", "그리움", "보고 싶")):
        return "longing"
    if _contains_any(text, ("sad", "cry", "tear", "슬픔", "눈물", "울", "아프")):
        return "sorrow"
    if _contains_any(text, ("warm", "comfort", "포근", "따뜻", "안온", "위로")):
        return "comfort"
    if _contains_any(text, ("anger", "rage", "furious", "분노", "화나", "격렬")):
        return "tension"
    if _contains_any(text, ("hope", "light", "희망", "빛", "구원")):
        return "hope"
    return "contemplative"


def _detect_visual_elements(text: str, *, time_value: str, weather: str, location: Dict[str, str], subject: Dict[str, Any]) -> List[str]:
    elements: List[str] = []
    if time_value == "night":
        elements.extend(["dim_light", "deep_shadows"])
    if weather == "rain":
        elements.extend(["raindrops", "wet_glass", "reflection"])
    if weather == "fog":
        elements.extend(["mist", "diffused_background"])
    if location.get("subtype") == "window_room":
        elements.extend(["window", "curtain", "interior_reflection"])
    if location.get("subtype") == "street":
        elements.extend(["street_lights", "wet_pavement"])
    if subject.get("type") == "human":
        elements.append("solitary_presence")

    lowered = str(text or "").lower()
    if _contains_any(lowered, ("mirror", "거울")):
        elements.append("mirror")
    if _contains_any(lowered, ("moon", "달")):
        elements.append("moonlight")
    if _contains_any(lowered, ("tea", "cup", "찻잔", "컵")):
        elements.append("cup")
    if _contains_any(lowered, ("wind", "바람")):
        elements.append("fabric_motion")
    return _unique_preserve(elements)


def extract_semantic_scene(text: str, *, segment_id: str | int | None = None) -> Dict[str, Any]:
    raw_text = str(text or "").strip()
    time_value = _detect_time(raw_text)
    weather = _detect_weather(raw_text)
    location = _detect_location(raw_text)
    subject = _detect_subject(raw_text)
    action = _detect_action(raw_text, location, weather)
    emotion = _detect_emotion(raw_text)
    visual_elements = _detect_visual_elements(
        raw_text,
        time_value=time_value,
        weather=weather,
        location=location,
        subject=subject,
    )
    return {
        "segment_id": None if segment_id is None else str(segment_id),
        "raw_text": raw_text,
        "time": time_value,
        "weather": weather,
        "location": location,
        "subject": subject,
        "action": action,
        "emotion": emotion,
        "visual_elements": visual_elements,
    }


def extract_semantic_scenes(segments: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    scenes: List[Dict[str, Any]] = []
    for idx, segment in enumerate(list(segments)):
        text = str(segment.get("text", "") or "")
        scene = extract_semantic_scene(text, segment_id=segment.get("id", idx))
        scene["start_time"] = float(segment.get("start_time", 0.0) or 0.0)
        scene["end_time"] = float(segment.get("end_time", 0.0) or 0.0)
        scenes.append(scene)
    return scenes
