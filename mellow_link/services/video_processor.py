"""
Compatibility shim for video processor helpers.

Canonical implementation lives in mellow_link.media.services.video_processor.
"""

from mellow_link.media.services import video_processor as _video_processor
from mellow_link.media.services.video_processor import *  # noqa: F401,F403

_get_compute = _video_processor._get_compute
