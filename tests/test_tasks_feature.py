"""Tests for the Tasks edit/due-date/clear-completed feature (#3).

Like the rest of the suite, the only mocked seam is `gog_api.run_gog`, so these
exercise that the GogAPI Tasks methods build the right `gog` arg lists and parse
results correctly.
"""
import unittest
from unittest import mock

from gogmail import gog_api
from gogmail.gog_api import GogAPI


def _fake_run_gog(result):
    async def _run(args, parse_json=True, quiet=False, stdin_data=None):
        return result
    return _run


class TestTasksAddDue(unittest.IsolatedAsyncioTestCase):
    async def test_add_includes_due_when_given(self):
        seen = {}
        async def fake(args, parse_json=True, quiet=False, stdin_data=None):
            seen["args"] = args
            return True, {}
        with mock.patch.object(gog_api, "run_gog", fake):
            ok = await GogAPI.tasks_add("L1", "Buy milk", notes="2%", due="2026-06-13")
        self.assertTrue(ok)
        self.assertEqual(
            seen["args"],
            ["tasks", "add", "L1", "--title", "Buy milk", "--notes", "2%", "--due", "2026-06-13"],
        )

    async def test_add_omits_due_when_empty(self):
        seen = {}
        async def fake(args, parse_json=True, quiet=False, stdin_data=None):
            seen["args"] = args
            return True, {}
        with mock.patch.object(gog_api, "run_gog", fake):
            await GogAPI.tasks_add("L1", "Just a title")
        self.assertEqual(seen["args"], ["tasks", "add", "L1", "--title", "Just a title"])
        self.assertNotIn("--due", seen["args"])
        self.assertNotIn("--notes", seen["args"])


class TestTasksEdit(unittest.IsolatedAsyncioTestCase):
    async def test_edit_includes_only_set_fields(self):
        seen = {}
        async def fake(args, parse_json=True, quiet=False, stdin_data=None):
            seen["args"] = args
            return True, {}
        with mock.patch.object(gog_api, "run_gog", fake):
            ok, _ = await GogAPI.tasks_edit("L1", "T1", title="New", due="2026-07-01")
        self.assertTrue(ok)
        self.assertEqual(
            seen["args"],
            ["tasks", "update", "L1", "T1", "--title", "New", "--due", "2026-07-01"],
        )
        # notes omitted because it was None
        self.assertNotIn("--notes", seen["args"])

    async def test_edit_passes_empty_string_to_clear(self):
        seen = {}
        async def fake(args, parse_json=True, quiet=False, stdin_data=None):
            seen["args"] = args
            return True, {}
        with mock.patch.object(gog_api, "run_gog", fake):
            await GogAPI.tasks_edit("L1", "T1", notes="", due="")
        self.assertEqual(
            seen["args"],
            ["tasks", "update", "L1", "T1", "--notes", "", "--due", ""],
        )

    async def test_edit_returns_str_tuple(self):
        with mock.patch.object(gog_api, "run_gog", _fake_run_gog((True, {"id": "T1"}))):
            ok, res = await GogAPI.tasks_edit("L1", "T1", title="X")
        self.assertTrue(ok)
        self.assertIsInstance(res, str)

    async def test_edit_surfaces_failure(self):
        with mock.patch.object(gog_api, "run_gog", _fake_run_gog((False, "boom"))):
            ok, res = await GogAPI.tasks_edit("L1", "T1", title="X")
        self.assertFalse(ok)
        self.assertEqual(res, "boom")


class TestTasksClearCompleted(unittest.IsolatedAsyncioTestCase):
    async def test_clear_builds_args(self):
        seen = {}
        async def fake(args, parse_json=True, quiet=False, stdin_data=None):
            seen["args"] = args
            return True, {}
        with mock.patch.object(gog_api, "run_gog", fake):
            ok = await GogAPI.tasks_clear_completed("L1")
        self.assertTrue(ok)
        self.assertEqual(seen["args"], ["tasks", "clear", "L1"])

    async def test_clear_returns_false_on_failure(self):
        with mock.patch.object(gog_api, "run_gog", _fake_run_gog((False, "nope"))):
            ok = await GogAPI.tasks_clear_completed("L1")
        self.assertFalse(ok)


if __name__ == "__main__":
    unittest.main()
