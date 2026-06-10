"""Headless UI smoke tests: mount the real app with mocked gog data and verify
core flows don't crash and render content."""
import unittest
from unittest import mock

from gogmail.gog_api import GogAPI
from gogmail.tui.widgets import (
    GmailTab, CalendarTab, DriveTab, DocsTab, TasksTab, ContactsTab,
)
from gogmail.tui.screens import GmailComposeScreen


def _async(value):
    async def f(*a, **k):
        return value
    return f


class FakeKey:
    def __init__(self, v): self.value = v


class FakeRowSelected:
    def __init__(self, v): self.row_key = FakeKey(v)


class TestUiSmoke(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # Patch every gog method the exercised tabs touch with synthetic data.
        self.patchers = [
            mock.patch.object(GogAPI, "preflight", _async((True, "demo@x.example"))),
            mock.patch.object(GogAPI, "list_accounts", _async(["demo@x.example"])),
            mock.patch.object(GogAPI, "gmail_search", _async(
                [{"id": "t1", "date": "2026-06-10", "from": "a@x.example", "subject": "Hi", "labels": ["INBOX"]}])),
            mock.patch.object(GogAPI, "gmail_get_message", _async(
                {"headers": {"from": "a@x.example", "subject": "Hi", "date": "2026-06-10"},
                 "body": "Hello there", "message": {"payload": {}}})),
            mock.patch.object(GogAPI, "gmail_mark_read", _async(True)),
            mock.patch.object(GogAPI, "calendar_events", _async([])),
            mock.patch.object(GogAPI, "tasks_lists", _async([{"id": "L1", "title": "My Tasks"}])),
            mock.patch.object(GogAPI, "tasks_list", _async([{"id": "k1", "title": "Do it", "status": "needsAction"}])),
            mock.patch.object(GogAPI, "drive_list", _async([{"id": "d1", "name": "Doc", "mimeType": "x", "size": "1", "owners": [{}]}])),
            mock.patch.object(GogAPI, "drive_search", _async([{"id": "d1", "name": "Doc", "mimeType": "x"}])),
            mock.patch.object(GogAPI, "contacts_list", _async([{"resource": "c1", "name": "A", "email": "a@x.example", "phone": ""}])),
            mock.patch.object(GogAPI, "chat_spaces", _async([])),
        ]
        for p in self.patchers:
            p.start()

    async def asyncTearDown(self):
        for p in self.patchers:
            p.stop()

    async def test_app_mounts_and_tabs_present(self):
        from gogmail.app import GogMailApp
        app = GogMailApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            for cls in (GmailTab, CalendarTab, DriveTab, DocsTab, TasksTab, ContactsTab):
                self.assertEqual(len(app.query(cls)), 1, f"{cls.__name__} missing")

    async def test_inbox_loads_and_email_opens(self):
        from gogmail.app import GogMailApp
        app = GogMailApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            gt = app.query_one(GmailTab)
            await gt.set_query("label:INBOX")
            await pilot.pause()
            self.assertTrue(getattr(gt, "threads_data", None))
            # Opening a row must not raise and should record the message.
            await gt.on_data_table_row_selected(FakeRowSelected("t1"))
            await pilot.pause()
            self.assertEqual(gt.selected_msg.get("body"), "Hello there")

    async def test_compose_dialog_opens(self):
        from gogmail.app import GogMailApp
        app = GogMailApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app.open_compose_dialog(to="x@y.example")
            await pilot.pause()
            self.assertIsInstance(app.screen, GmailComposeScreen)


if __name__ == "__main__":
    unittest.main()
