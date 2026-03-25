# Legacy Manifest

Date: 2026-03-25

This manifest lists repository paths that are not part of the maintained
runtime boundary and should not be used as current implementation references.

## Deprecated Entry Points

- `main.py`
- `tui_main.py`

## Legacy Runtime / Reference Code

- `ui/`
- `core/`
- `backend/old_video_engine.py`
- root `workflows/`

## Historical Output / Asset Areas

- `assets/`
- root `output/`

## Stale Test Suites

- `tests/test_backend_video.py`
- `tests/test_ui_integration.py`

## Usage Policy

- Do not use these paths as product entry points.
- Do not add new maintained runtime features to these paths.
- Treat them as forensic reference, migration residue, or phased removal
  candidates.
- Prefer `web_ui.py`, `api_server.py`, and the shared `mellow_link` runtime
  services for all maintained work.
