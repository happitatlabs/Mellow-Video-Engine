from __future__ import annotations

import asyncio
from pathlib import Path


def test_main_entrypoint_is_deprecation_shim():
    content = Path("main.py").read_text(encoding="utf-8")
    assert "deprecated" in content.lower()
    assert "web_ui.py" in content
    assert "api_server.py" in content


def test_sitecustomize_exists_for_local_repo_priority():
    assert Path("sitecustomize.py").exists()


def test_api_server_exports_fastapi_app():
    import api_server

    assert api_server.app.title == "Mellow API Server"
    assert callable(api_server.plan_scenes)
    assert callable(api_server.gen_scene_image)
    assert callable(api_server.gen_scene_video)
    assert callable(api_server.merge_session)


def test_web_ui_current_baseline_helpers():
    import web_ui

    assert web_ui.MAX_SCENES == 20
    scrubbed = web_ui._scrub_lyric_echo(
        "cinematic music video still, 사랑은 늘 도망가, soft lighting",
        "사랑은 늘 도망가",
    )
    assert "사랑은 늘 도망가" not in scrubbed
    updates = web_ui._scene_slot_updates_from_models([])
    assert len(updates) == web_ui.MAX_SCENES * 6


def test_visual_planner_runtime_is_config_prompt_driven():
    from mellow_link.services.visual_planner import VisualPlanner

    planner = VisualPlanner()
    scenes = planner.plan_scenes(
        lyrics_segments=[{"text": "그리운 밤", "start_time": 0.0, "end_time": 2.0}],
        metadata={"mood": "serene"},
        base_seed=1,
    )

    assert scenes
    scene = scenes[0]
    assert "no humans" in scene["static_prompt"].lower()
    assert "humans" in scene["negative_prompt"].lower()
    assert "hand enters frame" not in scene["motion_prompt"].lower()
    assert "semantic_scene" in scene
    assert scene["semantic_scene"]["emotion"]
    assert scene["policy_validation"]["ok"] is True
    assert scene["motion_bucket_id"] <= 80
    assert "background frame stays steady" in scene["dynamic_action_description"].lower()


def test_readme_documents_current_runtime():
    content = Path("README.md").read_text(encoding="utf-8")
    assert "web_ui.py" in content
    assert "api_server.py" in content
    assert "Prompt Policy Gap" in content
    assert "Provenance" in content
    assert "Minimum Startup Order" in content
    assert "LOCAL_MOTION_LOOP" in content
    assert "AMBIENT_STILL_LOOP" in content


def test_runtime_output_unification_settings():
    from mellow_link.services.runtime_config import (
        get_motion_video_spike_settings,
        get_output_directories,
        get_video_generation_settings,
        should_strip_prompt_metadata,
    )

    output_dirs = get_output_directories()
    video_settings = get_video_generation_settings()
    motion_spike = get_motion_video_spike_settings()
    assert output_dirs["root"].name == "outputs"
    assert output_dirs["images"].parent == output_dirs["root"]
    assert output_dirs["videos"].parent == output_dirs["root"]
    assert should_strip_prompt_metadata() is False
    assert video_settings["default_mode"] == "LOCAL_MOTION_LOOP"
    assert video_settings["locked_camera_mode"] is True
    assert video_settings["locked_camera_backend"] == "ambient_loop"
    assert video_settings["stabilize_zoom_drift"] is True
    assert video_settings["ambient_motion_strength"] >= 0.34
    assert video_settings["locked_camera_workflow"] == "svd_xt_locked_camera.json"
    assert video_settings["ambient_debug_visualization"] is True
    assert video_settings["ambient_visibility_mode"] == "visibility_first"
    assert video_settings["ambient_min_patch_alpha"] >= 0.22
    assert video_settings["ambient_min_patch_shift_px"] >= 14.0
    assert video_settings["ambient_min_light_pulse"] >= 0.75
    assert motion_spike["engine"] == "ltx_local_2b_v0_9"
    assert motion_spike["model_file"] == "ltx-video-2b-v0.9.safetensors"
    assert motion_spike["clip_file"] == "t5xxl_fp16.safetensors"
    assert motion_spike["width"] == 576
    assert motion_spike["height"] == 320
    assert motion_spike["length"] == 17


