"""Media processing — Stories 3.3, 3.4.

Provides:
- ImageProcessor: resize, thumbnail, preview, EXIF extraction
  + Daft-native batch processing for bulk image operations
- VideoProcessor: keyframe extraction with scene detection
  + Daft-native video frame reading for keyframe extraction
"""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import PIL
from PIL import Image
from PIL.ExifTags import Base as ExifBase

from arrow_lake.exceptions import ErrorCode, IngestError

logger = logging.getLogger(__name__)

__all__ = [
    "ExtractedKeyframe",
    "ImageMetadata",
    "ImageProcessor",
    "ProcessedImage",
    "VideoIngestResult",
    "VideoProcessor",
    "should_retain_original",
]


# ---------------------------------------------------------------------------
# Image processing (Story 3.3)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ImageMetadata:
    """Extracted image metadata."""

    width: int
    height: int
    exif_make: str | None = None
    exif_model: str | None = None
    exif_gps_lat: float | None = None
    exif_gps_lon: float | None = None
    exif_capture_time: str | None = None


@dataclass(frozen=True)
class ProcessedImage:
    """Result of image processing."""

    original_bytes: bytes
    thumbnail_bytes: bytes
    preview_bytes: bytes
    metadata: ImageMetadata


@dataclass(frozen=True)
class ExtractedKeyframe:
    """A single extracted keyframe from a video."""

    jpeg_bytes: bytes
    timestamp_ms: int


@dataclass(frozen=True)
class VideoIngestResult:
    """Result of video keyframe extraction."""

    keyframes: tuple[ExtractedKeyframe, ...]
    duration_ms: int
    scene_detection_used: bool


def _exif_gps_to_decimal(gps_info: dict[int, tuple[object, ...]]) -> tuple[float, float] | None:
    """Convert EXIF GPS coordinates to decimal degrees."""
    try:
        gps_lat_ref = gps_info.get(1, ("N",))[0]
        gps_lat = gps_info.get(2, ((0, 1), (0, 1), (0, 1)))
        gps_lon_ref = gps_info.get(3, ("E",))[0]
        gps_lon = gps_info.get(4, ((0, 1), (0, 1), (0, 1)))

        def _to_degrees(value: tuple[object, ...]) -> float:
            d, m, s = value
            return float(int(d)) + float(int(m)) / 60.0 + float(int(s)) / 3600.0

        lat = _to_degrees(gps_lat) * (-1 if gps_lat_ref == "S" else 1)
        lon = _to_degrees(gps_lon) * (-1 if gps_lon_ref == "W" else 1)
        return lat, lon
    except (IndexError, TypeError, ZeroDivisionError):
        return None


def _extract_exif(img: Image.Image) -> ImageMetadata:
    """Extract EXIF metadata from a PIL Image."""
    width, height = img.size
    exif_make = None
    exif_model = None
    exif_gps_lat = None
    exif_gps_lon = None
    exif_capture_time = None

    try:
        raw_exif = img.getexif()
        if raw_exif:
            exif_make = raw_exif.get(ExifBase.Make)
            if exif_make is not None:
                exif_make = str(exif_make).strip()
            exif_model = raw_exif.get(ExifBase.Model)
            if exif_model is not None:
                exif_model = str(exif_model).strip()
            exif_capture_time = raw_exif.get(ExifBase.DateTimeOriginal)
            if exif_capture_time is not None:
                exif_capture_time = str(exif_capture_time).strip()

            gps_info = raw_exif.get(ExifBase.GPSInfo)
            if isinstance(gps_info, dict) and gps_info:
                coords = _exif_gps_to_decimal(gps_info)
                if coords is not None:
                    exif_gps_lat, exif_gps_lon = coords
    except (ValueError, TypeError, KeyError, OSError):
        # EXIF extraction should never fail the pipeline
        logger.debug("EXIF extraction failed, returning null fields")

    return ImageMetadata(
        width=width,
        height=height,
        exif_make=exif_make,
        exif_model=exif_model,
        exif_gps_lat=exif_gps_lat,
        exif_gps_lon=exif_gps_lon,
        exif_capture_time=exif_capture_time,
    )


def _resize_and_encode(img: Image.Image, size: int, img_format: str = "JPEG") -> bytes:
    """Resize an image to fit within size x size and encode to bytes."""
    img.thumbnail((size, size), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format=img_format, quality=85)
    return buf.getvalue()


_DEFAULT_MAX_FILE_SIZE = 50 * 1024 * 1024  # 50 MB


