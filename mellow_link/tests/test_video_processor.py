import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from mellow_link.services.video_processor import extend_video_if_needed


class TestVideoProcessor(unittest.TestCase):
    def test_missing_ffprobe_returns_original(self):
        """Compute adapter raises FileNotFoundError -> wrapper returns original path."""
        p = Path(__file__).resolve()
        mock_compute = MagicMock()
        mock_compute.extend_video_if_needed.side_effect = FileNotFoundError()
        with patch("mellow_link.services.video_processor._get_compute", return_value=mock_compute):
            out = extend_video_if_needed(p, target_duration=12.0)
        self.assertEqual(Path(out).resolve(), p)


if __name__ == "__main__":
    unittest.main()