def test_prompt_policy_helper_and_provenance_sidecar(tmp_path: Path):
    from mellow_link.services.output_provenance import archive_artifact_with_sidecar, sidecar_path_for, write_sidecar_best_effort
    from mellow_link.services.prompt_policy import enforce_scene_policy

    scene = enforce_scene_policy(
        {
            "static_scene_description": "A guitarist under bright lights",
            "dynamic_action_description": "A hand enters frame and the performer moves forward as a silhouette appears",
            "negative_prompt": "",
        }
    )
    assert "guitarist" not in scene["static_prompt"].lower()
    assert scene["policy_validation"]["ok"] is True
    assert scene["policy_validation"]["policy_level"] in {"best_effort_sanitized", "fail_safe_downgraded"}

    output_file = tmp_path / "demo.png"
    output_file.write_bytes(b"fake")
    sidecar = write_sidecar_best_effort(
        output_file,
        artifact_type="image",
        source={"project_id": "demo"},
        runtime={"planner_version": "test"},
        request={"strip_prompt_metadata": False},
    )
    assert sidecar.exists()
    assert sidecar.name.endswith(".meta.json")
    assert sidecar == sidecar_path_for(output_file)

    archived = archive_artifact_with_sidecar(
        output_file,
        archive_root=tmp_path / "outputs" / "_archive",
        label="test",
        stamp="20260319",
    )
    assert archived["artifact"].exists()
    assert archived["sidecar"].exists()
    assert archived["artifact"].parent.name == "20260319_test"


def test_runtime_readiness_helper_and_adapter_use_runtime_endpoint(monkeypatch, tmp_path: Path):
    import mellow_link.media.adapters.ai_comfy as ai_comfy
    import mellow_link.media.services.image_service as image_service_module
    import mellow_link.media.services.video_service as video_service_module
    from mellow_link.media.schemas import ImageRequest, VideoRequest
    from mellow_link.services.runtime_config import get_comfyui_endpoint

    settings = {
        "comfyui": {"host": "10.0.0.55", "port": 8199, "timeout": 123},
        "outputs": {
            "root": str(tmp_path / "outputs"),
            "images_dir": "images",
            "videos_dir": "videos",
            "uploads_dir": "uploads",
            "transcripts_dir": "transcripts",
            "final_dir": "final",
        },
    }
    monkeypatch.setattr(ai_comfy, "load_settings", lambda: settings)
    monkeypatch.setattr(ai_comfy, "assert_media_generation_ready", lambda _settings: asyncio.sleep(0, result={"ok": True}))

    endpoint = get_comfyui_endpoint(settings)
    output_dirs = {
        "root": tmp_path / "outputs",
        "images": tmp_path / "outputs" / "images",
        "videos": tmp_path / "outputs" / "videos",
        "uploads": tmp_path / "outputs" / "uploads",
        "transcripts": tmp_path / "outputs" / "transcripts",
        "final": tmp_path / "outputs" / "final",
    }
    for path in output_dirs.values():
        path.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(ai_comfy, "get_output_directories", lambda _settings: output_dirs)

    seen: dict[str, tuple] = {}

    class FakeImageService:
        def __init__(self, host, port, timeout, output_dir):
            seen["image"] = (host, port, timeout, Path(output_dir))

        async def connect(self):
            return True

        async def disconnect(self):
            return None

        async def _execute_generation(self, request, **kwargs):
            return {"request": request.prompt, "kwargs": kwargs}

    class FakeVideoService:
        def __init__(self, host, port, timeout, output_dir):
            seen["video"] = (host, port, timeout, Path(output_dir))

        async def connect(self):
            return True

        async def disconnect(self):
            return None

        async def _generate_video_impl(self, request, **kwargs):
            return {"request": request.prompt, "kwargs": kwargs}

    monkeypatch.setattr(image_service_module, "ImageService", FakeImageService)
    monkeypatch.setattr(video_service_module, "VideoService", FakeVideoService)

    adapter = ai_comfy.ComfyMediaAIAdapter()
    asyncio.run(adapter.generate_image(ImageRequest(prompt="test image")))
    asyncio.run(adapter.generate_video(VideoRequest(image_path="x.png", prompt="test video")))

    assert seen["image"][0] == endpoint["host"]
    assert seen["image"][1] == endpoint["port"]
    assert seen["image"][2] == endpoint["timeout"]
    assert seen["image"][3] == output_dirs["images"]
    assert seen["video"][0] == endpoint["host"]
    assert seen["video"][1] == endpoint["port"]
    assert seen["video"][2] == endpoint["timeout"]
    assert seen["video"][3] == output_dirs["videos"]


