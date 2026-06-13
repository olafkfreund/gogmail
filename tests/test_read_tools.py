"""Tests for the AI assistant read-tools introduced in commit c418d2a.

Covers:
- _extract_tool_call: fence variants and edge cases
- execute_tool: missing-param error with example hint; dispatch with params
- _tool_search_emails, _tool_list_events, _tool_list_tasks,
  _tool_search_drive, _tool_read_doc, _tool_search_contacts
- Cap / truncation: result strings are bounded in length
"""
import unittest
from unittest import mock

from gogmail.app import (
    _extract_tool_call,
    execute_tool,
    _tool_search_emails,
    _tool_list_events,
    _tool_list_tasks,
    _tool_search_drive,
    _tool_read_doc,
    _tool_search_contacts,
    _MAX_CHARS,
    _MAX_ITEMS,
)
from gogmail.gog_api import GogAPI


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _async(value):
    """Return a coroutine that resolves to *value* (mirrors helper in other test files)."""
    async def _coro(*args, **kwargs):
        return value
    return _coro


def _fake_app():
    """Lightweight app stand-in; query_one raises so the try/except in
    _tool_search_emails swallows it without touching any real Textual state."""
    return mock.MagicMock()


# ---------------------------------------------------------------------------
# _extract_tool_call
# ---------------------------------------------------------------------------

class TestExtractToolCall(unittest.TestCase):

    def test_json_fenced_block(self):
        text = '```json\n{"tool": "list_tasks", "params": {}}\n```'
        result = _extract_tool_call(text)
        self.assertIsNotNone(result)
        self.assertEqual(result["tool"], "list_tasks")

    def test_bare_fenced_block(self):
        # Model sometimes emits ``` without "json" language tag
        text = '```\n{"tool": "list_events", "params": {"range": "today"}}\n```'
        result = _extract_tool_call(text)
        self.assertIsNotNone(result)
        self.assertEqual(result["tool"], "list_events")

    def test_raw_json_object(self):
        # Response IS the raw JSON object — no fence
        text = '{"tool": "search_drive", "params": {"query": "budget"}}'
        result = _extract_tool_call(text)
        self.assertIsNotNone(result)
        self.assertEqual(result["tool"], "search_drive")

    def test_prose_with_trailing_json(self):
        # Model adds prose then a JSON block — the block should still parse
        text = 'Sure, let me fetch that for you.\n```json\n{"tool": "read_doc", "params": {"doc_id": "1Abc"}}\n```'
        result = _extract_tool_call(text)
        self.assertIsNotNone(result)
        self.assertEqual(result["tool"], "read_doc")

    def test_plain_prose_returns_none(self):
        text = "Here are your emails from this morning."
        self.assertIsNone(_extract_tool_call(text))

    def test_json_missing_tool_key_returns_none(self):
        text = '```json\n{"action": "something", "value": 1}\n```'
        self.assertIsNone(_extract_tool_call(text))

    def test_malformed_json_returns_none(self):
        text = '```json\n{not valid json\n```'
        self.assertIsNone(_extract_tool_call(text))

    def test_tool_name_key_also_accepted(self):
        # The extractor also recognises "tool_name" as an alternative key
        text = '{"tool_name": "list_tasks", "params": {}}'
        result = _extract_tool_call(text)
        self.assertIsNotNone(result)
        self.assertIn("tool_name", result)

    def test_empty_string_returns_none(self):
        self.assertIsNone(_extract_tool_call(""))


# ---------------------------------------------------------------------------
# execute_tool
# ---------------------------------------------------------------------------

class TestExecuteTool(unittest.IsolatedAsyncioTestCase):

    async def test_unknown_tool_message(self):
        result = await execute_tool(None, "does_not_exist", {})
        self.assertIn("Unknown tool", result)

    async def test_missing_required_param_reports_hint(self):
        # search_drive requires "query"; expected example is "quarterly report"
        result = await execute_tool(None, "search_drive", {})
        self.assertIn("missing required parameter", result)
        self.assertIn("query", result)
        # The hint (example value) should be echoed
        self.assertIn("quarterly report", result)

    async def test_missing_required_param_read_doc(self):
        result = await execute_tool(None, "read_doc", {})
        self.assertIn("doc_id", result)
        # Example hint from the TOOLS registry
        self.assertIn("1AbC", result)

    async def test_dispatch_list_tasks_with_no_required_params(self):
        # list_tasks has no required params so it should dispatch immediately.
        with mock.patch.object(GogAPI, "tasks_list", _async([])):
            result = await execute_tool(_fake_app(), "list_tasks", {})
        # Empty list → "No open tasks" message
        self.assertIn("No open tasks", result)

    async def test_dispatch_search_drive_with_query(self):
        files = [{"name": "Q4 Report", "mimeType": "application/pdf", "id": "abc1"}]
        with mock.patch.object(GogAPI, "drive_search", _async(files)):
            result = await execute_tool(_fake_app(), "search_drive", {"query": "Q4"})
        self.assertIn("Q4 Report", result)


