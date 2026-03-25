"""
Compatibility module alias for Comfy media adapter.
"""

import sys
from mellow_link.media.adapters import ai_comfy as _canonical

sys.modules[__name__] = _canonical
