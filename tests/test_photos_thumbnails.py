"""Photos tab inline thumbnails (hermetic, no network).

Mounts the real app, points the Photos tab at synthetic media items, and
verifies that selecting a row builds a `=w...-h...` thumbnail URL from the
item's `baseUrl` and routes it through images.fetch_image_bytes + render_image.
"""
import unittest
from unittest import mock

from gogmail.gog_api import GogAPI


def _async(value):
    async def f(*a, **k):
        return value
    return f


PHOTOS = [
    {
        "id": "p1",
        "filename": "sunset.jpg",
        "mimeType": "image/jpeg",
        "baseUrl": "https://photos.example/AB123",
        "mediaMetadata": {"creationTime": "2026-01-02T03:04:05Z",
                          "width": "4000", "height": "3000"},
    },
    {
        "id": "p2",
        "filename": "cat.png",
        "mimeType": "image/png",
        "baseUrl": "https://photos.example/CD456",
        "mediaMetadata": {"creationTime": "2026-02-02T00:00:00Z"},
    },
]


class FakeKey:
    def __init__(self, v):
        self.value = v


class FakeRowSelected:
    def __init__(self, v):
        self.row_key = FakeKey(v)


class TestPhotosThumbnails(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.patchers = [
            mock.patch.object(GogAPI, "preflight", _async((True, "demo@x.example"))),
            mock.patch.object(GogAPI, "list_accounts", _async(["demo@x.example"])),
            mock.patch.object(GogAPI, "photos_list", _async(list(PHOTOS))),
        ]
        for p in self.patchers:
            p.start()

    async def asyncTearDown(self):
        for p in self.patchers:
            p.stop()

    async def test_selecting_photo_builds_thumbnail_url_and_renders(self):
        from gogmail.app import GogMailApp
        from gogmail.tui.widgets import PhotosTab

        from rich.text import Text
        sentinel = Text("RENDERED-THUMBNAIL")
        with mock.patch("gogmail.images.fetch_image_bytes",
                        return_value=b"\x89PNG-bytes") as fetch, \
                mock.patch("gogmail.images.render_image",
                           return_value=sentinel) as render:
            app = GogMailApp()
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause()
                app.query_one("#content-switcher").current = "photos-view"
                await pilot.pause()
                tab = app.query_one(PhotosTab)
                await tab.refresh_photos()
                await pilot.pause()
                self.assertEqual(tab.photos_data, PHOTOS)  # items kept w/ baseUrl

                log = tab.query_one("#photos-detail")
                table = tab.query_one("#photos-table")
                table.move_cursor(row=0)  # select sunset.jpg (p1)
                await tab.on_data_table_row_selected(FakeRowSelected("p1"))
                # Let the thumbnail worker run.
                await pilot.pause(0.3)

        # Fetched the size-suffixed thumbnail URL built from baseUrl.
        fetch.assert_called_once_with("https://photos.example/AB123=w480-h480")
        # Rendered the fetched bytes into the detail pane.
        self.assertEqual(render.call_count, 1)
        args, _ = render.call_args
        self.assertEqual(args[0], b"\x89PNG-bytes")
        # The rendered thumbnail was written to the detail pane.
        text = "".join(str(line) for line in log.lines)
        self.assertIn("RENDERED-THUMBNAIL", text)
        self.assertIn("sunset.jpg", text)  # caption

    async def test_failed_render_writes_text_note(self):
        from gogmail.app import GogMailApp
        from gogmail.tui.widgets import PhotosTab

        with mock.patch("gogmail.images.fetch_image_bytes", return_value=None) as fetch, \
                mock.patch("gogmail.images.render_image", return_value=None) as render:
            app = GogMailApp()
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause()
                app.query_one("#content-switcher").current = "photos-view"
                await pilot.pause()
                tab = app.query_one(PhotosTab)
                await tab.refresh_photos()
                await pilot.pause()
                log = tab.query_one("#photos-detail")
                table = tab.query_one("#photos-table")
                table.move_cursor(row=1)  # select cat.png (p2)
                await tab.on_data_table_row_selected(FakeRowSelected("p2"))
                await pilot.pause(0.3)

        fetch.assert_called_once_with("https://photos.example/CD456=w480-h480")
        # No bytes -> render not attempted; a text note mentions the filename.
        render.assert_not_called()
        text = "".join(str(line) for line in log.lines)
        self.assertIn("cat.png", text)


if __name__ == "__main__":
    unittest.main()
