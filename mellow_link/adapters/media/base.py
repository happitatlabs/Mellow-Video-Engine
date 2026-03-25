"""
Compatibility module alias for media adapter interfaces.
"""

import sys
from mellow_link.media.adapters import base as _canonical

sys.modules[__name__] = _canonical
