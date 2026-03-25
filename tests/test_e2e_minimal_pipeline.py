from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace


def test_e2e_minimal_pipeline(monkeypatch, tmp_path: Path):
    import web_ui
    from mellow_link.services.output_provenance import sidecar_path_for, write_sidecar_best_effort
    from mellow_link.services.visual_planner import VisualPlanner

    outputs_root = tmp_path / "outputs"
    output_dirs = {
        "root": outputs_root,
        "uploads": outputs_root / "uploads",
        "transcripts": outputs_root / "transcripts",
        "images": outputs_root / "images",
        "videos": outputs_root / "videos",
        "final": outputs_root / "final",
    }
    for path in output_dirs.values():
        path.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(web_ui, "get_output_directories", lambda *_args, **_kwargs: output_dirs)

    async def _fake_connect(_session):
        return None

    monkeypatch.setattr(web_ui, "_connect_services", _fake_connect)

    audio_path = output_dirs["uploads"] / "demo.wav"
    audio_path.write_bytes(b"RIFFdemo")

    class FakeImageService:
        async def generate_image(self, req, on_progress=None):
            if on_progress:
                await on_progress(100.0, "done")
            out = output_dirs["images"] / "scene_1.png"
            out.write_bytes(b"fake-png")
            provenance = getattr(req, "provenance", None) or {}
            write_sidecar_best_effort(
                out,
                artifact_type="image",
                source=provenance.get("source", {}),
                runtime=provenance.get("runtime", {}),
                request=provenance.get("request", {}),
            )
            return str(out)

    class FakeVideoService:
        def get_status(self):
            return SimpleNamespace(name="CONNECTED")

        async def generate_video(self, req, on_progress=None):
            if on_progress:
                await on_progress(100.0, "done")
            out = output_dirs["videos"] / "scene_1.mp4"
            out.write_bytes(b"fake-mp4")
            provenance = getattr(req, "provenance", None) or {}
            write_sidecar_best_effort(
                out,
                artifact_type="video",
                source=provenance.get("source", {}),
                runtime=provenance.get("runtime", {}),
                request=provenance.get("request", {}),
            )
            return str(out)

    project = web_ui.WebProject(project_name="smoke", audio_file_path=str(audio_path), metadata={"mood": "serene"})
    project.lyrics_segments = [{"text": "고요한 새벽", "start_time": 0.0, "end_time": 2.0}]
    project.scene_plans = VisualPlanner().plan_scenes(
        lyrics_segments=project.lyrics_segments,
        metadata=project.metadata,
        base_seed=7,
    )
    session = web_ui.WebSession(project=project, image_service=FakeImageService(), video_service=FakeVideoService())

    image_path = web_ui.asyncio.run(
        web_ui.generate_single_scene_image(session, 0, project.scene_plans[0]["static_prompt"])
    )
    assert Path(image_path).exists()
    assert sidecar_path_for(image_path).exists()

    project.generated_images = [image_path]
    video_path = web_ui.asyncio.run(
        web_ui.generate_single_scene_video(session, 0, image_path, project.scene_plans[0]["motion_prompt"])
    )
    assert Path(video_path).exists()
    assert sidecar_path_for(video_path).exists()

    project.generated_clips = [video_path]

    def fake_run(cmd, capture_output=False, text=False, check=False):
        exe = str(cmd[0]).lower()
        target = Path(str(cmd[-1]))
        if "ffprobe" in exe:
            probe_target = Path(str(cmd[-1]))
            stdout = "3.0" if probe_target == audio_path else "1.0"
            return SimpleNamespace(returncode=0, stdout=stdout, stderr="")
        if "ffmpeg" in exe:
            target.write_bytes(b"final-mp4")
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        raise AssertionError(f"Unexpected command: {cmd}")

    monkeypatch.setattr(web_ui.shutil, "which", lambda exe: exe)
    monkeypatch.setattr(web_ui.subprocess, "run", fake_run)

    final_path = web_ui.asyncio.run(web_ui.finalize_video(session))
    assert Path(final_path).exists()
    assert Path(final_path).parent == output_dirs["final"]
    assert sidecar_path_for(final_path).exists()