# ---------------------------------------------------------------------------
# _tool_search_emails
# ---------------------------------------------------------------------------

class TestToolSearchEmails(unittest.IsolatedAsyncioTestCase):

    def _make_threads(self, n):
        return [
            {"subject": f"Subject {i}", "from": f"sender{i}@example.com", "date": "2026-06-01"}
            for i in range(n)
        ]

    async def test_returns_subjects_for_three_threads(self):
        threads = self._make_threads(3)
        with mock.patch.object(GogAPI, "gmail_search", _async(threads)):
            result = await _tool_search_emails(_fake_app(), {"query": "is:unread"})
        for i in range(3):
            self.assertIn(f"Subject {i}", result)

    async def test_empty_query_defaults_to_inbox(self):
        captured = {}

        async def fake_search(query):
            captured["query"] = query
            return []

        with mock.patch.object(GogAPI, "gmail_search", fake_search):
            result = await _tool_search_emails(_fake_app(), {})
        self.assertEqual(captured["query"], "label:INBOX")
        self.assertIn("No emails matched", result)

    async def test_no_query_key_defaults_to_inbox(self):
        # params dict has no 'query' key at all
        captured = {}

        async def fake_search(query):
            captured["query"] = query
            return []

        with mock.patch.object(GogAPI, "gmail_search", fake_search):
            await _tool_search_emails(_fake_app(), {})
        self.assertEqual(captured["query"], "label:INBOX")

    async def test_returns_no_emails_message_on_empty_result(self):
        with mock.patch.object(GogAPI, "gmail_search", _async([])):
            result = await _tool_search_emails(_fake_app(), {"query": "from:boss@corp.com"})
        self.assertIn("No emails matched", result)


# ---------------------------------------------------------------------------
# _tool_list_events
# ---------------------------------------------------------------------------

class TestToolListEvents(unittest.IsolatedAsyncioTestCase):

    def _make_events(self, n):
        return [
            {
                "summary": f"Meeting {i}",
                "start": {"dateTime": f"2026-06-{10+i:02d}T10:00:00Z"},
            }
            for i in range(n)
        ]

    async def test_contains_event_summaries(self):
        events = self._make_events(3)
        with mock.patch.object(GogAPI, "calendar_events", _async(events)):
            result = await _tool_list_events(_fake_app(), {"range": "week"})
        for i in range(3):
            self.assertIn(f"Meeting {i}", result)

    async def test_empty_events_message(self):
        with mock.patch.object(GogAPI, "calendar_events", _async([])):
            result = await _tool_list_events(_fake_app(), {})
        self.assertIn("No calendar events", result)

    async def test_default_range_is_week(self):
        captured = {}

        async def fake_events(calendar_id="primary", time_range=None, time_from=None,
                              time_to=None, max_results=None):
            captured["range"] = time_range
            return []

        with mock.patch.object(GogAPI, "calendar_events", fake_events):
            await _tool_list_events(_fake_app(), {})
        self.assertEqual(captured["range"], "week")

    async def test_location_included_when_present(self):
        events = [{
            "summary": "Sprint Review",
            "start": {"date": "2026-06-10"},
            "location": "Room 4B",
        }]
        with mock.patch.object(GogAPI, "calendar_events", _async(events)):
            result = await _tool_list_events(_fake_app(), {"range": "week"})
        self.assertIn("Room 4B", result)


# ---------------------------------------------------------------------------
# _tool_list_tasks
# ---------------------------------------------------------------------------

