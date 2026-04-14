"""Gate B2: Video keyframe extraction validation.

Validates VideoProcessor with real video files:
- Generate a test video using PyAV
- Extract keyframes
- Verify at least 1 keyframe per video
- Verify JPEG output format
"""

from __future__ import annotations

from pathlib import Path

import numpy as np


def create_test_video(path: Path, num_frames: int = 30, fps: int = 10) -> Path:
    """Create a test video with changing frames for scene detection."""
    import av

    container = av.open(str(path), mode="w")
    stream = container.add_stream("mpeg4", rate=fps)
    stream.width = 320
    stream.height = 240
    stream.pix_fmt = "yuv420p"

    for i in range(num_frames):
        # Create frames with distinct colors to trigger scene detection
        r = int((i / num_frames) * 255)
        g = int(((num_frames - i) / num_frames) * 255)
        arr = np.zeros((stream.height, stream.width, 3), dtype=np.uint8)
        arr[:, :, 0] = r
        arr[:, :, 1] = g
        arr[:, :, 2] = 128

        frame = av.VideoFrame.from_ndarray(arr, format="rgb24")
        for packet in stream.encode(frame):
            container.mux(packet)

    for packet in stream.encode():
        container.mux(packet)

    container.close()
    return path


class TestGateB2VideoKeyframes:
    """Validate video keyframe extraction end-to-end."""

    def test_extract_keyframes_real_video(self, tmp_path: Path) -> None:
        """Extract keyframes from a real video file."""
        from arrow_lake.ingest.media import VideoProcessor

        video_path = create_test_video(tmp_path / "test_video.mp4", num_frames=30, fps=10)
        assert video_path.exists()

        processor = VideoProcessor(scene_threshold=0.2, max_keyframes=10)
        result = processor.extract_keyframes(video_path)

        assert result.duration_ms > 0
        assert len(result.keyframes) >= 1
        assert result.keyframes[0].jpeg_bytes[:2] == b"\xff\xd8"  # JPEG magic

    def test_keyframe_timestamps_monotonic(self, tmp_path: Path) -> None:
        """Keyframe timestamps are monotonically increasing."""
        from arrow_lake.ingest.media import VideoProcessor

        video_path = create_test_video(tmp_path / "mono_video.mp4", num_frames=50, fps=10)
        processor = VideoProcessor(scene_threshold=0.1, max_keyframes=10)
        result = processor.extract_keyframes(video_path)

        if len(result.keyframes) > 1:
            timestamps = [kf.timestamp_ms for kf in result.keyframes]
            assert timestamps == sorted(timestamps), "Timestamps must be monotonic"

    def test_multiple_videos(self, tmp_path: Path) -> None:
        """Process multiple video files."""
        from arrow_lake.ingest.media import VideoProcessor

        paths = [
            create_test_video(tmp_path / f"vid_{i}.mp4", num_frames=20 + i * 10) for i in range(3)
        ]

        processor = VideoProcessor()
        results = [processor.extract_keyframes(p) for p in paths]

        for result in results:
            assert len(result.keyframes) >= 1
            assert result.duration_ms > 0

    def test_video_ingest_to_lance(self, tmp_path: Path) -> None:
        """Ingest video keyframes into Lance dataset."""
        from arrow_lake.ingest.ingestor import Ingestor
        from arrow_lake.ingest.storage import LanceStorageManager

        video_path = create_test_video(tmp_path / "ingest_vid.mp4", num_frames=30)
        storage = LanceStorageManager(str(tmp_path / "lance_data"))
        ingestor = Ingestor(manager=storage)

        report = ingestor.ingest_videos("video_ds", [str(video_path)])
        assert report.total_files == 1
        assert report.total_rows == 1

        table = storage.read_dataset("video_ds")
        assert "video_data" in table.column_names
        assert "keyframe_count" in table.column_names
