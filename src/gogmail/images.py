"""Render images to terminal cells so they can be shown inside the TUI.

Uses rich-pixels (Unicode half-blocks): a Rich renderable that any RichLog can
write, working in every terminal with no graphics protocol required. Images are
downscaled to fit a target cell box first (a half-block cell is 1px wide × 2px
tall), keeping output small enough not to flood the pane.

Used by the Gmail (inline/attachment images), Drive (image-file preview) and
Photos (thumbnail) tabs. pillow + rich-pixels are optional at import time — if
unavailable, render_image returns None and callers fall back to a text note.
"""
import io
import logging

MAX_FETCH_BYTES = 5_000_000  # cap remote/inline image size fed to the renderer


def render_image(source, max_cols: int = 60, max_rows: int = 24):
    """Render `source` (a file path str, or raw image bytes) to a Rich
    renderable scaled to fit `max_cols` × `max_rows` half-block cells.
    Returns None on any failure (missing deps, bad data)."""
    try:
        from PIL import Image
        from rich_pixels import Pixels
    except Exception as e:  # deps not installed
        logging.error(f"images: rich-pixels/pillow unavailable: {e}")
        return None
    try:
        if isinstance(source, (bytes, bytearray)):
            img = Image.open(io.BytesIO(bytes(source)))
        else:
            img = Image.open(source)
        img = img.convert("RGBA")
        max_w, max_h = max(1, max_cols), max(1, max_rows * 2)
        w, h = img.size
        scale = min(max_w / w, max_h / h, 1.0)
        if scale < 1.0:
            img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))))
        return Pixels.from_image(img)
    except Exception as e:
        logging.error(f"images: render failed: {e}")
        return None


def fetch_image_bytes(url: str, timeout: int = 15, max_bytes: int = MAX_FETCH_BYTES):
    """Download an image over HTTPS for rendering. Returns bytes or None.

    Caller is responsible for the privacy decision (remote email images are a
    tracking vector) — this only enforces https + a size cap."""
    if not url or not url.lower().startswith("https://"):
        return None
    try:
        import requests
        resp = requests.get(url, timeout=timeout, stream=True)
        if resp.status_code != 200:
            return None
        data = resp.raw.read(max_bytes + 1, decode_content=True)
        if not data or len(data) > max_bytes:
            return None
        return data
    except Exception as e:
        logging.error(f"images: fetch failed: {e}")
        return None
