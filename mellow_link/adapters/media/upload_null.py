"""
Compatibility module alias for null upload adapter.
"""

import sys
from mellow_link.media.adapters import upload_null as _canonical

sys.modules[__name__] = _canonical
