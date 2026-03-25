# Mellow-Video-Engine

Current repository baseline as of 2026-03-25.

The maintained product boundary is the web/API runtime backed by shared
`mellow_link` services. The repository still contains legacy TUI/FSM code and
historical assets, but those are no longer the source of truth.

## Official Entry Points

Use one of these maintained entry points:

- Web UI: `.\.venv\Scripts\python.exe web_ui.py`
- API server: `.\.venv\Scripts\python.exe -m uvicorn api_server:app --reload`

`main.py` is deprecated. It is now only a shim that prints a deprecation
message because the historical FSM pipeline depended on deleted `modules/*`
paths.

## Python Environment

Use the repository virtual environment for maintained runtime commands.

- Required interpreter: `D:\Mellow-Video-Engine\.venv\Scripts\python.exe`
- Do not use the system `python` for `web_ui.py` / `api_server.py`
- Runtime dependencies such as `faster_whisper`, FastAPI, and Gradio are
  expected to live in `.venv`

Recommended activation on PowerShell:

```powershell
cd D:\Mellow-Video-Engine
.\.venv\Scripts\Activate.ps1
```

## Minimum Startup Order

For actual image/video generation, the maintained runtime currently needs all
of the following:

- ComfyUI running and reachable
- `ENABLE_MEDIA_AI=1` in the environment used by Python
- `ffmpeg` available at the configured path or on `PATH`

Recommended startup order:

1. Open PowerShell in `D:\Mellow-Video-Engine`.
2. Activate `.venv` or call `.venv\Scripts\python.exe` directly.
3. Start ComfyUI first.
4. Confirm the ComfyUI API responds on the configured endpoint.
5. Ensure `.env` contains `ENABLE_MEDIA_AI=1`.
6. Ensure `ffmpeg` is available for final merge/export.
7. Start one maintained entry point:
   - `.\.venv\Scripts\python.exe web_ui.py`
   - or `.\.venv\Scripts\python.exe -m uvicorn api_server:app --reload`

Canonical commands:

```powershell
cd D:\Mellow-Video-Engine
.\.venv\Scripts\python.exe web_ui.py
```

```powershell
cd D:\Mellow-Video-Engine
.\.venv\Scripts\python.exe -m uvicorn api_server:app --reload
```

## Runtime Readiness

`web_ui.py`, `api_server.py`, and `mellow_link.media.adapters.ai_comfy` now
share the same runtime readiness rules through
`mellow_link/services/runtime_readiness.py` and
`mellow_link/services/runtime_config.py`.

Startup readiness is judged by the same checks in both web and API flows:

- `ENABLE_MEDIA_AI=1`
- ComfyUI `/system_stats` reachable
- ComfyUI WebSocket `/ws` reachable

If those checks fail, the maintained runtime is expected to fail early with a
clear readiness error instead of deferring the failure until the first
generation request.

## Repository Status

This repository currently contains both maintained and legacy areas.

Maintained runtime paths:

- `web_ui.py`
- `api_server.py`
- `backend/audio_engine.py`
- `backend/transcribe_worker.py`
- `config/settings.yaml`
- `config/prompts.yaml`
- `mellow_link/services/runtime_config.py`
- `mellow_link/services/runtime_readiness.py`
- `mellow_link/services/semantic_scene_extractor.py`
- `mellow_link/services/visual_planner.py`
- `mellow_link/services/prompt_policy.py`
- `mellow_link/services/output_provenance.py`
- `mellow_link/media/services/image_service.py`
- `mellow_link/media/services/video_service.py`
- `mellow_link/data/workflows/`

Legacy or deprecated paths still present in the tree:

- `main.py`
- `tui_main.py`
- `ui/`
- `core/`
- `backend/old_video_engine.py`
- root `workflows/`
- historical `output/`

## Runtime Layout

- `web_ui.py`
  - Maintained Gradio runtime
  - Builds the current scene editor / generation UI
  - Uses `VisualPlanner`, `ImageService`, and `VideoService`
  - Keeps a fixed `MAX_SCENES = 20` slot layout and emits large UI update tuples
- `api_server.py`
  - Maintained FastAPI runtime
  - Exposes session-based lyrics, planning, image/video generation, and merge APIs
  - Also serves a minimal local HTML page at `/` and a workspace-style page at
    `/workspace`
  - Serves both `/outputs` and deprecated `/output` aliases
- `backend/audio_engine.py`
  - Maintained transcription/alignment helper based on `faster_whisper`
- `backend/transcribe_worker.py`
  - Separate worker process for transcription so worker crashes do not take down
    the web/API server process
- `mellow_link/services/semantic_scene_extractor.py`
  - Extracts structured scene hints from lyric segments before prompt generation
- `mellow_link/services/visual_planner.py`
  - Builds current scene plans from lyrics, metadata, semantic extraction, and
    runtime prompt policy
- `mellow_link/services/prompt_policy.py`
  - Applies no-human sanitation, negative prompt wiring, validation, and
    fail-safe downgrade behavior
- `mellow_link/services/output_provenance.py`
  - Writes and verifies sidecar provenance JSON files next to generated media
- `mellow_link/services/runtime_config.py`
  - Centralized config/prompts loader and output directory resolver
- `mellow_link/media/services/image_service.py`
  - Maintained image generation service writing into `outputs/images/`
- `mellow_link/media/services/video_service.py`
  - Maintained video generation service using workflow files under
    `mellow_link/data/workflows/`

## API Surface

The maintained API server currently exposes these operational paths:

