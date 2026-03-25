# Structure Normalization Notes

Date: 2026-03-25
Updated: 2026-03-25

This document reflects the current maintained runtime after the repository
shifted away from the historical FSM/TUI pipeline and into the shared
web/API-plus-services architecture.

## 1. Official Baseline

The official maintained runtime baseline is:

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

`main.py` is a deprecation shim and is not an application entry point.

## 2. Maintained Runtime Shape

### Web path

- `web_ui.py` is the maintained Gradio entry point
- it uses `VisualPlanner`, `ImageService`, and `VideoService`
- it keeps a fixed `MAX_SCENES = 20` UI and maps scene models into slot-based
  output tuples

### API path

- `api_server.py` is the maintained FastAPI entry point
- it exposes session-based scene planning, image/video generation, merge
  endpoints, and file serving
- it also serves local operator pages at `/` and `/workspace`

### Audio/transcription path

- `backend/audio_engine.py` remains the maintained alignment/transcription core
- `backend/transcribe_worker.py` runs transcription in a separate process so a
  transcription crash does not kill the main server process

### Shared service path

- `runtime_config.py` centralizes config loading and output directory mapping
- `runtime_readiness.py` centralizes ComfyUI and media-AI readiness checks
- `semantic_scene_extractor.py` derives structured hints from lyric segments
- `visual_planner.py` builds structured scenes and prompt-ready payloads
- `prompt_policy.py` sanitizes, validates, and downgrades unsafe prompts
- `output_provenance.py` writes, verifies, and moves sidecar metadata

## 3. Legacy / Deprecated / Stale Classification

### Deprecated entry points

- `main.py`
- `tui_main.py`

### Legacy runtime or reference code

- `ui/`
- `core/`
- `backend/old_video_engine.py`
- root `workflows/`

These paths remain in the repository for reference, phased cleanup, or old
tests/examples. They are not part of the maintained runtime path.

### Stale tests

- `tests/test_backend_video.py`
- `tests/test_ui_integration.py`

These suites are intentionally skipped to keep the legacy boundary explicit.

## 4. Output Boundary Mapping

The maintained runtime writes to a single output root resolved by
`runtime_config.py`:

- `outputs/uploads/`
- `outputs/transcripts/`
- `outputs/images/`
- `outputs/videos/`
- `outputs/final/`

Compatibility note:

- `/outputs` is the current FastAPI static mount
- `/output` is kept as a deprecated alias to the same root

Historical/reference data remains outside the canonical runtime root:

- `assets/`
- legacy files already present under root `output/`
- historical JSON exports such as `assets/generated_images/scene_plans.json`

## 5. Planner and Prompt Policy Wiring

The maintained planning path is now:

1. lyrics are transcribed or aligned
2. `semantic_scene_extractor.py` derives structured scene hints
3. `visual_planner.py` builds structured scene payloads
4. `prompt_policy.py` sanitizes and validates prompts
5. generation services consume the sanitized prompts

Policy behavior in the current runtime:

- `config/prompts.yaml` is loaded by `prompt_policy.py`
- semantic extraction may preserve human-related lyric meaning on purpose
- runtime prompt policy then rewrites those signals into no-human prompts
- explicit collisions after sanitation trigger fail-safe downgrade behavior
- `negative_prompt` is passed through to both image and video requests

This is best-effort runtime enforcement, not a formal guarantee against every
possible model output.

## 6. Video Workflow Normalization

The maintained video role model is:

- `LOCAL_MOTION_LOOP`
- `AMBIENT_STILL_LOOP`

Compatibility note:

- `VIDEO_LOCKED_CAMERA` remains accepted as a deprecated alias
- it now normalizes to `AMBIENT_STILL_LOOP`
- maintained web/API flows default to `LOCAL_MOTION_LOOP`

Workflow files used by the maintained runtime live under
`mellow_link/data/workflows/`, not root `workflows/`.

Operational support scripts related to this path:

- `scripts/motion_video_spike.py`
- `scripts/ltx_local_enablement.py`

## 7. Prompt Metadata and Workflow Tokens

- workflow JSON files may still contain replacement tokens that are filled
  before queueing a ComfyUI workflow
- generated PNGs may include ComfyUI prompt metadata unless disabled
- metadata stripping is controlled by `config/settings.yaml`:
  `outputs.strip_prompt_metadata`

## 8. Multi-Slot UI Behavior

`web_ui.py` still renders a fixed `MAX_SCENES = 20` UI, but the maintained
internal shape is now:

- scene list / scene view models
- list-to-slot update mapping
- UI tuple emission only at the final Gradio boundary

This preserves the current UI while keeping the internal behavior aligned with
the current planner/service boundary.

## 9. Provenance Sidecars

Generated runtime artifacts support sidecar provenance JSON files:

- image: `<file>.png.meta.json`
- video: `<file>.mp4.meta.json`
- final export: `<file>.mp4.meta.json`

The sidecar records:

- source input / session or project identity
- planner/runtime labels
- prompt/settings fingerprints
- prompt metadata strip setting
- prompt-policy validation status and fail-safe flag when present
- UTC timestamp

Operational guardrail:

- maintained write paths use best-effort sidecar creation and then verify the
  sidecar exists
- archive or move operations should carry the media file and its sidecar
  together

## 10. Operational Notes

For historical asset handling and legacy inventory, see:

- `docs/HISTORICAL_OUTPUT_POLICY.md`
- `docs/LEGACY_MANIFEST.md`