class TestToolListTasks(unittest.IsolatedAsyncioTestCase):

    async def test_contains_titles_and_status(self):
        tasks = [
            {"title": "Write tests", "status": "needsAction"},
            {"title": "Ship feature", "status": "completed"},
        ]
        with mock.patch.object(GogAPI, "tasks_list", _async(tasks)):
            result = await _tool_list_tasks(_fake_app(), {})
        self.assertIn("Write tests", result)
        self.assertIn("Ship feature", result)
        self.assertIn("[open]", result)
        self.assertIn("[done]", result)

    async def test_empty_tasks_message(self):
        with mock.patch.object(GogAPI, "tasks_list", _async([])):
            result = await _tool_list_tasks(_fake_app(), {})
        self.assertIn("No open tasks", result)

    async def test_due_date_included_when_present(self):
        tasks = [{"title": "File taxes", "status": "needsAction", "due": "2026-04-15T00:00:00Z"}]
        with mock.patch.object(GogAPI, "tasks_list", _async(tasks)):
            result = await _tool_list_tasks(_fake_app(), {})
        self.assertIn("2026-04-15", result)

    async def test_calls_default_tasklist(self):
        captured = {}

        async def fake_list(tasklist_id):
            captured["id"] = tasklist_id
            return []

        with mock.patch.object(GogAPI, "tasks_list", fake_list):
            await _tool_list_tasks(_fake_app(), {})
        self.assertEqual(captured["id"], "@default")


# ---------------------------------------------------------------------------
# _tool_search_drive
# ---------------------------------------------------------------------------

class TestToolSearchDrive(unittest.IsolatedAsyncioTestCase):

    async def test_missing_query_returns_error(self):
        result = await _tool_search_drive(_fake_app(), {})
        self.assertIn("Error", result)
        self.assertIn("query", result)

    async def test_no_files_message(self):
        with mock.patch.object(GogAPI, "drive_search", _async([])):
            result = await _tool_search_drive(_fake_app(), {"query": "invoice"})
        self.assertIn("No Drive files matched", result)

    async def test_file_names_in_result(self):
        files = [
            {"name": "Budget 2026", "mimeType": "application/vnd.google-apps.spreadsheet", "id": "xyz"},
            {"name": "Proposal", "mimeType": "application/pdf", "id": "abc"},
        ]
        with mock.patch.object(GogAPI, "drive_search", _async(files)):
            result = await _tool_search_drive(_fake_app(), {"query": "budget"})
        self.assertIn("Budget 2026", result)
        self.assertIn("Proposal", result)

    async def test_file_id_in_result(self):
        files = [{"name": "Doc", "mimeType": "application/vnd.google-apps.document", "id": "DOC123"}]
        with mock.patch.object(GogAPI, "drive_search", _async(files)):
            result = await _tool_search_drive(_fake_app(), {"query": "doc"})
        self.assertIn("DOC123", result)


# ---------------------------------------------------------------------------
# _tool_read_doc
# ---------------------------------------------------------------------------

class TestToolReadDoc(unittest.IsolatedAsyncioTestCase):

    async def test_missing_doc_id_returns_error(self):
        result = await _tool_read_doc(_fake_app(), {})
        self.assertIn("Error", result)
        self.assertIn("doc_id", result)

    async def test_empty_document_message(self):
        with mock.patch.object(GogAPI, "docs_cat", _async("   ")):
            result = await _tool_read_doc(_fake_app(), {"doc_id": "abc123"})
        self.assertIn("empty", result)

    async def test_document_text_in_result(self):
        content = "This is the executive summary.\n\nKey points: …"
        with mock.patch.object(GogAPI, "docs_cat", _async(content)):
            result = await _tool_read_doc(_fake_app(), {"doc_id": "abc123"})
        self.assertIn("executive summary", result)
        self.assertIn("Document text:", result)


# ---------------------------------------------------------------------------
# _tool_search_contacts
# ---------------------------------------------------------------------------