- `GET /api/health`
- `POST /api/lyrics`
- `POST /api/session/{session_id}/scenes/plan`
- `POST /api/session/{session_id}/scene/{scene_index}/image`
- `POST /api/session/{session_id}/scene/{scene_index}/video`
- `POST /api/session/{session_id}/merge`
- `GET /files`

The root page and `/workspace` page are local operator helpers, not separate
product baselines.

## Planner Pipeline

The maintained planner path is now:

1. lyric segments are transcribed or aligned
2. `semantic_scene_extractor` derives structured hints
3. `VisualPlanner` builds scene payloads
4. `prompt_policy` sanitizes human signals, adds negative prompts, and validates
   the result
5. image/video services consume the sanitized prompts

Important nuance:

- semantic extraction may preserve human-related meaning from lyrics
- prompt policy then rewrites or downgrades those signals so runtime prompts stay
  aligned with the no-human policy
- this is intentional and covered by the current semantic/policy tests

## Video Engine Roles

The maintained runtime treats video generation as two separate roles:

- `LOCAL_MOTION_LOOP`
  - Official default web/API video path
  - Uses the local-motion LTX workflow configured in `config/settings.yaml`
  - Interprets the motion prompt as "what moves locally while the frame stays
    steady"
- `AMBIENT_STILL_LOOP`
  - Ambient-loop path for subtle static-camera animation
  - Used when the runtime or request selects the ambient locked-camera backend

Compatibility note:

- `VIDEO_LOCKED_CAMERA` remains accepted in code as a deprecated alias
- it now normalizes to `AMBIENT_STILL_LOOP`
- maintained web/API paths default to `LOCAL_MOTION_LOOP`

Operational tools:

- `scripts/motion_video_spike.py`
- `scripts/ltx_local_enablement.py`

These scripts support the current local-motion workflow bring-up, but the
product entry points remain `web_ui.py` and `api_server.py`.

## Output Directory Mapping

Actual output boundaries in the maintained runtime are unified under one root:

- `outputs/uploads/`
  - Uploaded source audio
- `outputs/transcripts/`
  - Transcription worker JSON output
- `outputs/images/`
  - Generated still images
- `outputs/videos/`
  - Generated clips and temporary video artifacts
- `outputs/final/`
  - Final merged exports

Compatibility note:

- `/outputs` is the current served static mount in the API
- `/output` is kept as a deprecated alias for older links
- maintained code writes to `outputs/`

Historical/reference areas:

- `assets/`
- root `output/`
- older JSON exports such as `assets/generated_images/scene_plans.json`

## Prompt Policy Gap

The earlier prompt-policy gap in historical artifacts has been closed in the
maintained planner/runtime path.

Current behavior:

- `config/prompts.yaml` defines the no-human policy and negative prompt base
- `VisualPlanner` enriches scenes with semantic summaries before prompt
  generation
- `prompt_policy` sanitizes scene descriptions, validates prompt safety, and
  applies a fail-safe downgrade when needed
- `negative_prompt` is passed through to both image and video requests

Remaining nuance:

- policy enforcement is still best-effort runtime logic, not a formal model
  guarantee
- semantic extraction intentionally preserves lyric meaning before sanitization
- historical assets are not rewritten retroactively

## Prompt Metadata and `%PROMPT%`

- No `%PROMPT%` literal is expected in maintained Python source paths
- workflow JSON files may still contain replacement tokens that are filled
  before queueing a ComfyUI workflow
- generated PNGs may include ComfyUI prompt metadata unless disabled
- this is controlled by `config/settings.yaml -> outputs.strip_prompt_metadata`
- default is `false`

If prompt contamination is suspected, inspect generated PNG metadata and the
prompt strings passed through `ImageRequest` / `VideoRequest`.

## Provenance

Generated artifacts can include sidecar provenance JSON files:

- image: `<file>.png.meta.json`
- video: `<file>.mp4.meta.json`
- final export: `<file>.mp4.meta.json`

Each sidecar records source identity, runtime/planner labels, prompt/settings
fingerprints, policy state, the metadata-strip flag, and a UTC timestamp.

Operational guardrail:

- maintained write paths use best-effort sidecar creation plus follow-up
  verification
- archive or move operations should carry the artifact and its sidecar together

## Tests

The root test suite now follows the current product boundary.

Active coverage includes:

- official-path smoke checks for `main.py`, `web_ui.py`, `api_server.py`, and
  current runtime config
- semantic extraction and prompt-policy integration tests
- a minimal end-to-end pipeline test covering image generation, video
  generation, final merge, and provenance sidecars
- audio utility coverage for the maintained alignment path

Intentionally skipped legacy suites:

- `tests/test_backend_video.py`
- `tests/test_ui_integration.py`

Run:

```bash
pytest -q
```

## Giant Tuple / Multi-Slot Update Model

`web_ui.py` still uses a preallocated `MAX_SCENES` slot model. It builds one
large output tuple covering:

- global status/state widgets
- scene visibility
- scene lyric markdown
- scene image prompt input
- scene video prompt input
- scene image output
- scene video output

This remains documented current behavior, not an unexplained anomaly.

## Historical and Legacy Policy

For historical outputs and legacy inventory:

- [docs/STRUCTURE_NORMALIZATION.md](/D:/Mellow-Video-Engine/docs/STRUCTURE_NORMALIZATION.md)
- [docs/HISTORICAL_OUTPUT_POLICY.md](/D:/Mellow-Video-Engine/docs/HISTORICAL_OUTPUT_POLICY.md)
- [docs/LEGACY_MANIFEST.md](/D:/Mellow-Video-Engine/docs/LEGACY_MANIFEST.md)

Archive operations should move the artifact and its sidecar together into
`outputs/_archive/YYYYMMDD_<label>/`.
