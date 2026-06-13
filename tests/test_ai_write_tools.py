"""Tests for the AI assistant write/action tools.

Covers the tools that mutate workspace state:
- save_draft, create_task_list, switch_account, star_email, label_email,
  share_file, edit_event

Each test mocks the relevant GogAPI method (async return) and a fake app, then
asserts the handler calls the right method and returns a success string. Also
asserts every new tool name is registered (TOOLS / TOOL_BY_NAME) and appears in
SYSTEM_INSTRUCTION. Mirrors tests/test_ai_tools.py + tests/test_read_tools.py.
"""
import unittest
from unittest import mock

from gogmail.app import (
    TOOLS,
    TOOL_BY_NAME,
    SYSTEM_INSTRUCTION,
    execute_tool,
    _tool_save_draft,
    _tool_create_task_list,
    _tool_switch_account,
    _tool_star_email,
    _tool_label_email,
    _tool_share_file,
    _tool_edit_event,
)
from gogmail.gog_api import GogAPI


_NEW_TOOLS = [
    "save_draft", "create_task_list", "switch_account",
    "star_email", "label_email", "share_file", "edit_event",
]


def _async(value):
    """Return an async function that resolves to *value* and records its args."""
    async def _coro(*args, **kwargs):
        return value
    return _coro


def _fake_app(selected_thread_id="THREAD123"):
    """App stand-in: async safe_refresh/switch_account, and a Gmail tab whose
    selected_thread_id is configurable (None for the no-selection cases)."""
    app = mock.MagicMock()
    app.safe_refresh = mock.AsyncMock()
    app.switch_account = mock.AsyncMock()
    tab = mock.MagicMock()
    tab.selected_thread_id = selected_thread_id
    app.query_one.return_value = tab
    return app


# ---------------------------------------------------------------------------
# Registry / prompt wiring
# ---------------------------------------------------------------------------

class TestWriteToolsRegistered(unittest.TestCase):
    def test_new_tools_in_tools_and_by_name(self):
        names = {t["name"] for t in TOOLS}
        for name in _NEW_TOOLS:
            self.assertIn(name, names)
            self.assertIn(name, TOOL_BY_NAME)

    def test_new_tools_in_system_instruction(self):
        for name in _NEW_TOOLS:
            self.assertIn(name, SYSTEM_INSTRUCTION)


# ---------------------------------------------------------------------------
# save_draft
# ---------------------------------------------------------------------------

class TestSaveDraft(unittest.IsolatedAsyncioTestCase):
    async def test_calls_create_draft_and_returns_success(self):
        captured = {}

        async def fake(to, subject, body, *a, **k):
            captured.update(to=to, subject=subject, body=body)
            return True, ""

        app = _fake_app()
        with mock.patch.object(GogAPI, "gmail_create_draft", fake):
            result = await _tool_save_draft(app, {"to": "a@b.com", "subject": "Hi", "body": "Yo"})
        self.assertEqual(captured, {"to": "a@b.com", "subject": "Hi", "body": "Yo"})
        self.assertIn("Saved a draft", result)
        app.safe_refresh.assert_awaited()

    async def test_failure_message(self):
        with mock.patch.object(GogAPI, "gmail_create_draft", _async((False, "boom"))):
            result = await _tool_save_draft(_fake_app(), {"to": "a@b.com"})
        self.assertIn("Failed", result)
        self.assertIn("boom", result)


# ---------------------------------------------------------------------------
# create_task_list
# ---------------------------------------------------------------------------

class TestCreateTaskList(unittest.IsolatedAsyncioTestCase):
    async def test_calls_lists_create_and_refreshes(self):
        captured = {}

        async def fake(title):
            captured["title"] = title
            return True

        app = _fake_app()
        with mock.patch.object(GogAPI, "tasks_lists_create", fake):
            result = await _tool_create_task_list(app, {"title": "Project X"})
        self.assertEqual(captured["title"], "Project X")
        self.assertIn("Created task list", result)
        app.safe_refresh.assert_awaited()

    async def test_failure_message(self):
        with mock.patch.object(GogAPI, "tasks_lists_create", _async(False)):
            result = await _tool_create_task_list(_fake_app(), {"title": "X"})
        self.assertIn("Failed", result)

    async def test_missing_title_reported_by_execute_tool(self):
        result = await execute_tool(None, "create_task_list", {})
        self.assertIn("missing required parameter", result)
        self.assertIn("title", result)


# ---------------------------------------------------------------------------
# switch_account
# ---------------------------------------------------------------------------

class TestSwitchAccount(unittest.IsolatedAsyncioTestCase):
    async def test_calls_app_switch_account(self):
        app = _fake_app()
        result = await _tool_switch_account(app, {"email": "new@example.com"})
        app.switch_account.assert_awaited_once_with("new@example.com")
        self.assertIn("Switched", result)
        self.assertIn("new@example.com", result)

    async def test_missing_email_reported_by_execute_tool(self):
        result = await execute_tool(None, "switch_account", {})
        self.assertIn("missing required parameter", result)
        self.assertIn("email", result)


# ---------------------------------------------------------------------------
# star_email
# ---------------------------------------------------------------------------

