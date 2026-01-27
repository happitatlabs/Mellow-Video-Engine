"""
Asset Selector UI
=================
Widget for selecting, confirming, or regenerating generated assets.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Optional, Callable, Any

if TYPE_CHECKING:
    from core.project_state import ProjectState, ImageAsset, VideoClip

from core.project_state import AssetStatus

logger = logging.getLogger(__name__)


class AssetSelectorWidget:
    """
    Asset selector widget for reviewing generated images and videos.

    Features:
    - Grid view of generated assets
    - Preview images/videos
    - Confirm or reject individual assets
    - Request regeneration with modified prompts
    - Batch operations

    Note: This is a skeleton implementation.
    Actual UI can be implemented with PyQt6, Tkinter, or Web UI.
    """

    def __init__(
        self,
        project: ProjectState,
        asset_type: str = "images",  # "images" or "videos"
        on_confirm: Optional[Callable[[list[str]], None]] = None,
        on_regenerate: Optional[Callable[[list[str], dict], None]] = None,
        on_cancel: Optional[Callable[[], None]] = None,
    ):
        """
        Initialize AssetSelectorWidget.

        Args:
            project: Project state with assets
            asset_type: Type of assets to display ("images" or "videos")
            on_confirm: Callback when user confirms selections
            on_regenerate: Callback when user requests regeneration
            on_cancel: Callback when user cancels
        """
        self.project = project
        self.asset_type = asset_type
        self.on_confirm = on_confirm
        self.on_regenerate = on_regenerate
        self.on_cancel = on_cancel

        # Selection state
        self._selections: dict[str, str] = {}  # asset_id -> "confirmed" | "rejected" | "regenerate"
        self._regenerate_prompts: dict[str, str] = {}  # asset_id -> new prompt

        self.logger = logging.getLogger(self.__class__.__name__)

    def get_assets(self) -> list[dict]:
        """Get list of assets with current selections."""
        if self.asset_type == "images":
            assets = list(self.project.images.values())
        else:
            assets = list(self.project.video_clips.values())

        result = []
        for asset in assets:
            asset_dict = asset.to_dict()
            asset_dict["selection"] = self._selections.get(asset.id, "pending")
            asset_dict["new_prompt"] = self._regenerate_prompts.get(asset.id, "")
            result.append(asset_dict)

        return result

    def get_asset(self, asset_id: str) -> Optional[Any]:
        """Get specific asset by ID."""
        if self.asset_type == "images":
            return self.project.images.get(asset_id)
        else:
            return self.project.video_clips.get(asset_id)

    def confirm_asset(self, asset_id: str) -> bool:
        """
        Mark asset as confirmed.

        Args:
            asset_id: Asset ID to confirm

        Returns:
            True if successful
        """
        asset = self.get_asset(asset_id)
        if not asset:
            return False

        self._selections[asset_id] = "confirmed"

        # Update actual asset status
        asset.status = AssetStatus.CONFIRMED
        return True

    def reject_asset(self, asset_id: str) -> bool:
        """
        Mark asset as rejected.

        Args:
            asset_id: Asset ID to reject

        Returns:
            True if successful
        """
        asset = self.get_asset(asset_id)
        if not asset:
            return False

        self._selections[asset_id] = "rejected"
        asset.status = AssetStatus.REJECTED
        return True

    def request_regeneration(
        self,
        asset_id: str,
        new_prompt: Optional[str] = None,
    ) -> bool:
        """
        Request regeneration of an asset.

        Args:
            asset_id: Asset ID to regenerate
            new_prompt: Optional new prompt for regeneration

        Returns:
            True if successful
        """
        asset = self.get_asset(asset_id)
        if not asset:
            return False

        self._selections[asset_id] = "regenerate"
        if new_prompt:
            self._regenerate_prompts[asset_id] = new_prompt

        return True

    def confirm_all(self) -> None:
        """Confirm all generated assets."""
        if self.asset_type == "images":
            for asset_id, asset in self.project.images.items():
                if asset.status == AssetStatus.GENERATED:
                    self.confirm_asset(asset_id)
        else:
            for asset_id, asset in self.project.video_clips.items():
                if asset.status == AssetStatus.GENERATED:
                    self.confirm_asset(asset_id)

    def get_confirmed_ids(self) -> list[str]:
        """Get list of confirmed asset IDs."""
        return [
            asset_id for asset_id, status in self._selections.items()
            if status == "confirmed"
        ]

    def get_rejected_ids(self) -> list[str]:
        """Get list of rejected asset IDs."""
        return [
            asset_id for asset_id, status in self._selections.items()
            if status == "rejected"
        ]

    def get_regeneration_requests(self) -> list[dict]:
        """Get list of assets to regenerate with their prompts."""
        return [
            {
                "asset_id": asset_id,
                "new_prompt": self._regenerate_prompts.get(asset_id, ""),
            }
            for asset_id, status in self._selections.items()
            if status == "regenerate"
        ]

    def finalize(self) -> dict:
        """
        Finalize selections and return summary.

        Returns:
            Dictionary with confirmed, rejected, and regenerate lists
        """
        result = {
            "confirmed": self.get_confirmed_ids(),
            "rejected": self.get_rejected_ids(),
            "regenerate": self.get_regeneration_requests(),
        }

        if self.on_confirm:
            self.on_confirm(result["confirmed"])

        if self.on_regenerate and result["regenerate"]:
            self.on_regenerate(
                [r["asset_id"] for r in result["regenerate"]],
                {r["asset_id"]: r["new_prompt"] for r in result["regenerate"]},
            )

        return result

    def cancel(self) -> None:
        """Cancel selection process."""
        if self.on_cancel:
            self.on_cancel()

    # ========================================================================
    # CLI Interface
    # ========================================================================

    def run_cli(self) -> dict:
        """
        Run simple CLI interface for asset selection.

        Returns:
            Finalized selection dictionary
        """
        print(f"\n=== Asset Selector ({self.asset_type.title()}) ===")
        print(f"Project: {self.project.project_name}")

        assets = self.get_assets()
        print(f"Total assets: {len(assets)}")
        print()

        while True:
            self._print_assets(assets)
            print("\nCommands:")
            print("  [number] c - Confirm asset")
            print("  [number] r - Reject asset")
            print("  [number] g - Regenerate asset")
            print("  [number] v - View details")
            print("  a - Confirm all")
            print("  f - Finalize and continue")
            print("  q - Cancel")

            cmd = input("\nCommand: ").strip().lower()

            if cmd == "f":
                return self.finalize()
            elif cmd == "q":
                self.cancel()
                return {"confirmed": [], "rejected": [], "regenerate": []}
            elif cmd == "a":
                self.confirm_all()
                assets = self.get_assets()
            else:
                self._process_command(cmd, assets)
                assets = self.get_assets()

    def _print_assets(self, assets: list[dict]) -> None:
        """Print assets in CLI format."""
        print(f"\n{self.asset_type.title()}:")
        print("-" * 70)

        status_symbols = {
            "confirmed": "[+]",
            "rejected": "[-]",
            "regenerate": "[R]",
            "pending": "[ ]",
        }

        for i, asset in enumerate(assets, 1):
            status = status_symbols.get(asset.get("selection", "pending"), "[ ]")
            file_path = asset.get("file_path", "N/A")
            if file_path and len(file_path) > 30:
                file_path = "..." + file_path[-30:]

            if self.asset_type == "images":
                prompt = asset.get("prompt", "")[:40]
                print(f"{i:3d} {status} {file_path:<35} {prompt}...")
            else:
                motion = asset.get("motion_type", "N/A")
                duration = asset.get("duration", 0)
                print(f"{i:3d} {status} {file_path:<35} {motion} ({duration:.1f}s)")

    def _process_command(self, cmd: str, assets: list[dict]) -> None:
        """Process a CLI command."""
        parts = cmd.split()
        if len(parts) < 2:
            return

        try:
            idx = int(parts[0]) - 1
            action = parts[1]

            if 0 <= idx < len(assets):
                asset_id = assets[idx]["id"]

                if action == "c":
                    self.confirm_asset(asset_id)
                    print(f"Asset {idx + 1} confirmed")
                elif action == "r":
                    self.reject_asset(asset_id)
                    print(f"Asset {idx + 1} rejected")
                elif action == "g":
                    new_prompt = ""
                    if self.asset_type == "images":
                        print(f"Current prompt: {assets[idx].get('prompt', 'N/A')}")
                        new_prompt = input("New prompt (empty to keep): ").strip()
                    self.request_regeneration(asset_id, new_prompt or None)
                    print(f"Asset {idx + 1} marked for regeneration")
                elif action == "v":
                    self._print_asset_details(assets[idx])
        except (ValueError, IndexError):
            print("Invalid command")

    def _print_asset_details(self, asset: dict) -> None:
        """Print detailed asset information."""
        print("\n" + "=" * 50)
        print(f"Asset ID: {asset.get('id')}")
        print(f"File: {asset.get('file_path')}")
        print(f"Status: {asset.get('status')}")
        print(f"Selection: {asset.get('selection', 'pending')}")

        if self.asset_type == "images":
            print(f"\nPrompt: {asset.get('prompt')}")
            print(f"Negative: {asset.get('negative_prompt')}")
            print(f"Size: {asset.get('width')}x{asset.get('height')}")
            print(f"Steps: {asset.get('steps')}")
            print(f"Seed: {asset.get('seed')}")
        else:
            print(f"\nSource Image: {asset.get('source_image_id')}")
            print(f"Duration: {asset.get('duration')}s")
            print(f"FPS: {asset.get('fps')}")
            print(f"Motion Type: {asset.get('motion_type')}")
            print(f"Motion Params: {asset.get('motion_params')}")

        print("=" * 50)


class ImageGalleryWidget(AssetSelectorWidget):
    """Convenience wrapper for image selection."""

    def __init__(self, project: ProjectState, **kwargs):
        super().__init__(project, asset_type="images", **kwargs)


class VideoGalleryWidget(AssetSelectorWidget):
    """Convenience wrapper for video selection."""

    def __init__(self, project: ProjectState, **kwargs):
        super().__init__(project, asset_type="videos", **kwargs)
