#!/usr/bin/env python3
"""
Deprecated legacy entry point for Mellow-Video-Engine.

The historical FSM pipeline in this repository depended on modules that are no
longer part of the maintained runtime. The official runtime paths are now:

- `python web_ui.py`
- `uvicorn api_server:app --reload`

This shim exists only to prevent broken legacy imports from acting like the
main application entry point.
"""

from __future__ import annotations

import sys


DEPRECATION_MESSAGE = """\
`main.py` is deprecated and is no longer the official Mellow-Video-Engine entry point.

Official entry points:
  - Web UI: `python web_ui.py`
  - API server: `uvicorn api_server:app --reload`

Reason:
  - The legacy FSM pipeline depended on deleted `modules/*` paths.
  - The maintained runtime now lives in `web_ui.py`, `api_server.py`, and the
    current planner/config path used by those files.

See `README.md` and `docs/STRUCTURE_NORMALIZATION.md` for the current layout.
"""


def main() -> int:
    sys.stderr.write(DEPRECATION_MESSAGE + "\n")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
