"""
Compatibility module alias for media factory.
"""

import sys
from mellow_link.media.adapters import factory as _canonical

sys.modules[__name__] = _canonical
