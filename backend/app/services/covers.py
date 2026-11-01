"""App cover images (NAP-025).

An uploaded file is never served as is: it is decoded with Pillow (rejecting anything that is not really a PNG, JPEG or
WebP image), checked for size, stripped of metadata (EXIF can hold GPS coordinates and camera serials), centre-cropped
to the store's 4:3 card format and re-encoded as WebP 1200x900. Only that normalized file is stored and shown.
"""
from __future__ import annotations

import os
import tempfile

from ..errors import ApiError

MIN_W, MIN_H = 800, 600            # smaller covers look blurry on the card
MAX_PIXELS = 40_000_000            # decompression-bomb guard (e.g. 8000x5000)
OUT_W, OUT_H = 1200, 900           # 4:3, the card and the app page use this ratio
REQUIREMENTS = {"formats": ["png", "jpg", "jpeg", "webp"], "max_bytes": 10 * 1024 * 1024,
                "min_width": MIN_W, "min_height": MIN_H, "ratio": "4:3", "output": f"webp {OUT_W}x{OUT_H}"}


def normalize(src_path: str) -> tuple[str, dict]:
    """Validate and re-encode an uploaded cover. Returns (path to the WebP file, info). Raises ApiError."""
    from PIL import Image, ImageOps, UnidentifiedImageError
    Image.MAX_IMAGE_PIXELS = MAX_PIXELS
    try:
        with Image.open(src_path) as im:
            fmt = (im.format or "").upper()
            if fmt not in ("PNG", "JPEG", "WEBP"):
                raise ApiError("cover_invalid", 422, details={"reason": "format"})
            im.load()  # decode fully: truncated / corrupt files fail here, not later in a browser
            if getattr(im, "is_animated", False):
                raise ApiError("cover_invalid", 422, details={"reason": "animated"})
            im = ImageOps.exif_transpose(im)  # honour the camera orientation before dropping EXIF
            w, h = im.size
            if w < MIN_W or h < MIN_H:
                raise ApiError("cover_too_small", 422, details={"width": w, "height": h},
                               min_width=MIN_W, min_height=MIN_H)
            if im.mode not in ("RGB", "RGBA"):
                im = im.convert("RGBA" if "A" in im.getbands() else "RGB")
            if im.mode == "RGBA":  # flatten transparency on the store background colour
                bg = Image.new("RGB", im.size, (28, 42, 53))
                bg.paste(im, mask=im.split()[-1])
                im = bg
            out = ImageOps.fit(im, (OUT_W, OUT_H), method=Image.Resampling.LANCZOS, centering=(0.5, 0.5))
            fd, path = tempfile.mkstemp(suffix=".webp")
            os.close(fd)
            out.save(path, "WEBP", quality=85, method=4)  # no exif/icc passed: metadata is dropped
            return path, {"source_format": fmt.lower(), "source_size": [w, h], "size": [OUT_W, OUT_H],
                          "cropped": abs(w / h - OUT_W / OUT_H) > 0.01}
    except ApiError:
        raise
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, ValueError) as e:
        raise ApiError("cover_invalid", 422, details={"reason": type(e).__name__}) from e
