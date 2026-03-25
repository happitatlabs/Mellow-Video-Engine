"""
Compatibility shim for media request schemas.

Canonical definitions live in mellow_link.media.schemas.
"""

from mellow_link.media.schemas import ImageRequest, VideoRequest

__all__ = ["ImageRequest", "VideoRequest"]
