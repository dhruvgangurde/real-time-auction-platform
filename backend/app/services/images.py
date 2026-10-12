"""Image intake: size cap, type detection by decoding, re-encoding (FR-13, NFR-24, SI-5).

The type is what Pillow decodes, never the file name or Content-Type. Every accepted image is
decoded and written again, which drops EXIF (including GPS), ICC and text chunks and anything
appended to the file. Pillow's decompression-bomb guard is set to image_max_pixels and its
warning raised as an error, so an oversized image is refused from its header, before a full
decode.
"""

import io
import warnings
from collections.abc import AsyncIterator
from typing import Any

from PIL import Image, ImageOps, UnidentifiedImageError

from app.core.config import settings
from app.core.errors import AppError

# decoded format -> (file extension, save options); no exif/icc options, so none are written
FORMATS: dict[str, tuple[str, dict[str, Any]]] = {
    "JPEG": ("jpg", {"quality": 85, "optimize": True}),
    "PNG": ("png", {"optimize": True}),
    "WEBP": ("webp", {"quality": 85}),
}


def _unsupported(message: str) -> AppError:
    return AppError("UNSUPPORTED_IMAGE", message, 422)


async def read_capped(body: AsyncIterator[bytes], limit: int) -> bytes:
    """Read a request body, stopping as soon as it passes `limit` bytes."""
    data = bytearray()
    async for chunk in body:
        data += chunk
        if len(data) > limit:
            raise AppError("IMAGE_TOO_LARGE", f"images are limited to {limit} bytes", 413)
    if not data:
        raise _unsupported("empty upload")
    return bytes(data)


def reencode(data: bytes) -> tuple[bytes, str]:
    """Decode, check and re-encode one image. Returns (bytes, extension). CPU bound: run it in
    a worker thread."""
    Image.MAX_IMAGE_PIXELS = settings.image_max_pixels
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as probe:  # reads the header only
                if probe.format not in FORMATS:
                    raise _unsupported("only JPEG, PNG and WebP images are accepted")
                width, height = probe.size
                if width * height > settings.image_max_pixels:
                    raise _unsupported("image has too many pixels")
                fmt = probe.format
                probe.verify()  # structural check; leaves the object unusable
            with Image.open(io.BytesIO(data)) as reopened:  # so open it again to decode
                image = ImageOps.exif_transpose(reopened)  # apply orientation, then drop EXIF
                if fmt == "JPEG" and image.mode not in ("RGB", "L"):
                    image = image.convert("RGB")
                image.info = {}  # nothing from the upload is carried into the new file
                out = io.BytesIO()
                extension, options = FORMATS[fmt]
                image.save(out, format=fmt, **options)
    except AppError:
        raise
    except (
        UnidentifiedImageError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
        OSError,
        SyntaxError,
        ValueError,
    ) as exc:
        raise _unsupported("not a valid JPEG, PNG or WebP image") from exc
    return out.getvalue(), extension
