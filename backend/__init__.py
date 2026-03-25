"""
Backend package exports.

Only the audio path is guaranteed to be part of the current maintained runtime.
Legacy video composition modules are intentionally not re-exported from this
package to avoid accidental use.
"""

from __future__ import annotations

from .audio_engine import (
    LyricAligner,
    LyricSegment,
    ModelSize,
    WordSegment,
    format_timestamp,
    merge_segments,
    split_long_segments,
)

__all__ = [
    "LyricAligner",
    "LyricSegment",
    "ModelSize",
    "WordSegment",
    "format_timestamp",
    "merge_segments",
    "split_long_segments",
]
