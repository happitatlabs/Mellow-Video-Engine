# Historical Output Policy

Date: 2026-03-25

## Current Canonical Runtime Outputs

The maintained runtime writes only to:

- `outputs/uploads/`
- `outputs/transcripts/`
- `outputs/images/`
- `outputs/videos/`
- `outputs/final/`

These directories are created and resolved through
`mellow_link/services/runtime_config.py` and should be treated as the current
operational state.

FastAPI serving note:

- `/outputs` is the current static mount
- `/output` remains available only as a deprecated compatibility alias

## Historical / Reference Areas

The following locations are historical, compatibility-oriented, or manually
curated and should not be treated as canonical runtime state:

- `assets/`
- legacy files already present under root `output/`
- older JSON exports such as `assets/generated_images/scene_plans.json`

## Retention / Segregation Guidance

No bulk deletion is performed by this documentation update. The operational
policy is:

- Keep current runtime outputs in `outputs/`
- Treat `assets/` as historical/reference data only
- Treat root `output/` as deprecated compatibility residue only
- When archiving, move the artifact and its `.meta.json` sidecar together

Recommended archive buckets:

- `outputs/_archive/YYYYMMDD_<label>/`
- `assets/_archive/YYYYMMDD_<label>/`

## Naming Guidance

When archiving or preserving historical data, prefer names that encode:

- date
- source flow (`api`, `web_ui`, `manual`, `legacy`)
- reason (`smoke`, `debug`, `before_cleanup`, `reference`)

Examples:

- `outputs/_archive/20260325_web_ui_reference/`
- `assets/_archive/20260325_legacy_scene_plans/`

## Provenance Preference

Current runtime artifacts should prefer sidecar provenance JSON files over
embedding extra state into the media file itself.
