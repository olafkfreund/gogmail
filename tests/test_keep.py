"""Tests for the Google Keep (Notes) GogAPI methods.

Mocks the single `run_gog` seam to assert each Keep method builds the correct
`gog keep ...` argument list and parses results per the GogAPI conventions
(reads -> list (empty on failure); mutations -> (bool, str)/bool).
"""
import unittest
from unittest import mock

from gogmail import gog_api
from gogmail.gog_api import GogAPI


def _fake_run_gog(result):
    async def _run(args, parse_json=True, quiet=False, stdin_data=None):
        return result
    return _run


class TestKeepList(unittest.IsolatedAsyncioTestCase):
    async def test_list_parses_notes(self):
        payload = {"notes": [{"id": "n1", "title": "A"}, {"id": "n2", "title": "B"}]}
        with mock.patch.object(gog_api, "run_gog", _fake_run_gog((True, payload))):
            notes = await GogAPI.keep_list()
        self.assertEqual(notes, [{"id": "n1", "title": "A"}, {"id": "n2", "title": "B"}])

    async def test_list_empty_on_failure(self):
        with mock.patch.object(gog_api, "run_gog", _fake_run_gog((False, "boom"))):
            self.assertEqual(await GogAPI.keep_list(), [])

    async def test_list_builds_args(self):
        seen = {}
        async def fake(args, parse_json=True, quiet=False, stdin_data=None):
            seen["args"] = args
            return True, {"notes": []}
        with mock.patch.object(gog_api, "run_gog", fake):
            await GogAPI.keep_list()
        self.assertEqual(seen["args"], ["keep", "list"])


class TestKeepCreate(unittest.IsolatedAsyncioTestCase):
    async def test_create_builds_title_and_text(self):
        seen = {}
        async def fake(args, parse_json=True, quiet=False, stdin_data=None):
            seen["args"] = args
            return True, {"id": "n1"}
        with mock.patch.object(gog_api, "run_gog", fake):
            ok, msg = await GogAPI.keep_create("Shopping", "Milk and eggs")
        self.assertTrue(ok)
        self.assertEqual(msg, '{"id": "n1"}')
        self.assertEqual(
            seen["args"],
            ["keep", "create", "--title", "Shopping", "--text", "Milk and eggs"],
        )

    async def test_create_omits_empty_text(self):
        seen = {}
        async def fake(args, parse_json=True, quiet=False, stdin_data=None):
            seen["args"] = args
            return True, {}
        with mock.patch.object(gog_api, "run_gog", fake):
            await GogAPI.keep_create("Title only", "")
        self.assertEqual(seen["args"], ["keep", "create", "--title", "Title only"])
        self.assertNotIn("--text", seen["args"])

    async def test_create_omits_empty_title(self):
        seen = {}
        async def fake(args, parse_json=True, quiet=False, stdin_data=None):
            seen["args"] = args
            return True, {}
        with mock.patch.object(gog_api, "run_gog", fake):
            await GogAPI.keep_create("", "body text")
        self.assertEqual(seen["args"], ["keep", "create", "--text", "body text"])
        self.assertNotIn("--title", seen["args"])

    async def test_create_returns_str_tuple_on_failure(self):
        with mock.patch.object(gog_api, "run_gog", _fake_run_gog((False, "denied"))):
            ok, msg = await GogAPI.keep_create("T", "B")
        self.assertFalse(ok)
        self.assertEqual(msg, "denied")


class TestKeepDelete(unittest.IsolatedAsyncioTestCase):
    async def test_delete_builds_args_with_force(self):
        seen = {}
        async def fake(args, parse_json=True, quiet=False, stdin_data=None):
            seen["args"] = args
            return True, {}
        with mock.patch.object(gog_api, "run_gog", fake):
            ok = await GogAPI.keep_delete("notes/abc123")
        self.assertTrue(ok)
        self.assertEqual(seen["args"], ["keep", "delete", "notes/abc123", "--force"])

    async def test_delete_returns_false_on_failure(self):
        with mock.patch.object(gog_api, "run_gog", _fake_run_gog((False, "boom"))):
            self.assertFalse(await GogAPI.keep_delete("notes/x"))


if __name__ == "__main__":
    unittest.main()
