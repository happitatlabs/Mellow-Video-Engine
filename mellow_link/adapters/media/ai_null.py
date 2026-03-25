"""
Compatibility module alias for null media AI adapter.
"""

import sys
from mellow_link.media.adapters import ai_null as _canonical

sys.modules[__name__] = _canonical
