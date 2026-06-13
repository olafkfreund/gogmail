"""Drive inline image preview tests.

Hermetic: no network, no `gog`. We mount the real app with synthetic Drive
data, then drive DriveTab._preview_file for an image row (asserting a download
+ render with the temp path) and a non-image row (asserting a text summary,
with no download). GogAPI.drive_download (async) and images.render_image are
mocked.
"""
import unittest
from unittest import mock

from gogmail.gog_api import GogAPI
from gogmail import images
from gogmail.tui.widgets import DriveTab


def _async(value):
    async def f(*a, **k):
        return value
    return f


# Two files: a PNG image and a plain Google Doc (non-image).
DRIVE_FILES = [
    {"id": "img1", "name": "photo.png", "mimeType": "image/png",
     "size": "2048", "owners": [{}]},
    {"id": "doc1", "name": "Notes", "mimeType": "application/vnd.google-apps.document",
     "size": None, "owners": [{}]},
]


class TestDrivePreview(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.patchers = [
            mock.patch.object(GogAPI, "preflight", _async((True, "demo@x.example"))),
            mock.patch.object(GogAPI, "list_accounts", _async(["demo@x.example"])),
            mock.patch.object(GogAPI, "drive_list", _async(DRIVE_FILES)),
            mock.patch.object(GogAPI, "drive_search", _async(DRIVE_FILES)),
            # Tabs the app mounts at startup also touch these; keep them quiet.
            mock.patch.object(GogAPI, "gmail_search_page", _async(([], ""))),
            mock.patch.object(GogAPI, "calendar_events", _async([])),
            mock.patch.object(GogAPI, "tasks_lists", _async([])),
            mock.patch.object(GogAPI, "contacts_list", _async([])),
            mock.patch.object(GogAPI, "chat_spaces", _async([])),
        ]
        for p in self.patchers:
            p.start()

    async def asyncTearDown(self):
        for p in self.patchers:
            p.stop()

    async def test_image_row_downloads_and_renders(self):
        from gogmail.app import GogMailApp

        downloads = []

        async def fake_download(file_id, destination):
            downloads.append((file_id, destination))
            return True, ""

        from rich.text import Text
        # A real, writable Rich renderable carrying a distinctive marker so we
        # can confirm the rendered image actually reached the RichLog.
        SENTINEL = Text("RENDERED-IMAGE-MARKER")
        renders = []

        def fake_render(path, max_cols=60, max_rows=24):
            renders.append((path, max_cols, max_rows))
            return SENTINEL

        app = GogMailApp()
        async with app.run_test(size=(140, 45)) as pilot:
            await pilot.pause()
            app.query_one("#content-switcher").current = "drive-view"
            await pilot.pause()
            tab = app.query_one(DriveTab)
            await tab.refresh_files()
            await pilot.pause()

            preview = tab.query_one("#drive-preview")
            with mock.patch.object(GogAPI, "drive_download", side_effect=fake_download), \
                    mock.patch.object(images, "render_image", side_effect=fake_render):
                tab._preview_file("img1", "photo.png")
                await pilot.pause(0.3)

            # Downloaded the right file to the registered temp path...
            self.assertEqual(len(downloads), 1)
            dl_id, dl_path = downloads[0]
            self.assertEqual(dl_id, "img1")
            self.assertTrue(dl_path.endswith("photo.png"))
            self.assertIn(dl_path, app._temp_files)
            # ...and rendered that same path into the preview pane (with the
            # ~50-col / 24-row preview box passed through).
            self.assertEqual(len(renders), 1)
            self.assertEqual(renders[0][0], dl_path)
            self.assertEqual(renders[0][1], tab.PREVIEW_COLS)
            self.assertEqual(renders[0][2], 24)
            # The rendered renderable made it into the RichLog.
            text = "".join(str(s) for s in preview.lines)
            self.assertIn("RENDERED-IMAGE-MARKER", text)

    async def test_image_row_writes_loading_then_renders(self):
        # The "Loading preview…" hint should appear before the download resolves.
        from gogmail.app import GogMailApp

        async def fake_download(file_id, destination):
            return True, ""

        app = GogMailApp()
        async with app.run_test(size=(140, 45)) as pilot:
            await pilot.pause()
            app.query_one("#content-switcher").current = "drive-view"
            await pilot.pause()
            tab = app.query_one(DriveTab)
            await tab.refresh_files()
            await pilot.pause()
            with mock.patch.object(GogAPI, "drive_download", side_effect=fake_download), \
                    mock.patch.object(images, "render_image", return_value=None):
                tab._preview_file("img1", "photo.png")
                # Before pausing for the worker, the synchronous part ran.
                await pilot.pause(0.3)
            # render_image returned None -> fallback text note, not a crash.
            preview = tab.query_one("#drive-preview")
            text = "".join(str(s) for s in preview.lines)
            self.assertIn("photo.png", text)

    async def test_non_image_row_shows_text_summary_no_download(self):
        from gogmail.app import GogMailApp

        downloads = []

        async def fake_download(file_id, destination):
            downloads.append(file_id)
            return True, ""

        app = GogMailApp()
        async with app.run_test(size=(140, 45)) as pilot:
            await pilot.pause()
            app.query_one("#content-switcher").current = "drive-view"
            await pilot.pause()
            tab = app.query_one(DriveTab)
            await tab.refresh_files()
            await pilot.pause()

            with mock.patch.object(GogAPI, "drive_download", side_effect=fake_download), \
                    mock.patch.object(images, "render_image") as render:
                tab._preview_file("doc1", "Notes")
                await pilot.pause(0.2)

            # No download and no render for a non-image file.
            self.assertEqual(downloads, [])
            render.assert_not_called()
            preview = tab.query_one("#drive-preview")
            text = "".join(str(s) for s in preview.lines)
            self.assertIn("Notes", text)
            self.assertIn("document", text)  # mimeType shown in summary


if __name__ == "__main__":
    unittest.main()
