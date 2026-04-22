"""Tests for video keyframe extraction — Story 3.4 (unit).

Tests VideoProcessor data structures and error handling.
Real video tests are in integration tests.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest
from arrow_lake.exceptions import ErrorCode, IngestError
from arrow_lake.ingest.media import ExtractedKeyframe, VideoIngestResult, VideoProcessor


class TestExtractedKeyframe:
    """Test ExtractedKeyframe frozen dataclass."""

    def test_keyframe_is_frozen(self) -> None:
        kf = ExtractedKeyframe(jpeg_bytes=b"data", timestamp_ms=1000)
        with pytest.raises(AttributeError):
            kf.timestamp_ms = 2000  # type: ignore[misc]

    def test_keyframe_fields(self) -> None:
        kf = ExtractedKeyframe(jpeg_bytes=b"\xff\xd8\xff", timestamp_ms=5000)
        assert kf.jpeg_bytes == b"\xff\xd8\xff"
        assert kf.timestamp_ms == 5000


class TestVideoIngestResult:
    """Test VideoIngestResult frozen dataclass."""

    def test_result_is_frozen(self) -> None:
        result = VideoIngestResult(
            keyframes=(ExtractedKeyframe(b"data", 0),),
            duration_ms=10000,
            scene_detection_used=True,
        )
        with pytest.raises(AttributeError):
            result.duration_ms = 5000  # type: ignore[misc]

    def test_result_with_empty_keyframes(self) -> None:
        result = VideoIngestResult(
            keyframes=(),
            duration_ms=0,
            scene_detection_used=False,
        )
        assert len(result.keyframes) == 0


class TestVideoProcessor:
    """Test VideoProcessor error handling."""

    def test_nonexistent_file_raises(self, tmp_path: Path) -> None:
        processor = VideoProcessor()
        with pytest.raises(FileNotFoundError):
            processor.extract_keyframes(tmp_path / "nonexistent.mp4")

    def test_corrupted_file_raises_ingest_error(self, tmp_path: Path) -> None:
        bad_path = tmp_path / "corrupted.mp4"
        bad_path.write_bytes(b"not a real video file")

        processor = VideoProcessor()
        with pytest.raises(IngestError) as exc_info:
            processor.extract_keyframes(bad_path)
        assert exc_info.value.error_code == ErrorCode.VIDEO_DECODE_FAILED

    def test_scene_detection_fallback_sets_false(self) -> None:
        """When scene detection fails, scene_detection_used should be False."""
        from unittest.mock import patch

        from PIL import Image

        processor = VideoProcessor()
        arr = np.array(Image.new("RGB", (50, 50), color="red"))

        mock_frame = MagicMock()
        mock_frame.pts = 0
        mock_frame.time_base = MagicMock()
        mock_frame.time_base.__truediv__ = lambda self, x: 1 / 90000
        mock_frame.to_ndarray.return_value = arr

        def make_container():
            container = MagicMock()
            container.streams = [MagicMock(type="video", duration=5000)]
            container.decode.return_value = iter([mock_frame])
            return container

        with (
            patch("pathlib.Path.exists", return_value=True),
            patch(
                "av.open",
                side_effect=[make_container(), make_container()],
            ),
            patch.object(
                processor,
                "_detect_scenes",
                side_effect=RuntimeError("scene detection failed"),
            ),
        ):
            result = processor.extract_keyframes("/fake/video.mp4")
            assert result.scene_detection_used is False

    def test_processor_init_defaults(self) -> None:
        processor = VideoProcessor()
        assert processor.scene_threshold == 0.3
        assert processor.max_keyframes == 20
        assert processor.keyframe_size == 720

    def test_processor_init_custom(self) -> None:
        processor = VideoProcessor(
            scene_threshold=0.5,
            max_keyframes=10,
            keyframe_size=480,
        )
        assert processor.scene_threshold == 0.5
        assert processor.max_keyframes == 10
        assert processor.keyframe_size == 480

    def test_scene_detection_with_mock_frames(self) -> None:
        """Test _detect_scenes with mocked video frames."""
        from PIL import Image

        processor = VideoProcessor(scene_threshold=0.1, max_keyframes=5)

        # Create mock container and stream
        mock_container = MagicMock()
        mock_stream = MagicMock()

        # Generate frames with distinct histograms
        frames = []
        for i in range(3):
            arr = np.array(Image.new("RGB", (100, 100), color=(i * 80, i * 80, i * 80)))
            mock_frame = MagicMock()
            mock_frame.pts = i * 1000
            mock_frame.time_base = MagicMock()
            mock_frame.time_base.__truediv__ = lambda self, x: 1 / 90000
            mock_frame.to_ndarray.return_value = arr
            frames.append(mock_frame)

        mock_container.decode.return_value = iter(frames)

        keyframes = processor._detect_scenes(mock_container, mock_stream)
        assert len(keyframes) >= 1
        assert all(isinstance(kf, ExtractedKeyframe) for kf in keyframes)

    def test_extract_first_frame_fallback(self) -> None:
        """Test _extract_first_frame returns first frame only."""
        from PIL import Image

        processor = VideoProcessor()

        mock_container = MagicMock()
        mock_stream = MagicMock()

        arr = np.array(Image.new("RGB", (50, 50), color="red"))
        mock_frame = MagicMock()
        mock_frame.pts = 0
        mock_frame.time_base = MagicMock()
        mock_frame.time_base.__truediv__ = lambda self, x: 1 / 90000
        mock_frame.to_ndarray.return_value = arr

        mock_container.decode.return_value = iter([mock_frame])

        keyframes = processor._extract_first_frame(mock_container, mock_stream)
        assert len(keyframes) == 1
        assert keyframes[0].timestamp_ms >= 0
        assert keyframes[0].jpeg_bytes[:2] == b"\xff\xd8"

    def test_extract_first_frame_empty_video(self) -> None:
        """Test _extract_first_frame with no frames returns empty list."""
        processor = VideoProcessor()

        mock_container = MagicMock()
        mock_stream = MagicMock()
        mock_container.decode.return_value = iter([])

        keyframes = processor._extract_first_frame(mock_container, mock_stream)
        assert keyframes == []
