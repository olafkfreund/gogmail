"""Inline image rendering for the TUI.

The TUI never renders pixels itself: like the rest of GogMail it shells out to
external tools (the same ones wired onto PATH by the Nix package). `render_image`
turns image bytes (or a path) into a Rich renderable made of coloured ANSI cells
that a `RichLog` can `write()`. `fetch_image_bytes` downloads a URL with
`requests` (used for Google Photos `baseUrl` thumbnails). Both are best-effort:
they return `None` on any failure so callers can fall back to a text note.
"""
import logging
import os
import shutil
import subprocess
import tempfile

import requests
from rich.text import Text

log = logging.getLogger("gogmail")

# Terminal-to-ANSI image renderers, in preference order. Each entry builds the
# argv that prints a cell-art rendering of `path` constrained to cols x rows.
# These mirror view_media_file's tool list (timg is wired onto PATH via Nix).
def _timg_args(path, cols, rows):
    return ["timg", "-g", f"{cols}x{rows}", "-pq", path]


def _chafa_args(path, cols, rows):
    return ["chafa", "--format=symbols", f"--size={cols}x{rows}", path]


def _viu_args(path, cols, rows):
    return ["viu", "-w", str(cols), "-h", str(rows), path]


_RENDERERS = (
    ("timg", _timg_args),
    ("chafa", _chafa_args),
    ("viu", _viu_args),
)


def fetch_image_bytes(url: str, timeout: int = 20):
    """Download `url` and return its bytes, or None on failure.

    Used for Google Photos thumbnails (a temporary `baseUrl` plus a size param).
    """
    if not url:
        return None
    try:
        resp = requests.get(url, timeout=timeout)
        resp.raise_for_status()
        return resp.content
    except Exception as exc:  # network/HTTP errors are non-fatal
        log.warning("fetch_image_bytes failed for %s: %s", url, exc)
        return None


def render_image(path_or_bytes, max_cols: int = 50, max_rows: int = 24):
    """Render an image to a Rich `Text` of ANSI cell-art, or None on failure.

    `path_or_bytes` may be a filesystem path (str) or raw image bytes. When bytes
    are given they're written to a temp file because the renderers read files.
    The output is constrained to `max_cols` x `max_rows` so it fits the detail
    pane. Returns None if no renderer is available or rendering fails, letting
    the caller fall back to a textual description.
    """
    cleanup = None
    try:
        if isinstance(path_or_bytes, (bytes, bytearray)):
            fd, path = tempfile.mkstemp(prefix="gogmail-img-")
            os.write(fd, bytes(path_or_bytes))
            os.close(fd)
            cleanup = path
        else:
            path = path_or_bytes
        if not path or not os.path.exists(path):
            return None

        for tool, build_args in _RENDERERS:
            if not shutil.which(tool):
                continue
            try:
                proc = subprocess.run(
                    build_args(path, max_cols, max_rows),
                    capture_output=True, timeout=30,
                )
            except Exception as exc:
                log.warning("image renderer %s failed: %s", tool, exc)
                continue
            out = proc.stdout.decode("utf-8", "replace")
            if proc.returncode == 0 and out.strip():
                # ANSI escapes -> styled Rich Text the RichLog can render.
                return Text.from_ansi(out.rstrip("\n"))
        return None
    finally:
        if cleanup:
            try:
                os.remove(cleanup)
            except OSError:
                pass