class TestStarEmail(unittest.IsolatedAsyncioTestCase):
    async def test_stars_explicit_thread_id(self):
        captured = {}

        async def fake(thread_id, add="", remove=""):
            captured.update(thread_id=thread_id, add=add)
            return True

        app = _fake_app()
        with mock.patch.object(GogAPI, "gmail_modify_labels", fake):
            result = await _tool_star_email(app, {"thread_id": "T9"})
        self.assertEqual(captured, {"thread_id": "T9", "add": "STARRED"})
        self.assertIn("Starred", result)
        app.safe_refresh.assert_awaited()

    async def test_defaults_to_selected_thread(self):
        captured = {}

        async def fake(thread_id, add="", remove=""):
            captured["thread_id"] = thread_id
            return True

        app = _fake_app(selected_thread_id="SELECTED42")
        with mock.patch.object(GogAPI, "gmail_modify_labels", fake):
            await _tool_star_email(app, {})
        self.assertEqual(captured["thread_id"], "SELECTED42")

    async def test_no_selection_errors(self):
        app = _fake_app(selected_thread_id=None)
        result = await _tool_star_email(app, {})
        self.assertIn("Error", result)


# ---------------------------------------------------------------------------
# label_email
# ---------------------------------------------------------------------------

class TestLabelEmail(unittest.IsolatedAsyncioTestCase):
    async def test_applies_label_to_selected_thread(self):
        captured = {}

        async def fake(thread_id, add="", remove=""):
            captured.update(thread_id=thread_id, add=add)
            return True

        app = _fake_app(selected_thread_id="SEL1")
        with mock.patch.object(GogAPI, "gmail_modify_labels", fake):
            result = await _tool_label_email(app, {"label": "Important"})
        self.assertEqual(captured, {"thread_id": "SEL1", "add": "Important"})
        self.assertIn("Important", result)
        app.safe_refresh.assert_awaited()

    async def test_missing_label_reported_by_execute_tool(self):
        result = await execute_tool(_fake_app(), "label_email", {})
        self.assertIn("missing required parameter", result)
        self.assertIn("label", result)

    async def test_no_selection_errors(self):
        app = _fake_app(selected_thread_id=None)
        with mock.patch.object(GogAPI, "gmail_modify_labels", _async(True)):
            result = await _tool_label_email(app, {"label": "Work"})
        self.assertIn("Error", result)


# ---------------------------------------------------------------------------
# share_file
# ---------------------------------------------------------------------------

class TestShareFile(unittest.IsolatedAsyncioTestCase):
    async def test_shares_with_default_role(self):
        captured = {}

        async def fake(file_id, email, role="reader", notify=False):
            captured.update(file_id=file_id, email=email, role=role)
            return True, ""

        app = _fake_app()
        with mock.patch.object(GogAPI, "drive_share", fake):
            result = await _tool_share_file(app, {"file_id": "F1", "email": "x@y.com"})
        self.assertEqual(captured, {"file_id": "F1", "email": "x@y.com", "role": "reader"})
        self.assertIn("Shared", result)
        app.safe_refresh.assert_awaited()

    async def test_respects_explicit_role(self):
        captured = {}

        async def fake(file_id, email, role="reader", notify=False):
            captured["role"] = role
            return True, ""

        with mock.patch.object(GogAPI, "drive_share", fake):
            await _tool_share_file(_fake_app(), {"file_id": "F1", "email": "x@y.com", "role": "writer"})
        self.assertEqual(captured["role"], "writer")

    async def test_missing_required_params_reported(self):
        result = await execute_tool(None, "share_file", {})
        self.assertIn("missing required parameter", result)
        self.assertIn("file_id", result)
        self.assertIn("email", result)


# ---------------------------------------------------------------------------
# edit_event
# ---------------------------------------------------------------------------

class TestEditEvent(unittest.IsolatedAsyncioTestCase):
    async def test_updates_event_and_refreshes_calendar(self):
        captured = {}

        async def fake(calendar_id, event_id, summary=None, start_time=None,
                       end_time=None, description=None, location=None):
            captured.update(calendar_id=calendar_id, event_id=event_id,
                            summary=summary, start_time=start_time)
            return True, ""

        app = _fake_app()
        with mock.patch.object(GogAPI, "calendar_update_event", fake):
            result = await _tool_edit_event(
                app, {"event_id": "E1", "summary": "Renamed", "start": "2026-06-11T10:00:00Z"})
        self.assertEqual(captured["calendar_id"], "primary")
        self.assertEqual(captured["event_id"], "E1")
        self.assertEqual(captured["summary"], "Renamed")
        self.assertEqual(captured["start_time"], "2026-06-11T10:00:00Z")
        self.assertIn("Updated", result)
        app.safe_refresh.assert_awaited()

    async def test_failure_message(self):
        with mock.patch.object(GogAPI, "calendar_update_event", _async((False, "nope"))):
            result = await _tool_edit_event(_fake_app(), {"event_id": "E1"})
        self.assertIn("Failed", result)
        self.assertIn("nope", result)

    async def test_missing_event_id_reported_by_execute_tool(self):
        result = await execute_tool(None, "edit_event", {})
        self.assertIn("missing required parameter", result)
        self.assertIn("event_id", result)


if __name__ == "__main__":
    unittest.main()
