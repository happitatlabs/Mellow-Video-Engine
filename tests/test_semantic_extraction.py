from __future__ import annotations


def test_semantic_extractor_maps_rain_night_and_alone():
    from mellow_link.services.semantic_scene_extractor import extract_semantic_scene

    scene = extract_semantic_scene("비 내리는 밤 창가에 혼자 앉아 너를 그리워해")

    assert scene["time"] == "night"
    assert scene["weather"] == "rain"
    assert scene["location"]["setting"] == "indoor"
    assert scene["location"]["subtype"] == "window_room"
    assert scene["subject"]["type"] == "human"
    assert scene["subject"]["count"] == 1
    assert scene["subject"]["state"] == "alone"
    assert scene["action"] in {"sitting_by_window", "watching_rain"}
    assert scene["emotion"] == "longing"
    assert "window" in scene["visual_elements"]
    assert "raindrops" in scene["visual_elements"]


def test_visual_planner_embeds_semantic_scene_and_policy_outputs():
    from mellow_link.services.visual_planner import VisualPlanner

    planner = VisualPlanner()
    scenes = planner.plan_scenes(
        lyrics_segments=[{"text": "비 내리는 밤 창가에 혼자 앉아", "start_time": 0.0, "end_time": 2.0}],
        metadata={"mood": "melancholy"},
        base_seed=11,
    )

    assert len(scenes) == 1
    scene = scenes[0]
    semantic = scene["semantic_scene"]
    assert semantic["weather"] == "rain"
    assert semantic["time"] == "night"
    assert semantic["subject"]["type"] == "human"
    assert scene["semantic_summary"]["emotion"] == semantic["emotion"]
    assert "window" in scene["static_scene_description"].lower()
    assert "loop" in scene["motion_prompt"].lower()
    assert "semantic_scene" in scene
    assert "policy_inputs" in scene
    assert "policy_outputs" in scene
    assert scene["policy_validation"]["ok"] is True


def test_semantic_human_signal_survives_extraction_but_is_sanitized_for_prompts():
    from mellow_link.services.visual_planner import VisualPlanner

    scene = VisualPlanner().plan_scenes(
        lyrics_segments=[{"text": "혼자 무대 위에 서 있는 너", "start_time": 0.0, "end_time": 1.0}],
        base_seed=3,
    )[0]

    assert scene["semantic_scene"]["subject"]["type"] == "human"
    assert scene["semantic_scene"]["subject"]["count"] >= 1
    assert "human subject" not in scene["static_prompt"].lower()
    assert scene["policy_validation"]["ok"] is True
