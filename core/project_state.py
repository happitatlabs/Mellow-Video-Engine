"""
Project State Management
========================
Manages the current project data including lyrics, images, videos, and metadata.
Supports serialization to/from JSON for persistence and user modification.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Optional


class AssetStatus(Enum):
    """Status of generated assets."""
    PENDING = "pending"
    GENERATING = "generating"
    GENERATED = "generated"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    FAILED = "failed"


@dataclass
class LyricSegment:
    """Represents a single lyric segment with timing information."""
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    text: str = ""
    start_time: float = 0.0  # seconds
    end_time: float = 0.0    # seconds
    confidence: float = 1.0  # Whisper confidence score

    # User modifications
    is_modified: bool = False
    original_text: Optional[str] = None

    # Visual mapping
    image_prompt: Optional[str] = None
    assigned_image_id: Optional[str] = None
    assigned_video_id: Optional[str] = None

    @property
    def duration(self) -> float:
        """Duration of this segment in seconds."""
        return self.end_time - self.start_time

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "id": self.id,
            "text": self.text,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "confidence": self.confidence,
            "is_modified": self.is_modified,
            "original_text": self.original_text,
            "image_prompt": self.image_prompt,
            "assigned_image_id": self.assigned_image_id,
            "assigned_video_id": self.assigned_video_id,
        }

    @classmethod
    def from_dict(cls, data: dict) -> LyricSegment:
        """Create instance from dictionary."""
        return cls(**data)


@dataclass
class ImageAsset:
    """Represents a generated image asset."""
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    file_path: Optional[Path] = None
    prompt: str = ""
    negative_prompt: str = ""

    # Generation parameters
    seed: int = -1
    steps: int = 20
    cfg_scale: float = 7.5
    width: int = 1280
    height: int = 720

    # Status
    status: AssetStatus = AssetStatus.PENDING
    generation_time: Optional[datetime] = None
    error_message: Optional[str] = None

    # Linked segments
    segment_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "id": self.id,
            "file_path": str(self.file_path) if self.file_path else None,
            "prompt": self.prompt,
            "negative_prompt": self.negative_prompt,
            "seed": self.seed,
            "steps": self.steps,
            "cfg_scale": self.cfg_scale,
            "width": self.width,
            "height": self.height,
            "status": self.status.value,
            "generation_time": self.generation_time.isoformat() if self.generation_time else None,
            "error_message": self.error_message,
            "segment_ids": self.segment_ids,
        }

    @classmethod
    def from_dict(cls, data: dict) -> ImageAsset:
        """Create instance from dictionary."""
        data = data.copy()
        data["file_path"] = Path(data["file_path"]) if data.get("file_path") else None
        data["status"] = AssetStatus(data.get("status", "pending"))
        if data.get("generation_time"):
            data["generation_time"] = datetime.fromisoformat(data["generation_time"])
        return cls(**data)


@dataclass
class VideoClip:
    """Represents a generated video clip."""
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    file_path: Optional[Path] = None
    source_image_id: Optional[str] = None

    # Video parameters
    duration: float = 4.0  # seconds
    fps: int = 24
    motion_type: str = "slow_zoom"
    motion_params: dict = field(default_factory=dict)

    # Status
    status: AssetStatus = AssetStatus.PENDING
    generation_time: Optional[datetime] = None
    error_message: Optional[str] = None

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "id": self.id,
            "file_path": str(self.file_path) if self.file_path else None,
            "source_image_id": self.source_image_id,
            "duration": self.duration,
            "fps": self.fps,
            "motion_type": self.motion_type,
            "motion_params": self.motion_params,
            "status": self.status.value,
            "generation_time": self.generation_time.isoformat() if self.generation_time else None,
            "error_message": self.error_message,
        }

    @classmethod
    def from_dict(cls, data: dict) -> VideoClip:
        """Create instance from dictionary."""
        data = data.copy()
        data["file_path"] = Path(data["file_path"]) if data.get("file_path") else None
        data["status"] = AssetStatus(data.get("status", "pending"))
        if data.get("generation_time"):
            data["generation_time"] = datetime.fromisoformat(data["generation_time"])
        return cls(**data)


@dataclass
class ProjectMetadata:
    """Project metadata for the music video."""
    title: str = ""
    artist: str = ""
    song_title: str = ""
    lyricist: str = ""
    composer: str = ""
    mood: str = ""
    story_description: str = ""

    # Localized content
    translations: dict[str, dict] = field(default_factory=dict)
    # Format: {"en": {"title": "...", "description": "..."}, "ja": {...}}

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> ProjectMetadata:
        return cls(**data)


@dataclass
class ProjectState:
    """
    Main project state container.
    Holds all data for a music video generation project.
    """
    # Project identification
    project_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    project_name: str = "Untitled Project"
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)

    # Audio source
    audio_file_path: Optional[Path] = None
    audio_duration: float = 0.0

    # Extracted content
    lyrics_segments: list[LyricSegment] = field(default_factory=list)

    # Generated assets
    images: dict[str, ImageAsset] = field(default_factory=dict)
    video_clips: dict[str, VideoClip] = field(default_factory=dict)

    # Final output
    final_video_path: Optional[Path] = None

    # Metadata
    metadata: ProjectMetadata = field(default_factory=ProjectMetadata)

    # FSM current state tracking
    current_state: str = "INIT"
    state_history: list[dict] = field(default_factory=list)

    def add_lyric_segment(self, segment: LyricSegment) -> None:
        """Add a lyric segment to the project."""
        self.lyrics_segments.append(segment)
        self._update_timestamp()

    def update_lyric_segment(self, segment_id: str, **kwargs) -> bool:
        """Update a lyric segment by ID."""
        for segment in self.lyrics_segments:
            if segment.id == segment_id:
                for key, value in kwargs.items():
                    if hasattr(segment, key):
                        if key == "text" and segment.text != value:
                            segment.original_text = segment.original_text or segment.text
                            segment.is_modified = True
                        setattr(segment, key, value)
                self._update_timestamp()
                return True
        return False

    def add_image(self, image: ImageAsset) -> None:
        """Add an image asset to the project."""
        self.images[image.id] = image
        self._update_timestamp()

    def add_video_clip(self, clip: VideoClip) -> None:
        """Add a video clip to the project."""
        self.video_clips[clip.id] = clip
        self._update_timestamp()

    def get_confirmed_images(self) -> list[ImageAsset]:
        """Get all confirmed images."""
        return [img for img in self.images.values() if img.status == AssetStatus.CONFIRMED]

    def get_segments_for_time_range(self, start: float, end: float) -> list[LyricSegment]:
        """Get all lyric segments within a time range."""
        return [
            seg for seg in self.lyrics_segments
            if seg.start_time < end and seg.end_time > start
        ]

    def record_state_transition(self, from_state: str, to_state: str, details: dict = None) -> None:
        """Record a state transition in history."""
        self.state_history.append({
            "from": from_state,
            "to": to_state,
            "timestamp": datetime.now().isoformat(),
            "details": details or {},
        })
        self.current_state = to_state
        self._update_timestamp()

    def _update_timestamp(self) -> None:
        """Update the modification timestamp."""
        self.updated_at = datetime.now()

    def to_dict(self) -> dict:
        """Convert entire project state to dictionary."""
        # Helper function to safely convert images/video_clips values
        def safe_to_dict(value):
            """Convert value to dict, handling both objects and dicts."""
            if isinstance(value, dict):
                return value
            elif hasattr(value, 'to_dict'):
                return value.to_dict()
            else:
                # Fallback: convert to string representation
                return str(value)
        
        result = {
            "project_id": self.project_id,
            "project_name": self.project_name,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "audio_file_path": str(self.audio_file_path) if self.audio_file_path else None,
            "audio_duration": self.audio_duration,
            "lyrics_segments": [seg.to_dict() for seg in self.lyrics_segments],
            "images": {k: safe_to_dict(v) for k, v in self.images.items()},
            "video_clips": {k: safe_to_dict(v) for k, v in self.video_clips.items()},
            "final_video_path": str(self.final_video_path) if self.final_video_path else None,
            "metadata": self.metadata.to_dict(),
            "current_state": self.current_state,
            "state_history": self.state_history,
        }
        
        # Add scene_plans if it exists (dynamic attribute)
        if hasattr(self, 'scene_plans') and self.scene_plans:
            result["scene_plans"] = self.scene_plans
        
        # Add visual_plans if it exists (alternative name)
        if hasattr(self, 'visual_plans') and self.visual_plans:
            result["visual_plans"] = self.visual_plans
        
        # Add generated_images if it exists
        if hasattr(self, 'generated_images') and self.generated_images:
            result["generated_images"] = self.generated_images
        
        return result

    @classmethod
    def from_dict(cls, data: dict) -> ProjectState:
        """Create project state from dictionary."""
        state = cls()
        state.project_id = data.get("project_id", state.project_id)
        state.project_name = data.get("project_name", state.project_name)
        state.created_at = datetime.fromisoformat(data["created_at"]) if data.get("created_at") else state.created_at
        state.updated_at = datetime.fromisoformat(data["updated_at"]) if data.get("updated_at") else state.updated_at
        state.audio_file_path = Path(data["audio_file_path"]) if data.get("audio_file_path") else None
        state.audio_duration = data.get("audio_duration", 0.0)
        state.lyrics_segments = [LyricSegment.from_dict(seg) for seg in data.get("lyrics_segments", [])]
        state.images = {k: ImageAsset.from_dict(v) for k, v in data.get("images", {}).items()}
        state.video_clips = {k: VideoClip.from_dict(v) for k, v in data.get("video_clips", {}).items()}
        state.final_video_path = Path(data["final_video_path"]) if data.get("final_video_path") else None
        state.metadata = ProjectMetadata.from_dict(data["metadata"]) if data.get("metadata") else ProjectMetadata()
        state.current_state = data.get("current_state", "INIT")
        state.state_history = data.get("state_history", [])
        return state

    def save_to_file(self, file_path: Path) -> None:
        """Save project state to JSON file."""
        file_path = Path(file_path)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)

    @classmethod
    def load_from_file(cls, file_path: Path) -> ProjectState:
        """Load project state from JSON file."""
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return cls.from_dict(data)

    def export_lyrics_for_editing(self) -> str:
        """Export lyrics in editable JSON format for user modification."""
        export_data = {
            "project_id": self.project_id,
            "audio_file": str(self.audio_file_path) if self.audio_file_path else None,
            "segments": [
                {
                    "id": seg.id,
                    "text": seg.text,
                    "start_time": seg.start_time,
                    "end_time": seg.end_time,
                    "confidence": seg.confidence,
                }
                for seg in self.lyrics_segments
            ],
        }
        return json.dumps(export_data, ensure_ascii=False, indent=2)

    def import_lyrics_from_editing(self, json_str: str) -> None:
        """Import modified lyrics from user editing session."""
        data = json.loads(json_str)

        # Create a lookup for existing segments
        existing = {seg.id: seg for seg in self.lyrics_segments}

        # Update with imported data
        for seg_data in data.get("segments", []):
            seg_id = seg_data.get("id")
            if seg_id in existing:
                self.update_lyric_segment(
                    seg_id,
                    text=seg_data.get("text", existing[seg_id].text),
                    start_time=seg_data.get("start_time", existing[seg_id].start_time),
                    end_time=seg_data.get("end_time", existing[seg_id].end_time),
                )
            else:
                # New segment added by user
                new_segment = LyricSegment(
                    id=seg_id,
                    text=seg_data.get("text", ""),
                    start_time=seg_data.get("start_time", 0.0),
                    end_time=seg_data.get("end_time", 0.0),
                    is_modified=True,
                )
                self.add_lyric_segment(new_segment)

        # Sort segments by start time
        self.lyrics_segments.sort(key=lambda x: x.start_time)
