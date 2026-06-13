"""Tests for inline (CID) and remote email image handling.

Hermetic: no network. The renderer (`images.render_image`) and the network
fetch (`images.fetch_image_bytes`) are both mocked so PIL/requests are never
exercised, and we only assert the detection/collection/dispatch logic plus the
`load_remote_images` config round-trip.
"""
import base64
import json
import os
import tempfile
import unittest
from unittest import mock

from gogmail.tui import widgets
from gogmail.tui.widgets import (
    TUIHTMLParser,
    extract_inline_images,
    collect_remote_image_urls,
)


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii")


# A message payload with one embedded image part and one text part.
_MSG_WITH_INLINE = {
    "message": {
        "payload": {
            "mimeType": "multipart/related",
            "parts": [
                {
                    "mimeType": "text/html",
                    "body": {"data": _b64(b"<p>hello</p>")},
                },
                {
                    "mimeType": "image/png",
                    "filename": "logo.png",
                    "body": {"data": _b64(b"\x89PNG\r\n\x1a\nFAKEPNGDATA")},
                },
                {
                    "mimeType": "multipart/alternative",
                    "parts": [
                        {
                            "mimeType": "image/jpeg",
                            "filename": "photo.jpg",
                            "body": {"data": _b64(b"\xff\xd8\xff\xe0FAKEJPEG")},
                        },
                    ],
                },
            ],
        }
    }
}

_MSG_NO_IMAGES = {
    "message": {
        "payload": {
            "mimeType": "text/plain",
            "body": {"data": _b64(b"just text")},
        }
    }
}

_HTML_WITH_REMOTE = (
    '<html><body>'
    '<img src="https://example.com/a.png" alt="A">'
    '<img src="http://insecure.example.com/b.png" alt="B">'
    '<img src="cid:embedded123" alt="C">'
    '<img src="https://example.com/a.png" alt="dup">'
    '<img src="https://tracker.example.com/pixel.gif">'
    '</body></html>'
)


class TestInlineImageExtraction(unittest.TestCase):
    def test_detects_inline_image_parts(self):
        found = extract_inline_images(_MSG_WITH_INLINE)
        names = [f["name"] for f in found]
        self.assertEqual(names, ["logo.png", "photo.jpg"])
        # Each carries decoded bytes (not the base64 string).
        self.assertEqual(found[0]["data"], b"\x89PNG\r\n\x1a\nFAKEPNGDATA")
        self.assertTrue(all(isinstance(f["data"], bytes) for f in found))

    def test_no_images_returns_empty(self):
        self.assertEqual(extract_inline_images(_MSG_NO_IMAGES), [])
        self.assertEqual(extract_inline_images({}), [])
        self.assertEqual(extract_inline_images(None), [])


class TestRemoteUrlCollection(unittest.TestCase):
    def test_parser_records_img_srcs(self):
        parser = TUIHTMLParser()
        parser.feed('<img src="https://x.test/1.png"><img src="cid:y">')
        self.assertIn("https://x.test/1.png", parser.image_srcs)
        self.assertIn("cid:y", parser.image_srcs)
        # Placeholder text path is preserved alongside the recorded src.
        self.assertIn("🖼️", parser.get_text())

    def test_collect_only_https_distinct(self):
        urls = collect_remote_image_urls(_HTML_WITH_REMOTE)
        # http:// and cid: are excluded; https deduped; order preserved.
        self.assertEqual(
            urls,
            ["https://example.com/a.png", "https://tracker.example.com/pixel.gif"],
        )

    def test_empty_html(self):
        self.assertEqual(collect_remote_image_urls(""), [])
        self.assertEqual(collect_remote_image_urls(None), [])


class _FakeBodyView:
    """Minimal stand-in for the RichLog body pane."""
    def __init__(self):
        self.writes = []
        self.size = type("S", (), {"width": 50})()

    def write(self, content):
        self.writes.append(content)

    def clear(self):
        self.writes.clear()


class _FakeApp:
    def __init__(self, config):
        self.config = config


class TestGmailTabImageRendering(unittest.TestCase):
    """Drive the GmailTab image helpers without a running Textual app."""

    def _make_tab(self, config=None):
        tab = widgets.GmailTab.__new__(widgets.GmailTab)
        tab._fake_view = _FakeBodyView()
        tab._app = _FakeApp(config or {})
        # Patch the two pieces of Textual plumbing the helpers touch.
        tab.query_one = lambda *a, **k: tab._fake_view
        tab.post_message = lambda *a, **k: None
        return tab

    def test_render_inline_images_calls_render_image(self):
        tab = self._make_tab()
        with mock.patch.object(widgets.images, "render_image", return_value="RENDERED") as m:
            tab._render_inline_images(_MSG_WITH_INLINE)
        # render_image called once per inline image part, with the raw bytes.
        self.assertEqual(m.call_count, 2)
        passed = [c.args[0] for c in m.call_args_list]
        self.assertEqual(passed[0], b"\x89PNG\r\n\x1a\nFAKEPNGDATA")
        self.assertIn("RENDERED", tab._fake_view.writes)

    def test_render_inline_none_falls_back_to_text(self):
        tab = self._make_tab()
        with mock.patch.object(widgets.images, "render_image", return_value=None):
            tab._render_inline_images(_MSG_WITH_INLINE)
        joined = " ".join(str(w) for w in tab._fake_view.writes)
        self.assertIn("[image:", joined)

    def test_load_remote_images_fetches_and_renders(self):
        import asyncio
        tab = self._make_tab()
        tab.selected_msg = {
            "message": {
                "payload": {
                    "mimeType": "text/html",
                    "body": {"data": _b64(_HTML_WITH_REMOTE.encode("utf-8"))},
                }
            }
        }
        with mock.patch.object(widgets.images, "fetch_image_bytes", return_value=b"IMGBYTES") as fetch, \
             mock.patch.object(widgets.images, "render_image", return_value="PIX") as render:
            asyncio.run(tab._load_remote_images())
        # Both distinct https URLs fetched.
        fetched_urls = [c.args[0] for c in fetch.call_args_list]
        self.assertIn("https://example.com/a.png", fetched_urls)
        self.assertIn("https://tracker.example.com/pixel.gif", fetched_urls)
        self.assertEqual(render.call_count, 2)

    def test_remote_images_enabled_reads_config(self):
        self.assertTrue(self._make_tab({"load_remote_images": True})._remote_images_enabled())
        self.assertFalse(self._make_tab({"load_remote_images": False})._remote_images_enabled())
        self.assertFalse(self._make_tab({})._remote_images_enabled())

    # GmailTab.app resolves through Textual; stub it via a property override.
    def setUp(self):
        self._orig_app = widgets.GmailTab.app
        widgets.GmailTab.app = property(lambda self: self._app)

    def tearDown(self):
        widgets.GmailTab.app = self._orig_app


class TestConfigRoundTrip(unittest.TestCase):
    def test_load_remote_images_default_and_persist(self):
        from gogmail import app
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "settings.json")
            with mock.patch.object(app, "get_config_path", return_value=path):
                cfg = app.load_config()
                self.assertIn("load_remote_images", cfg)
                self.assertFalse(cfg["load_remote_images"])
                cfg["load_remote_images"] = True
                app.save_config(cfg)
                reloaded = app.load_config()
                self.assertTrue(reloaded["load_remote_images"])


if __name__ == "__main__":
    unittest.main()
