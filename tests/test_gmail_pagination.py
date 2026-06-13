"""Tests for Gmail pagination (issue #6).

Like test_gog_api.py, these mock the single `gog_api.run_gog` seam so no real
`gog` binary is needed. They assert the paging method passes --max/--page and
threads the nextPageToken out of the envelope.
"""
import unittest
from unittest import mock

from gogmail import gog_api
from gogmail.gog_api import GogAPI


class TestGmailSearchPage(unittest.IsolatedAsyncioTestCase):
    async def test_passes_max_and_returns_threads_and_token(self):
        seen = {}

        async def fake(args, parse_json=True, quiet=False, stdin_data=None):
            seen["args"] = args
            return True, {"threads": [{"id": "1"}, {"id": "2"}], "nextPageToken": "TOK"}

        with mock.patch.object(gog_api, "run_gog", fake):
            threads, token = await GogAPI.gmail_search_page("is:unread", max_results=25)

        self.assertEqual(threads, [{"id": "1"}, {"id": "2"}])
        self.assertEqual(token, "TOK")
        # --max is always passed; --page is omitted on the first page.
        self.assertEqual(seen["args"], ["gmail", "search", "is:unread", "--max", "25"])
        self.assertNotIn("--page", seen["args"])

    async def test_passes_page_token_on_subsequent_page(self):
        seen = {}

        async def fake(args, parse_json=True, quiet=False, stdin_data=None):
            seen["args"] = args
            return True, {"threads": [{"id": "3"}], "nextPageToken": ""}

        with mock.patch.object(gog_api, "run_gog", fake):
            threads, token = await GogAPI.gmail_search_page(
                "is:unread", max_results=10, page_token="PREV")

        self.assertEqual(threads, [{"id": "3"}])
        # Empty envelope token => no more pages.
        self.assertEqual(token, "")
        self.assertEqual(
            seen["args"],
            ["gmail", "search", "is:unread", "--max", "10", "--page", "PREV"])
        # --results-only would drop the envelope (and the token): never use it.
        self.assertNotIn("--results-only", seen["args"])

    async def test_no_token_when_envelope_lacks_it(self):
        async def fake(args, parse_json=True, quiet=False, stdin_data=None):
            return True, {"threads": [{"id": "1"}]}

        with mock.patch.object(gog_api, "run_gog", fake):
            threads, token = await GogAPI.gmail_search_page("is:unread")

        self.assertEqual(threads, [{"id": "1"}])
        self.assertEqual(token, "")

    async def test_empty_on_failure(self):
        async def fake(args, parse_json=True, quiet=False, stdin_data=None):
            return False, "boom"

        with mock.patch.object(gog_api, "run_gog", fake):
            threads, token = await GogAPI.gmail_search_page("is:unread")

        self.assertEqual(threads, [])
        self.assertEqual(token, "")

    async def test_gmail_search_delegates_and_drops_token(self):
        async def fake(args, parse_json=True, quiet=False, stdin_data=None):
            return True, {"threads": [{"id": "1"}], "nextPageToken": "TOK"}

        with mock.patch.object(gog_api, "run_gog", fake):
            self.assertEqual(await GogAPI.gmail_search("is:unread"), [{"id": "1"}])


if __name__ == "__main__":
    unittest.main()