class ImageProcessor:
    """Processes images: thumbnail, preview, EXIF extraction.

    Args:
        thumbnail_size: Thumbnail dimension (square, pixels).
        preview_size: Preview dimension (square, pixels).
        max_image_dimension: Max dimension before downscaling.
        max_file_size: Max image file size in bytes (default 50 MB).
    """

    def __init__(
        self,
        thumbnail_size: int = 64,
        preview_size: int = 512,
        max_image_dimension: int = 4096,
        max_file_size: int = _DEFAULT_MAX_FILE_SIZE,
    ) -> None:
        self.thumbnail_size = thumbnail_size
        self.preview_size = preview_size
        self.max_image_dimension = max_image_dimension
        self.max_file_size = max_file_size

    def process(self, image_path: str | Path) -> ProcessedImage:
        """Process an image file: extract metadata, generate thumbnail and preview.

        Args:
            image_path: Path to the image file.

        Returns:
            ProcessedImage with original bytes, thumbnail, preview, and metadata.

        Raises:
            FileNotFoundError: If image file does not exist.
            IngestError: If image cannot be decoded (IMAGE_DECODE_FAILED).
        """
        path = Path(image_path)
        if not path.exists():
            raise FileNotFoundError(f"Image not found: {path}")

        file_size = path.stat().st_size
        if file_size > self.max_file_size:
            raise IngestError(
                error_code=ErrorCode.IMAGE_DECODE_FAILED,
                message=f"Image file too large: {file_size} bytes (max {self.max_file_size})",
            )

        original_bytes = path.read_bytes()

        try:
            img: Image.Image = Image.open(io.BytesIO(original_bytes))
            img.load()
        except (PIL.UnidentifiedImageError, OSError) as exc:
            raise IngestError(
                error_code=ErrorCode.IMAGE_DECODE_FAILED,
                message=f"Failed to decode image '{path}': {exc}",
            ) from exc

        # Ensure RGB mode for JPEG encoding
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")

        # Extract EXIF before any resize
        metadata = _extract_exif(img)

        # Downscale large images
        max_dim = max(img.size)
        if max_dim > self.max_image_dimension:
            ratio = self.max_image_dimension / max_dim
            new_size = (int(img.width * ratio), int(img.height * ratio))
            img = img.resize(new_size, Image.Resampling.LANCZOS)
            # Update metadata to reflect resized dimensions
            metadata = ImageMetadata(
                width=img.width,
                height=img.height,
                exif_make=metadata.exif_make,
                exif_model=metadata.exif_model,
                exif_gps_lat=metadata.exif_gps_lat,
                exif_gps_lon=metadata.exif_gps_lon,
                exif_capture_time=metadata.exif_capture_time,
            )

        # Generate thumbnail and preview
        thumb = _resize_and_encode(img.copy(), self.thumbnail_size)
        preview = _resize_and_encode(img.copy(), self.preview_size)

        return ProcessedImage(
            original_bytes=original_bytes,
            thumbnail_bytes=thumb,
            preview_bytes=preview,
            metadata=metadata,
        )

    def process_batch(
        self,
        image_bytes_list: list[bytes],
    ) -> list[tuple[bytes, bytes]]:
        """Batch process images using Daft-native pipeline.

        Decodes, resizes, and encodes all images in one Daft DataFrame
        pipeline, leveraging Rust-native image processing for 3-5x speedup
        over per-image PIL processing.

        EXIF extraction is NOT done here (use PIL ``_extract_exif`` for that).

        Args:
            image_bytes_list: List of raw image bytes.

        Returns:
            List of (thumbnail_bytes, preview_bytes) tuples.
        """
        import daft

        df = daft.from_pydict({"raw": image_bytes_list})
        df = df.with_column("image", daft.functions.decode_image(df["raw"]))
        df = df.with_column(
            "thumb_img",
            daft.functions.resize(df["image"], self.thumbnail_size, self.thumbnail_size),
        )
        df = df.with_column(
            "preview_img",
            daft.functions.resize(df["image"], self.preview_size, self.preview_size),
        )
        df = df.with_column(
            "thumbnail",
            daft.functions.encode_image(df["thumb_img"], image_format="JPEG"),
        )
        df = df.with_column(
            "preview",
            daft.functions.encode_image(df["preview_img"], image_format="JPEG"),
        )
        result = df.select("thumbnail", "preview").to_pydict()
        return list(zip(result["thumbnail"], result["preview"], strict=False))


# ---------------------------------------------------------------------------
# Video processing (Story 3.4)
# ---------------------------------------------------------------------------


