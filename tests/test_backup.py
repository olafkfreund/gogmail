"""Tests for the account backup wrapper (GogAPI.backup → `gog backup push`).

Like the rest of the suite, these mock the single `run_gog` seam so no real
`gog` binary is needed.
"""
import unittest
from unittest import mock

from gogmail import gog_api
from gogmail.gog_api import GogAPI


class TestBackup(unittest.IsolatedAsyncioTestCase):
    async def test_backup_builds_args_with_destination_and_services(self):
        seen = {}

        async def fake(args, parse_json=True, quiet=False, stdin_data=None):
            seen["args"] = args
            return True, {"status": "ok"}

        with mock.patch.object(gog_api, "run_gog", fake):
            ok, msg = await GogAPI.backup(destination="/tmp/bk", services="gmail,drive")

        self.assertTrue(ok)
        self.assertEqual(
            seen["args"],
            ["backup", "push", "--no-input", "--repo", "/tmp/bk", "--services", "gmail,drive"],
        )
        # message is a string (the JSON-encoded result)
        self.assertIsInstance(msg, str)

    async def test_backup_omits_optional_flags_when_unset(self):
        seen = {}

        async def fake(args, parse_json=True, quiet=False, stdin_data=None):
            seen["args"] = args
            return True, "done"

        with mock.patch.object(gog_api, "run_gog", fake):
            ok, msg = await GogAPI.backup()

        self.assertTrue(ok)
        self.assertEqual(seen["args"], ["backup", "push", "--no-input"])
        self.assertNotIn("--repo", seen["args"])
        self.assertNotIn("--services", seen["args"])
        self.assertEqual(msg, "done")

    async def test_backup_returns_failure_message(self):
        async def fake(args, parse_json=True, quiet=False, stdin_data=None):
            return False, "backup repo not initialized"

        with mock.patch.object(gog_api, "run_gog", fake):
            ok, msg = await GogAPI.backup(destination="/tmp/bk")

        self.assertFalse(ok)
        self.assertEqual(msg, "backup repo not initialized")


if __name__ == "__main__":
    unittest.main()
