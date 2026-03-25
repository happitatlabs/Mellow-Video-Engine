from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional


def _emit(obj: Dict[str, Any]) -> None:
    try:
        sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
        sys.stdout.flush()
    except Exception:
        pass


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Mellow transcription worker (isolated process)")
    p.add_argument("--audio", required=True, help="Path to audio file")
    p.add_argument("--out", required=True, help="Path to output JSON file")
    p.add_argument("--model", default="large-v3", help="Whisper model size (e.g. large-v3)")
    p.add_argument("--device", default="cpu", help="cpu|cuda (default: cpu for stability)")
    p.add_argument("--compute", default=None, help="compute_type override (e.g. int8, float16)")
    p.add_argument("--language", default=None, help="language code or omit for auto")
    p.add_argument("--initial-prompt", default=None, help="initial prompt hint")
    args = p.parse_args(argv)

    audio_path = Path(args.audio)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if not audio_path.exists():
        _emit({"type": "error", "message": f"audio not found: {audio_path}"})
        return 2

    device = (args.device or "cpu").strip().lower()
    if device not in {"cpu", "cuda"}:
        device = "cpu"

    compute_type = args.compute
    if not compute_type:
        compute_type = "float16" if device == "cuda" else "int8"

    _emit({"type": "start", "audio": str(audio_path), "model": args.model, "device": device, "compute": compute_type})

    try:
        from backend.audio_engine import LyricAligner  # type: ignore

        aligner = LyricAligner(device=device, compute_type=str(compute_type))

        def cb(progress: float, status: str) -> None:
            _emit({"type": "progress", "p": float(progress), "msg": str(status)})

        raw = aligner.transcribe(
            audio_path=audio_path,
            model_size=str(args.model),
            language=args.language,
            initial_prompt=args.initial_prompt,
            progress_callback=cb,
        )

        segments: List[Dict[str, Any]] = []
        max_end = 0.0
        for seg in raw or []:
            start = float(seg.get("start", 0.0) or 0.0)
            end = float(seg.get("end", 0.0) or 0.0)
            text = str(seg.get("text", "") or "").strip()
            if not text:
                continue
            max_end = max(max_end, end)
            segments.append(
                {
                    "text": text,
                    "start_time": start,
                    "end_time": end,
                    "confidence": seg.get("confidence", None),
                    "words": seg.get("words", None),
                }
            )

        payload = {"segments": segments, "duration": max_end}
        out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        _emit({"type": "done", "segments": len(segments), "duration": max_end, "out": str(out_path)})
        return 0

    except Exception as e:
        _emit({"type": "error", "message": str(e)})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