class VideoProcessor:
    """Extracts keyframes from video files with scene detection.

    Uses PyAV for frame-level access and histogram-based scene detection.
    Falls back to first-frame extraction on failure.
    """

    def __init__(
        self,
        scene_threshold: float = 0.3,
        max_keyframes: int = 20,
        keyframe_size: int = 720,
    ) -> None:
        self.scene_threshold = scene_threshold
        self.max_keyframes = max_keyframes
        self.keyframe_size = keyframe_size

    def extract_keyframes(self, video_path: str | Path) -> VideoIngestResult:
        """Extract keyframes from a video file.

        Args:
            video_path: Path to the video file.

        Returns:
            VideoIngestResult with keyframes, duration, and detection method.

        Raises:
            FileNotFoundError: If video file does not exist.
            IngestError: If video cannot be decoded (VIDEO_DECODE_FAILED).
        """
        path = Path(video_path)
        if not path.exists():
            raise FileNotFoundError(f"Video not found: {path}")

        import av

        try:
            container = av.open(str(path))
        except (OSError, RuntimeError, ValueError) as exc:
            raise IngestError(
                error_code=ErrorCode.VIDEO_DECODE_FAILED,
                message=f"Failed to open video '{path}': {exc}",
            ) from exc

        video_stream = next(
            (s for s in container.streams if s.type == "video"),
            None,
        )
        if video_stream is None:
            container.close()
            raise IngestError(
                error_code=ErrorCode.VIDEO_DECODE_FAILED,
                message=f"No video stream found in '{path}'",
            )

        duration_ms = int(video_stream.duration * 1000) if video_stream.duration else 0

        try:
            keyframes = self._detect_scenes(container, video_stream)
            scene_used = True
        except (RuntimeError, OSError):
            logger.warning(
                "Scene detection failed for '%s', falling back to first frame",
                path,
                exc_info=True,
            )
            container.seek(0)
            video_stream = next(
                (s for s in container.streams if s.type == "video"),
                None,
            )
            keyframes = self._extract_first_frame(container, video_stream)
            scene_used = False

        container.close()

        return VideoIngestResult(
            keyframes=tuple(keyframes),
            duration_ms=duration_ms,
            scene_detection_used=scene_used,
        )

    def _detect_scenes(
        self,
        container: Any,
        stream: Any,
    ) -> list[ExtractedKeyframe]:
        """Extract keyframes using histogram-based scene detection."""

        keyframes: list[ExtractedKeyframe] = []
        prev_hist: np.ndarray[Any, Any] | None = None

        for frame in container.decode(stream):
            timestamp_ms = int(frame.pts * 1000 / frame.time_base)

            # Convert frame to numpy array and compute histogram
            arr = frame.to_ndarray(format="rgb24")
            hist = np.histogram(arr, bins=64, range=(0, 256))[0].astype(np.float32)
            hist /= hist.sum() + 1e-10  # Normalize

            is_scene = False
            if prev_hist is not None:
                # Histogram difference as scene boundary metric
                diff = np.abs(hist - prev_hist).sum()
                is_scene = diff > self.scene_threshold

            if is_scene or len(keyframes) == 0:
                jpeg_bytes = self._frame_to_jpeg(arr)
                keyframes.append(
                    ExtractedKeyframe(
                        jpeg_bytes=jpeg_bytes,
                        timestamp_ms=timestamp_ms,
                    )
                )

            prev_hist = hist

            if len(keyframes) >= self.max_keyframes:
                break

        return keyframes

    def _extract_first_frame(
        self,
        container: Any,
        stream: Any,
    ) -> list[ExtractedKeyframe]:
        """Extract only the first frame as fallback."""

        for frame in container.decode(stream):
            timestamp_ms = int(frame.pts * 1000 / frame.time_base)
            arr = frame.to_ndarray(format="rgb24")
            jpeg_bytes = self._frame_to_jpeg(arr)
            return [
                ExtractedKeyframe(
                    jpeg_bytes=jpeg_bytes,
                    timestamp_ms=timestamp_ms,
                )
            ]

        return []

    @staticmethod
    def _frame_to_jpeg(arr: np.ndarray[Any, Any]) -> bytes:
        """Convert a numpy RGB array to JPEG bytes."""
        img = Image.fromarray(arr)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85)
        return buf.getvalue()

    def extract_keyframes_daft(self, video_path: str | Path) -> VideoIngestResult:
        """Extract keyframes using Daft-native video frame reading.

        Falls back to ``extract_keyframes`` if Daft video support is unavailable.

        Args:
            video_path: Path to the video file.

        Returns:
            VideoIngestResult with keyframes and metadata.
        """
        import daft

        path = Path(video_path)
        if not path.exists():
            raise FileNotFoundError(f"Video not found: {path}")

        try:
            # Daft-native frame reading with resize
            df = daft.read_video_frames(
                str(path),
                image_height=self.keyframe_size,
            )

            # Extract timestamps and JPEG-encode frames
            df = df.with_column(
                "jpeg",
                daft.functions.encode_image(df["image"], image_format="JPEG"),
            )

            result = df.select("jpeg", "timestamp_ms").to_pydict()
            jpegs = result["jpeg"]
            timestamps = result.get("timestamp_ms", [0] * len(jpegs))

            keyframes = [
                ExtractedKeyframe(jpeg_bytes=j, timestamp_ms=int(t))
                for j, t in zip(jpegs[:self.max_keyframes], timestamps[:self.max_keyframes], strict=False)
            ]

            # Get duration via Daft metadata if available
            duration_ms = 0
            if timestamps:
                duration_ms = int(max(timestamps))

            return VideoIngestResult(
                keyframes=tuple(keyframes),
                duration_ms=duration_ms,
                scene_detection_used=False,
            )
        except Exception as exc:
            logger.warning(
                "Daft video extraction failed for '%s', falling back to PyAV: %s",
                path,
                exc,
            )
            return self.extract_keyframes(video_path)


def should_retain_original(days_old: int, retention_days: int = 90) -> bool:
    """Check if original image should be retained based on age.

    Args:
        days_old: Age of the image in days.
        retention_days: Retention policy in days.

    Returns:
        True if the original should be kept.
    """
    return days_old <= retention_days