def test_video_service_motion_bucket_respects_locked_camera_prompt():
    from mellow_link.media.services.video_service import VideoService

    svc = VideoService(host="127.0.0.1", port=8188)
    assert svc._resolve_motion_bucket_id(127, "locked-off camera, static frame, subtle ambient motion only") == 1
    assert svc._resolve_motion_bucket_id(127, "slow zoom in with gentle movement") == 80


def test_video_service_defaults_to_local_motion_loop_mode():
    from mellow_link.media.services.video_service import VideoService

    svc = VideoService(host="127.0.0.1", port=8188)
    assert svc._normalize_mode(None) == "LOCAL_MOTION_LOOP"
    assert svc._normalize_mode("") == "LOCAL_MOTION_LOOP"


def test_web_ui_normalizes_default_video_prompt_for_looping():
    import web_ui

    prompt = web_ui._normalize_loop_motion_prompt("slow pan across the room with gentle zoom")
    assert "window light" in prompt.lower()
    assert "visible local motion" in prompt.lower()
    assert "fixed camera" in prompt.lower()


def test_video_service_uses_repo_workflow_directory():
    from mellow_link.media.services.video_service import VideoService

    svc = VideoService(host="127.0.0.1", port=8188)
    workflow_dir = svc._workflow_dir()
    assert workflow_dir.as_posix().endswith("mellow_link/data/workflows")
    assert (workflow_dir / "svd_xt_main.json").exists()
    assert (workflow_dir / "svd_xt_locked_camera.json").exists()
    assert (workflow_dir / "ltx_2b_v0_9_i2v_lowmem.json").exists()
    assert (workflow_dir / "ltx_2b_v0_9_ckpt_i2v_lowmem.json").exists()


def test_ltx_checkpoint_workflow_uses_explicit_clip_loader():
    import json
    workflow = json.loads(Path("mellow_link/data/workflows/ltx_2b_v0_9_ckpt_i2v_lowmem.json").read_text(encoding="utf-8"))
    assert workflow["3"]["class_type"] == "CLIPLoader"
    assert workflow["3"]["inputs"]["clip_name"] == "t5xxl_fp16.safetensors"
    assert workflow["3"]["inputs"]["type"] == "ltxv"
    assert workflow["4"]["inputs"]["clip"] == ["3", 0]
    assert workflow["5"]["inputs"]["clip"] == ["3", 0]


def test_video_service_prefers_locked_camera_workflow_and_overrides():
    import json
    from pathlib import Path
    from mellow_link.media.services.video_service import VideoService

    svc = VideoService(host="127.0.0.1", port=8188)
    selected = svc._select_video_workflow_name("svd_xt_main.json", "AMBIENT_STILL_LOOP")
    assert selected == "svd_xt_locked_camera.json"

    workflow_path = Path("mellow_link/data/workflows/svd_xt_main.json")
    workflow = json.loads(workflow_path.read_text(encoding="utf-8"))
    overridden = svc._apply_locked_camera_overrides(workflow, mode="AMBIENT_STILL_LOOP", motion_bucket_id=1)
    assert overridden["14"]["inputs"]["motion_bucket_id"] == 1
    assert overridden["14"]["inputs"]["augmentation_level"] == 0.0
    assert overridden["14"]["inputs"]["video_frames"] == 21
    assert overridden["14"]["inputs"]["fps"] == 7
    assert overridden["19"]["inputs"]["steps"] == 14
    assert overridden["19"]["inputs"]["cfg"] == 1.5


def test_video_service_prefers_ltx_workflow_for_local_motion_loop():
    from mellow_link.media.services.video_service import VideoService

    svc = VideoService(host="127.0.0.1", port=8188)
    selected = svc._select_video_workflow_name(None, "LOCAL_MOTION_LOOP")
    assert selected == "ltx_2b_v0_9_ckpt_i2v_lowmem.json"


