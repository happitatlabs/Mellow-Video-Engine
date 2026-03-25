"""
Compatibility module alias for FFmpeg compute adapter.
"""

import sys
from mellow_link.media.adapters import compute_ffmpeg as _canonical

sys.modules[__name__] = _canonical
