"""Tests for the Sheets inline cell-edit and append-row API methods.

These funnel through `run_gog` like the rest of GogAPI, so mocking that one
seam exercises the argument-building and result handling without a real `gog`.
"""
import json
import unittest
from unittest import mock

from gogmail import gog_api
from gogmail.gog_api import GogAPI


class TestSheetsSetCell(unittest.IsolatedAsyncioTestCase):
    async def test_set_cell_builds_update_args(self):
        seen = {}
        async def fake(args, parse_json=True, quiet=False, stdin_data=None):
            seen["args"] = args
            return True, {}
        with mock.patch.object(gog_api, "run_gog", fake):
            ok = await GogAPI.sheets_set_cell("SID", "B3", "hello")
        self.assertTrue(ok)
        self.assertEqual(seen["args"][:4], ["sheets", "update", "SID", "B3"])
        # JSON 2D array carries the single cell so arbitrary text is safe.
        self.assertIn("--values-json", seen["args"])
        payload = seen["args"][seen["args"].index("--values-json") + 1]
        self.assertEqual(json.loads(payload), [["hello"]])

    async def test_set_cell_returns_false_on_failure(self):
        async def fake(args, parse_json=True, quiet=False, stdin_data=None):
            return False, "boom"
        with mock.patch.object(gog_api, "run_gog", fake):
            self.assertFalse(await GogAPI.sheets_set_cell("SID", "A1", "x"))


class TestSheetsAppendRow(unittest.IsolatedAsyncioTestCase):
    async def test_append_row_builds_append_args(self):
        seen = {}
        async def fake(args, parse_json=True, quiet=False, stdin_data=None):
            seen["args"] = args
            return True, {}
        with mock.patch.object(gog_api, "run_gog", fake):
            ok = await GogAPI.sheets_append_row("SID", ["a", "b", "c"])
        self.assertTrue(ok)
        self.assertEqual(seen["args"][:4], ["sheets", "append", "SID", "A1"])
        payload = seen["args"][seen["args"].index("--values-json") + 1]
        # One appended row of three cells.
        self.assertEqual(json.loads(payload), [["a", "b", "c"]])

    async def test_append_row_returns_false_on_failure(self):
        async def fake(args, parse_json=True, quiet=False, stdin_data=None):
            return False, "nope"
        with mock.patch.object(gog_api, "run_gog", fake):
            self.assertFalse(await GogAPI.sheets_append_row("SID", ["x"]))


class TestSheetsUpdate(unittest.IsolatedAsyncioTestCase):
    async def test_update_uses_values_json(self):
        seen = {}
        async def fake(args, parse_json=True, quiet=False, stdin_data=None):
            seen["args"] = args
            return True, {}
        with mock.patch.object(gog_api, "run_gog", fake):
            ok = await GogAPI.sheets_update("SID", "A1:B2", [["1", "2"], ["3", "4"]])
        self.assertTrue(ok)
        self.assertEqual(seen["args"][:4], ["sheets", "update", "SID", "A1:B2"])
        payload = seen["args"][seen["args"].index("--values-json") + 1]
        self.assertEqual(json.loads(payload), [["1", "2"], ["3", "4"]])


if __name__ == "__main__":
    unittest.main()