class TestToolSearchContacts(unittest.IsolatedAsyncioTestCase):

    def _make_contacts(self, n):
        return [
            {"name": f"Person {i}", "email": f"person{i}@example.com"}
            for i in range(n)
        ]

    async def test_uses_contacts_search_when_query_given(self):
        captured = {}

        async def fake_search(query):
            captured["query"] = query
            return self._make_contacts(2)

        with mock.patch.object(GogAPI, "contacts_search", fake_search):
            result = await _tool_search_contacts(_fake_app(), {"query": "Beatriz"})
        self.assertEqual(captured["query"], "Beatriz")
        self.assertIn("Person 0", result)

    async def test_uses_contacts_list_when_no_query(self):
        called = {}

        async def fake_list():
            called["listed"] = True
            return self._make_contacts(2)

        with mock.patch.object(GogAPI, "contacts_list", fake_list):
            result = await _tool_search_contacts(_fake_app(), {})
        self.assertTrue(called.get("listed"))
        self.assertIn("Person 0", result)

    async def test_no_contacts_found_with_query(self):
        with mock.patch.object(GogAPI, "contacts_search", _async([])):
            result = await _tool_search_contacts(_fake_app(), {"query": "unknown person"})
        self.assertIn("No contacts found", result)

    async def test_empty_contact_list_message(self):
        with mock.patch.object(GogAPI, "contacts_list", _async([])):
            result = await _tool_search_contacts(_fake_app(), {})
        self.assertIn("empty", result)

    async def test_contact_names_and_emails_in_result(self):
        contacts = [{"name": "Alice Bob", "email": "alice@example.com"}]
        with mock.patch.object(GogAPI, "contacts_search", _async(contacts)):
            result = await _tool_search_contacts(_fake_app(), {"query": "Alice"})
        self.assertIn("Alice Bob", result)
        self.assertIn("alice@example.com", result)


# ---------------------------------------------------------------------------
# Cap / truncation tests
# ---------------------------------------------------------------------------

class TestCapAndTruncation(unittest.IsolatedAsyncioTestCase):
    """Verify that read handlers never return unboundedly long strings."""

    _TRUNCATION_MARKER = "\n…(truncated)"
    # Allow a small slack above _MAX_CHARS for wrapper text.
    _HARD_LIMIT = 7000

    async def _result_for_many_emails(self, n: int) -> str:
        threads = [
            {"subject": "x" * 200, "from": "a@b.com", "date": "2026-01-01"}
            for _ in range(n)
        ]
        with mock.patch.object(GogAPI, "gmail_search", _async(threads)):
            return await _tool_search_emails(_fake_app(), {"query": "is:unread"})

    async def test_search_emails_capped_at_max_items(self):
        result = await self._result_for_many_emails(100)
        # Only _MAX_ITEMS rows should appear (count "- [" prefix occurrences)
        self.assertLessEqual(result.count("- ["), _MAX_ITEMS)

    async def test_search_emails_char_limit(self):
        result = await self._result_for_many_emails(100)
        self.assertLessEqual(len(result), self._HARD_LIMIT)

    async def test_read_doc_truncates_very_long_document(self):
        long_text = "word " * 5000  # ~25 000 chars
        with mock.patch.object(GogAPI, "docs_cat", _async(long_text)):
            result = await _tool_read_doc(_fake_app(), {"doc_id": "bigdoc"})
        self.assertLessEqual(len(result), self._HARD_LIMIT)
        self.assertIn(self._TRUNCATION_MARKER, result)

    async def test_list_tasks_capped_at_max_items(self):
        tasks = [{"title": f"Task {i}", "status": "needsAction"} for i in range(100)]
        with mock.patch.object(GogAPI, "tasks_list", _async(tasks)):
            result = await _tool_list_tasks(_fake_app(), {})
        self.assertLessEqual(result.count("- ["), _MAX_ITEMS)

    async def test_search_drive_capped_at_max_items(self):
        files = [
            {"name": f"File{i}", "mimeType": "application/pdf", "id": f"id{i}"}
            for i in range(100)
        ]
        with mock.patch.object(GogAPI, "drive_search", _async(files)):
            result = await _tool_search_drive(_fake_app(), {"query": "report"})
        self.assertLessEqual(result.count("- "), _MAX_ITEMS)

    async def test_search_contacts_capped_at_max_items(self):
        contacts = [
            {"name": f"Person {i}", "email": f"p{i}@example.com"}
            for i in range(100)
        ]
        with mock.patch.object(GogAPI, "contacts_list", _async(contacts)):
            result = await _tool_search_contacts(_fake_app(), {})
        self.assertLessEqual(result.count("- "), _MAX_ITEMS)


if __name__ == "__main__":
    unittest.main()
