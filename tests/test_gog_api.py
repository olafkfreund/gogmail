"""Tests for the gog CLI wrapper layer.

The entire Workspace surface funnels through `run_gog`, so mocking that one
function exercises every GogAPI method without a real `gog` binary.
"""
import unittest
from unittest import mock

from gogmail import gog_api
from gogmail.gog_api import GogAPI, _extract_list, _str_result


class TestPureHelpers(unittest.TestCase):
    def test_extract_list_happy(self):
        self.assertEqual(_extract_list(True, {"threads": [1, 2]}, "threads"), [1, 2])

    def test_extract_list_missing_key(self):
        self.assertEqual(_extract_list(True, {"other": []}, "threads"), [])

    def test_extract_list_failure_returns_empty(self):
        self.assertEqual(_extract_list(False, "error message", "threads"), [])

    def test_extract_list_fallback_key(self):
        self.assertEqual(
            _extract_list(True, {"connections": [9]}, "contacts", "connections"), [9]
        )

    def test_str_result_passthrough_and_dumps(self):
        self.assertEqual(_str_result("hello"), "hello")
        self.assertEqual(_str_result({"a": 1}), '{"a": 1}')

    def test_account_from_status_variants(self):
        self.assertEqual(GogAPI._account_from_status({"account": "a@b.com"}), "a@b.com")
        self.assertEqual(GogAPI._account_from_status({"user": {"email": "c@d.com"}}), "c@d.com")
        self.assertEqual(GogAPI._account_from_status({"nope": 1}), "")


class TestErrorSink(unittest.TestCase):
    def tearDown(self):
        gog_api.set_error_sink(None)

    def test_report_error_invokes_sink_with_subcommand(self):
        captured = []
        gog_api.set_error_sink(captured.append)
        gog_api._report_error(["gog", "--json", "gmail", "search", "is:unread"], "token expired")
        self.assertEqual(len(captured), 1)
        self.assertIn("gmail search", captured[0])
        self.assertIn("token expired", captured[0])

    def test_sink_exception_is_swallowed(self):
        def boom(_msg):
            raise RuntimeError("sink failed")
        gog_api.set_error_sink(boom)
        # Must not raise.
        gog_api._report_error(["gog", "status"], "x")


def _fake_run_gog(result):
    async def _run(args, parse_json=True):
        return result
    return _run


class TestGogAPIReads(unittest.IsolatedAsyncioTestCase):
    async def test_gmail_search_parses_threads(self):
        with mock.patch.object(gog_api, "run_gog", _fake_run_gog((True, {"threads": [{"id": "1"}]}))):
            self.assertEqual(await GogAPI.gmail_search("is:unread"), [{"id": "1"}])

    async def test_gmail_search_empty_on_failure(self):
        with mock.patch.object(gog_api, "run_gog", _fake_run_gog((False, "boom"))):
            self.assertEqual(await GogAPI.gmail_search("is:unread"), [])

    async def test_calendar_events_parses(self):
        with mock.patch.object(gog_api, "run_gog", _fake_run_gog((True, {"events": [1, 2, 3]}))):
            self.assertEqual(await GogAPI.calendar_events(), [1, 2, 3])

    async def test_gmail_send_returns_str_tuple(self):
        with mock.patch.object(gog_api, "run_gog", _fake_run_gog((True, {"id": "sent"}))):
            ok, msg = await GogAPI.gmail_send("a@b.com", "subj", "body")
            self.assertTrue(ok)
            self.assertEqual(msg, '{"id": "sent"}')

    async def test_preflight_unauthenticated(self):
        with mock.patch.object(gog_api, "run_gog", _fake_run_gog((True, {}))):
            ok, msg = await GogAPI.preflight()
            self.assertFalse(ok)
            self.assertIn("auth login", msg)

    async def test_preflight_ok_returns_account(self):
        with mock.patch.object(gog_api, "run_gog", _fake_run_gog((True, {"account": "me@x.com"}))):
            ok, account = await GogAPI.preflight()
            self.assertTrue(ok)
            self.assertEqual(account, "me@x.com")

    async def test_list_accounts_parses_emails(self):
        payload = {"accounts": [{"email": "a@x.com"}, {"email": "b@y.com"}, {"no_email": 1}]}
        with mock.patch.object(gog_api, "run_gog", _fake_run_gog((True, payload))):
            self.assertEqual(await GogAPI.list_accounts(), ["a@x.com", "b@y.com"])


