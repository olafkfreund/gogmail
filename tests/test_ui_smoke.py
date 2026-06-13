"""Headless UI smoke tests: mount the real app with mocked gog data and verify
core flows don't crash and render content."""
import asyncio
import os
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

    async def test_slow_email_fetch_still_renders_body(self):
        # Regression: a non-instant fetch makes any RichLog.loading overlay
        # actually engage, and toggling it off wipes the log — which silently
        # blanked slow-loading emails/docs/zoom output. A delayed mock plus
        # real pilot pauses reproduces that timing; the body must still render.
        from gogmail.app import GogMailApp
        from textual.widgets import RichLog

        async def slow_get(*a, **k):
            await asyncio.sleep(0.2)
            return {"headers": {"from": "a@x.example", "subject": "Hi", "date": "2026-06-10"},
                    "body": "Hello there, this is the body.", "message": {"payload": {}}}

        app = GogMailApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            gt = app.query_one(GmailTab)
            await gt.set_query("label:INBOX")
            await pilot.pause()
            with mock.patch.object(GogAPI, "gmail_get_message", slow_get):
                await gt.on_data_table_row_selected(FakeRowSelected("t1"))
                await pilot.pause(0.5)
            body = gt.query_one("#email-body-view", RichLog)
            self.assertGreater(len(body.lines), 1, "email body rendered blank after a slow fetch")

    async def test_zoom_create_meeting_button_renders_join_url(self):
        from gogmail.app import GogMailApp
        from gogmail.tui.widgets import ZoomTab
        from gogmail.zoom_api import ZoomAPI
        from textual.widgets import RichLog

        app = GogMailApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app.query_one("#content-switcher").current = "zoom-view"
            await pilot.pause()
            tab = app.query_one(ZoomTab)
            created = (True, {"join_url": "https://zoom.us/j/42", "start_url": "https://zoom.us/s/42"})
            with mock.patch.object(ZoomAPI, "create_meeting", _async(created)), \
                    mock.patch("gogmail.tui.widgets.webbrowser.open") as opened:
                await tab.on_button_pressed(
                    type("E", (), {"button": type("B", (), {"id": "zoom-create-btn"})()})())
                await pilot.pause(0.3)
                log = tab.query_one("#zoom-output", RichLog)
                text = "".join(str(s) for s in log.lines)
                self.assertIn("zoom.us/j/42", text)
                opened.assert_called_once_with("https://zoom.us/s/42")

    async def test_settings_screen_saves_and_toggles_mic_button(self):
        from gogmail.app import GogMailApp, AIAssistantPanel
        from gogmail.tui.screens import SettingsScreen
        from textual.widgets import Checkbox
        base = {"theme": "gruvbox", "ai_width": 40, "account": "",
                "voice_input": False, "spoken_replies": False}
        with mock.patch("gogmail.app.load_config", return_value=dict(base)), \
                mock.patch("gogmail.app.save_config"):
            app = GogMailApp()
            async with app.run_test(size=(140, 45)) as pilot:
                await pilot.pause()
                mic = app.query_one(AIAssistantPanel).query_one("#ai-mic-btn")
                self.assertFalse(bool(mic.display))  # hidden until enabled
                app.open_settings_dialog()
                await pilot.pause()
                self.assertIsInstance(app.screen, SettingsScreen)
                app.screen.query_one("#set-voice-input", Checkbox).value = True
                app.screen.query_one("#settings-save-btn").press()
                await pilot.pause()
                self.assertTrue(app.config["voice_input"])
                self.assertTrue(bool(mic.display))  # appears after enabling

    async def test_voice_button_transcribes_and_submits(self):
        import tempfile
        from gogmail.app import GogMailApp, AIAssistantPanel
        from gogmail.gemini_api import GeminiAPI

        class FakeRecorder:
            def __init__(self): self._rec = False
            @property
            def recording(self): return self._rec
            def start(self): self._rec = True; return True
            def stop(self):
                self._rec = False
                fd, p = tempfile.mkstemp(suffix=".wav")
                os.write(fd, b"RIFF" + b"\x00" * 64)
                os.close(fd)
                return p

        base = {"theme": "gruvbox", "ai_width": 40, "account": "",
                "voice_input": True, "spoken_replies": False}
        with mock.patch("gogmail.app.load_config", return_value=dict(base)), \
                mock.patch("gogmail.app.save_config"):
            app = GogMailApp()
            async with app.run_test(size=(140, 45)) as pilot:
                await pilot.pause()
                panel = app.query_one(AIAssistantPanel)
                panel._recorder = FakeRecorder()
                captured = []
                async def fake_submit(p): captured.append(p)
                panel.submit_prompt = fake_submit
                mic = panel.query_one("#ai-mic-btn")
                with mock.patch.object(GeminiAPI, "transcribe_audio",
                                       _async("show me my latest emails")):
                    mic.press()            # start recording
                    await pilot.pause()
                    self.assertEqual(str(mic.label), "Stop")
                    mic.press()            # stop -> transcribe -> submit
                    await pilot.pause(0.3)
                self.assertEqual(captured, ["show me my latest emails"])
                self.assertEqual(str(mic.label), "Talk")

    async def test_label_picker_moves_and_returns_to_inbox(self):
        from gogmail.app import GogMailApp, GmailTab
        app = GogMailApp()
        async with app.run_test(size=(140, 45)) as pilot:
            await pilot.pause()
            gt = app.query_one(GmailTab)
            await gt.set_query("label:INBOX")
            await pilot.pause()
            gt.query_one("#gmail-switcher").current = "gmail-detail-view"
            calls = {}
            async def fake_modify(tid, add="", remove=""):
                calls.update(add=add, remove=remove); return True
            with mock.patch.object(GogAPI, "gmail_modify_labels", side_effect=fake_modify):
                await app._apply_label("t1", {"label": "Work", "move": True, "create": False})
            self.assertEqual(calls, {"add": "Work", "remove": "INBOX"})
            # A moved conversation leaves the inbox -> back to the list view.
            self.assertEqual(gt.query_one("#gmail-switcher").current, "gmail-list-view")

    async def test_label_picker_creates_new_label_then_applies(self):
        from gogmail.app import GogMailApp
        app = GogMailApp()
        async with app.run_test(size=(140, 45)) as pilot:
            await pilot.pause()
            created = []
            async def fake_create(name):
                created.append(name); return True, ""
            with mock.patch.object(GogAPI, "gmail_labels_create", side_effect=fake_create), \
                    mock.patch.object(GogAPI, "gmail_modify_labels", _async(True)):
                await app._apply_label("t1", {"label": "Receipts", "move": False, "create": True})
            self.assertEqual(created, ["Receipts"])

    async def test_read_tool_navigates_to_its_client_view(self):
        # A read tool should open the matching client view (so results show in
        # the tab, not just the chat) in addition to returning data.
        from gogmail.app import GogMailApp, _tool_list_tasks
        app = GogMailApp()
        async with app.run_test(size=(140, 45)) as pilot:
            await pilot.pause()
            with mock.patch.object(GogAPI, "tasks_list",
                                   _async([{"id": "k1", "title": "Ship it", "status": "needsAction"}])), \
                    mock.patch.object(GogAPI, "tasks_lists", _async([{"id": "L1", "title": "My Tasks"}])):
                result = await _tool_list_tasks(app, {})
                await pilot.pause()
            self.assertEqual(app.query_one("#content-switcher").current, "tasks-view")
            self.assertIn("Ship it", result)

    async def test_command_palette_opens_with_gogmail_provider(self):
        from textual.command import CommandPalette
        from gogmail.app import GogMailApp, GogMailCommands
        app = GogMailApp()
        self.assertIn(GogMailCommands, app.COMMANDS)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await pilot.press("ctrl+p")
            await pilot.pause()
            self.assertIsInstance(app.screen, CommandPalette)
            for ch in "compose":
                await pilot.press(ch)
            await pilot.pause(0.5)  # provider search runs async; must not raise
            await pilot.press("escape")

    async def test_confirm_dialog_gates_destructive_action(self):
        from gogmail.app import GogMailApp
        from gogmail.tui.screens import ConfirmDialog
        app = GogMailApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            ran = []

            async def do_it():
                ran.append(True)

            app.confirm("Delete the thing?", do_it)
            await pilot.pause()
            self.assertIsInstance(app.screen, ConfirmDialog)
            # Cancel is focused by default: Enter must NOT run the action.
            await pilot.press("enter")
            await pilot.pause()
            self.assertEqual(ran, [])
            # Explicitly confirming does run it.
            app.confirm("Delete the thing?", do_it)
            await pilot.pause()
            app.screen.query_one("#confirm-btn").press()
            await pilot.pause()
            self.assertEqual(ran, [True])


    async def test_paste_inserts_clipboard_into_focused_input(self):
        from gogmail.app import GogMailApp
        app = GogMailApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            with mock.patch.object(GogMailApp, "clipboard",
                                   new_callable=mock.PropertyMock,
                                   return_value="pasted@x.example"):
                box = app.query_one("#email-search-input")
                box.focus()
                await pilot.pause()
                app.action_paste_clipboard()
                await pilot.pause()
                self.assertIn("pasted@x.example", box.value)


if __name__ == "__main__":
    unittest.main()
