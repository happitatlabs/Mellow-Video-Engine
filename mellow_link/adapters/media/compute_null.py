"""
Compatibility module alias for null compute adapter.
"""

import sys
from mellow_link.media.adapters import compute_null as _canonical

sys.modules[__name__] = _canonical