def test_video_service_uses_local_ambient_loop_backend_for_locked_camera(monkeypatch, tmp_path: Path):
    import asyncio
    import mellow_link.media.services.video_service as video_service_module
    from mellow_link.media.schemas import VideoRequest

    src = tmp_path / "frame.png"
    src.write_bytes(b"fake-image")
    out = tmp_path / "ambient.mp4"
    out.write_bytes(b"ambient-video")
    seen = {}

    def fake_create(image_path, *, output_path, target_duration, fps, strength, motion_profile=None):
        seen["image_path"] = Path(image_path)
        seen["output_path"] = Path(output_path)
        seen["target_duration"] = target_duration
        seen["fps"] = fps
        seen["strength"] = strength
        seen["motion_profile"] = motion_profile or {}
        return out

    def fake_stabilize(input_path, *, strength):
        seen["stabilized"] = Path(input_path)
        return input_path

    monkeypatch.setattr(video_service_module, "create_ambient_loop_from_image", fake_create)
    monkeypatch.setattr(video_service_module, "stabilize_video_drift", fake_stabilize)

    svc = video_service_module.VideoService(host="127.0.0.1", port=8188, output_dir=tmp_path)
    req = VideoRequest(
        image_path=str(src),
        motion_prompt="locked-off camera",
        prompt="locked-off camera",
        mode="AMBIENT_STILL_LOOP",
        target_duration=12.0,
        fps=8,
    )
    result = asyncio.run(svc._generate_video_impl(req))
    assert Path(result) == out
    assert seen["image_path"] == src.resolve()
    assert seen["target_duration"] == 12.0
    assert seen["fps"] == 8
    assert seen["motion_profile"]["local_motion_emphasis"] >= seen["motion_profile"]["global_motion_balance"]
    assert seen["motion_profile"]["visibility_mode"] == "visibility_first"
    assert seen["motion_profile"]["debug_visualization"] is True
    assert seen["motion_profile"]["min_patch_alpha"] >= 0.22
    assert seen["motion_profile"]["min_patch_shift_px"] >= 14.0
    assert seen["motion_profile"]["min_light_pulse"] >= 0.75


def test_video_service_parses_ambient_loop_direction_to_local_motion_profile():
    from mellow_link.media.services.video_service import VideoService

    svc = VideoService(host="127.0.0.1", port=8188)
    profile = svc._ambient_loop_profile(
        "창빛이 은은하게 숨 쉬고, 안개가 뒤에서 흐르며, 커튼 끝과 갈대만 조금 더 또렷하게 움직인다. 국소 움직임 위주."
    )
    assert profile["light_pulse"] > 0.6
    assert profile["haze_drift"] > 0.4
    assert profile["fabric_shimmer"] > 0.4 or profile["foliage_shimmer"] > 0.45
    assert profile["local_motion_emphasis"] > profile["global_motion_balance"]
    assert profile["visibility_mode"] == "visibility_first"


def test_video_service_normalizes_legacy_locked_camera_alias():
    from mellow_link.media.services.video_service import VideoService

    svc = VideoService(host="127.0.0.1", port=8188)
    assert svc._normalize_mode("VIDEO_LOCKED_CAMERA") == "AMBIENT_STILL_LOOP"
    assert svc._normalize_mode("AMBIENT_STILL_LOOP") == "AMBIENT_STILL_LOOP"


def test_settings_load_repo_root_env_even_when_cwd_changes(monkeypatch):
    import mellow_link.config.settings as settings_module

    monkeypatch.setenv("ENABLE_MEDIA_AI", "0")
    monkeypatch.setenv("ENABLE_MEDIA_COMPUTE", "0")
    monkeypatch.setenv("ENABLE_FFMPEG", "0")
    monkeypatch.chdir(Path("D:/"))
    settings_module._preload_repo_env()
    settings_module.clear_settings_cache()
    try:
        loaded = settings_module.Settings()
        assert loaded.enable_media_ai is True
        assert loaded.allow_media_ai() is True
        assert loaded.enable_media_compute is True
        assert loaded.allow_media_compute() is True
        assert loaded.enable_ffmpeg is True
        assert loaded.allow_ffmpeg() is True
        assert str(settings_module._ENV_FILE).endswith(r"Mellow-Video-Engine\.env")
    finally:
        settings_module.clear_settings_cache()


def test_compute_ffmpeg_uses_configured_ffmpeg_paths():
    import mellow_link.media.adapters.compute_ffmpeg as compute_ffmpeg

    ffmpeg_path = compute_ffmpeg._resolve_ffmpeg()
    ffprobe_path = compute_ffmpeg._resolve_ffprobe()

    assert ffmpeg_path.lower().endswith("ffmpeg.exe")
    assert ffprobe_path.lower().endswith("ffprobe.exe")
    assert Path(ffmpeg_path).exists()
    assert Path(ffprobe_path).exists()