class TestContacts(unittest.TestCase):
    def test_contact_email_flat_and_nested(self):
        self.assertEqual(GogAPI.contact_email({"email": "a@x.com"}), "a@x.com")
        self.assertEqual(GogAPI.contact_email({"emailAddresses": [{"value": "b@y.com"}]}), "b@y.com")
        self.assertEqual(GogAPI.contact_email({"phone": "123"}), "")

    def test_contact_name(self):
        self.assertEqual(GogAPI.contact_name({"name": "Bob"}), "Bob")
        self.assertEqual(GogAPI.contact_name({"names": [{"displayName": "Al"}]}), "Al")


class TestContactSuggestions(unittest.IsolatedAsyncioTestCase):
    async def test_suggestions_skip_emailless_and_dedupe(self):
        payload = {"contacts": [
            {"name": "Anna", "email": "anna@x.com"},
            {"name": "NoMail", "phone": "555"},
            {"name": "Anna2", "email": "anna@x.com"},  # dup email
        ]}
        with mock.patch.object(gog_api, "run_gog", _fake_run_gog((True, payload))):
            sugs = await GogAPI.contact_suggestions()
        self.assertIn("Anna <anna@x.com>", sugs)
        self.assertIn("anna@x.com", sugs)
        self.assertEqual(sugs.count("anna@x.com"), 1)  # deduped
        self.assertFalse(any("NoMail" in s for s in sugs))


class TestGmailLabelsAndDrafts(unittest.IsolatedAsyncioTestCase):
    async def test_modify_labels_builds_add_remove(self):
        seen = {}
        async def fake(args, parse_json=True, quiet=False):
            seen["args"] = args
            return True, {}
        with mock.patch.object(gog_api, "run_gog", fake):
            ok = await GogAPI.gmail_modify_labels("T1", add="STARRED", remove="UNREAD")
        self.assertTrue(ok)
        self.assertEqual(seen["args"], ["gmail", "labels", "modify", "T1", "--add", "STARRED", "--remove", "UNREAD"])

    async def test_labels_list_parses(self):
        with mock.patch.object(gog_api, "run_gog", _fake_run_gog((True, {"labels": [{"name": "INBOX"}]}))):
            self.assertEqual(await GogAPI.gmail_labels_list(), [{"name": "INBOX"}])

    async def test_create_draft_returns_str(self):
        with mock.patch.object(gog_api, "run_gog", _fake_run_gog((True, {"id": "d1"}))):
            ok, msg = await GogAPI.gmail_create_draft("a@x.com", "Subj", "Body")
            self.assertTrue(ok)
            self.assertEqual(msg, '{"id": "d1"}')


class TestCalendarUpdate(unittest.IsolatedAsyncioTestCase):
    async def test_update_includes_only_set_fields(self):
        seen = {}
        async def fake(args, parse_json=True, quiet=False):
            seen["args"] = args
            return True, {}
        with mock.patch.object(gog_api, "run_gog", fake):
            await GogAPI.calendar_update_event("primary", "E1", summary="New", location="Office")
        self.assertEqual(seen["args"],
                         ["calendar", "update", "primary", "E1", "--summary", "New", "--location", "Office"])
        # start/end/description omitted because they were None
        self.assertNotIn("--from", seen["args"])
        self.assertNotIn("--description", seen["args"])


class TestDriveActions(unittest.IsolatedAsyncioTestCase):
    async def test_share_builds_args(self):
        seen = {}
        async def fake(args, parse_json=True, quiet=False):
            seen["args"] = args
            return True, {}
        with mock.patch.object(gog_api, "run_gog", fake):
            ok, _ = await GogAPI.drive_share("F1", "a@x.com", role="writer", notify=True)
        self.assertTrue(ok)
        self.assertEqual(seen["args"],
                         ["drive", "share", "F1", "--to", "user", "--email", "a@x.com", "--role", "writer", "--notify"])

    async def test_rename_and_move(self):
        with mock.patch.object(gog_api, "run_gog", _fake_run_gog((True, {}))):
            self.assertTrue((await GogAPI.drive_rename("F1", "new"))[0])
            self.assertTrue((await GogAPI.drive_move("F1", "PARENT"))[0])


class TestActiveAccount(unittest.TestCase):
    def tearDown(self):
        gog_api.set_account(None)

    def test_set_and_get_account(self):
        gog_api.set_account("a@x.com")
        self.assertEqual(gog_api.get_account(), "a@x.com")
        gog_api.set_account("")
        self.assertIsNone(gog_api.get_account())


if __name__ == "__main__":
    unittest.main()
