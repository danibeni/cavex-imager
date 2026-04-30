"""FITS preview endpoint — converts a FITS file to a browser-renderable PNG.

The INDIGO v4l2 driver (and most CCD drivers) write images in FITS format,
which browsers cannot display natively.  This endpoint:
  1. Locates the requested file under the data directory.
  2. Opens it with astropy.io.fits.
  3. Extracts the primary HDU pixel data.
  4. Applies a simple min/max stretch (or asinh for wide dynamic range).
  5. Returns a PNG via StreamingResponse so the <img> tag can render it.
"""

from __future__ import annotations

import io
import os

import numpy as np
from astropy.io import fits
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from PIL import Image

router = APIRouter(prefix="/api/v1", tags=["preview"])

# Directories to search for image files (host path then container bind-mount)
_SEARCH_DIRS: list[str] = [
    "/opt/cavex/data",
    "/data",
]

_ALLOWED_EXTENSIONS = {".fits", ".fit", ".jpg", ".jpeg", ".png", ".tiff", ".tif"}


def _locate(filename: str) -> str | None:
    """Find *filename* in one of the known data directories."""
    # Security: reject any path traversal attempts
    if "/" in filename or "\\" in filename or ".." in filename:
        return None
    for directory in _SEARCH_DIRS:
        candidate = os.path.join(directory, filename)
        if os.path.isfile(candidate):
            return candidate
    return None


def _stretch(data: np.ndarray) -> np.ndarray:
    """Stretch a 2-D float array to uint8 [0, 255].

    Uses an asinh stretch which preserves faint detail while avoiding
    saturation on bright stars/hot pixels.
    """
    data = data.astype(np.float64)

    # Replace NaN / Inf with the median so they don't skew the stretch
    finite_mask = np.isfinite(data)
    if not np.any(finite_mask):
        return np.zeros(data.shape, dtype=np.uint8)
    median = float(np.median(data[finite_mask]))
    data[~finite_mask] = median

    vmin = float(np.percentile(data, 0.5))
    vmax = float(np.percentile(data, 99.5))
    if vmax <= vmin:
        vmax = vmin + 1.0

    # asinh stretch
    scaled = (data - vmin) / (vmax - vmin)
    stretched = np.arcsinh(scaled * 10.0) / np.arcsinh(10.0)
    stretched = np.clip(stretched, 0.0, 1.0)
    return (stretched * 255).astype(np.uint8)


def _fits_to_png_bytes(file_path: str) -> bytes:
    """Open a FITS file and return a PNG-encoded preview as bytes."""
    with fits.open(file_path) as hdul:
        # Find the first HDU with 2-D image data
        data: np.ndarray | None = None
        for hdu in hdul:
            if hdu.data is not None and isinstance(hdu.data, np.ndarray):
                if hdu.data.ndim == 2:
                    data = hdu.data
                    break
                elif hdu.data.ndim == 3:
                    # RGB or multi-channel — take first channel
                    data = hdu.data[0]
                    break

    if data is None:
        raise ValueError("No image data found in FITS file.")

    stretched = _stretch(data)
    img = Image.fromarray(stretched, mode="L")  # Grayscale

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    buf.seek(0)
    return buf.read()


@router.get("/preview/{filename}")
async def fits_preview(filename: str) -> StreamingResponse:
    """Return a PNG preview of a FITS (or regular image) file.

    Args:
        filename: Bare filename (no path separators) of the image file.

    Returns:
        StreamingResponse with content-type image/png.

    Raises:
        404: File not found.
        422: File cannot be converted (corrupt FITS, unsupported format).
    """
    ext = os.path.splitext(filename)[1].lower()
    if ext not in _ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"Unsupported extension: {ext}")

    file_path = _locate(filename)
    if file_path is None:
        raise HTTPException(status_code=404, detail=f"File not found: {filename}")

    try:
        if ext in {".fits", ".fit"}:
            png_bytes = _fits_to_png_bytes(file_path)
        else:
            # Regular image — just re-encode as PNG for a uniform response type
            with Image.open(file_path) as img:
                buf = io.BytesIO()
                img.convert("RGB").save(buf, format="PNG")
                buf.seek(0)
                png_bytes = buf.read()

    except Exception as exc:
        raise HTTPException(
            status_code=422,
            detail=f"Could not convert image: {exc}",
        ) from exc

    return StreamingResponse(
        io.BytesIO(png_bytes),
        media_type="image/png",
        headers={
            "Cache-Control": "no-store",
            "Content-Length": str(len(png_bytes)),
        },
    )