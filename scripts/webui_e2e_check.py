from __future__ import annotations

from pathlib import Path


def _resolve_image_path(img) -> str | None:
    if isinstance(img, str):
        return img
    if isinstance(img, dict):
        return img.get("path") or img.get("name")
    return getattr(img, "path", None) or getattr(img, "name", None)


def main() -> int:
    # Local imports to keep this script lightweight
    from gradio_client import Client, handle_file

    host = "127.0.0.1"
    port = 7861
    base_url = f"http://{host}:{port}"

    audio_path = Path("output") / "web_ui_smoke.wav"
    if not audio_path.exists():
        raise SystemExit(f"Missing audio file: {audio_path}")

    lyrics = "[0.00 - 1.00] 사랑은 늘 도망가"

    print(f"[E2E] Connecting: {base_url}")
    c = Client(base_url)

    print("[E2E] 1) /start_processing")
    out = c.predict(
        handle_file(str(audio_path)),
        lyrics,  # full_lyrics
        "",  # artist
        "",  # title
        "잔잔함",  # mood
        "현실적인 도시의 밤",  # story
        api_name="/start_processing",
    )
    print("  - status:", str(out[0]).splitlines()[0] if out else "<empty>")

    print("[E2E] 2) /confirm_lyrics_and_continue")
    out2 = c.predict(lyrics, api_name="/confirm_lyrics_and_continue")
    print("  - status:", str(out2[0]).splitlines()[0] if out2 else "<empty>")

    # Heuristic pick prompts from giant tuple
    img_prompt = None
    vid_prompt = None
    for v in out2:
        if isinstance(v, str) and img_prompt is None and "cinematic" in v.lower() and "still" in v.lower():
            img_prompt = v
        if isinstance(v, str) and vid_prompt is None and (
            "camera" in v.lower() or "dolly" in v.lower() or "zoom" in v.lower() or "parallax" in v.lower()
        ):
            vid_prompt = v
        if img_prompt and vid_prompt:
            break

    img_prompt = img_prompt or "cinematic music video still, soft lighting, high quality, 16:9 composition"
    vid_prompt = vid_prompt or "cinematic music video, slow cinematic dolly in, subtle parallax, stable framing, 16:9 composition"
    print("  - img_prompt:", img_prompt[:140].replace("\n", " "))
    print("  - vid_prompt:", vid_prompt[:140].replace("\n", " "))

    print("[E2E] 3) /scene_1_generate_image")
    img_res = c.predict(img_prompt, api_name="/scene_1_generate_image")
    print("  - status:", img_res[0] if img_res else "<empty>")
    img = img_res[1] if len(img_res) > 1 else None
    img_path = _resolve_image_path(img)
    print("  - raw image output:", img)
    print("  - resolved img_path:", img_path)
    if not img_path or not Path(img_path).exists():
        raise SystemExit(f"Image generation did not return a valid file path: {img_path!r}")

    print("[E2E] 4) /scene_1_generate_video")
    vid_res = c.predict(vid_prompt, img, api_name="/scene_1_generate_video")
    print("  - status:", vid_res[0] if vid_res else "<empty>")
    vid_path = vid_res[1] if len(vid_res) > 1 else None
    print("  - video path:", vid_path)
    if not vid_path or not Path(str(vid_path)).exists():
        raise SystemExit(f"Video generation did not return a valid file path: {vid_path!r}")

    print("[E2E] OK video exists:", str(Path(str(vid_path)).resolve()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

