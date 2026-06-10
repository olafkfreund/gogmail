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


if __name__ == "__main__":
    unittest.main()
