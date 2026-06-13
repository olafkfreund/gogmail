"""Tests for the Google Groups GogAPI wrappers.

Groups is an Admin API (Cloud Identity / Workspace Directory), but the TUI
layer only cares that the right `gog groups ...` args are built and that the
JSON envelope is parsed. Mocking `run_gog` exercises both without a real
`gog` binary or any Admin scopes.
"""
import unittest
from unittest import mock

from gogmail import gog_api
from gogmail.gog_api import GogAPI


def _fake_run_gog(result):
    async def _run(args, parse_json=True, quiet=False, stdin_data=None):
        return result
    return _run


class TestGroupsReads(unittest.IsolatedAsyncioTestCase):
    async def test_groups_list_builds_args(self):
        seen = {}
        async def fake(args, parse_json=True, quiet=False, stdin_data=None):
            seen["args"] = args
            return True, {"groups": [{"email": "eng@x.com", "name": "Eng"}]}
        with mock.patch.object(gog_api, "run_gog", fake):
            groups = await GogAPI.groups_list()
        self.assertEqual(seen["args"], ["groups", "list"])
        self.assertEqual(groups, [{"email": "eng@x.com", "name": "Eng"}])

    async def test_groups_list_empty_on_failure(self):
        with mock.patch.object(gog_api, "run_gog", _fake_run_gog((False, "no permission"))):
            self.assertEqual(await GogAPI.groups_list(), [])

    async def test_groups_list_empty_when_key_missing(self):
        with mock.patch.object(gog_api, "run_gog", _fake_run_gog((True, {"other": []}))):
            self.assertEqual(await GogAPI.groups_list(), [])

    async def test_group_members_builds_args(self):
        seen = {}
        async def fake(args, parse_json=True, quiet=False, stdin_data=None):
            seen["args"] = args
            return True, {"members": [{"email": "a@x.com", "role": "OWNER"}]}
        with mock.patch.object(gog_api, "run_gog", fake):
            members = await GogAPI.group_members("eng@x.com")
        self.assertEqual(seen["args"], ["groups", "members", "eng@x.com"])
        self.assertEqual(members, [{"email": "a@x.com", "role": "OWNER"}])

    async def test_group_members_empty_on_failure(self):
        with mock.patch.object(gog_api, "run_gog", _fake_run_gog((False, "boom"))):
            self.assertEqual(await GogAPI.group_members("eng@x.com"), [])


if __name__ == "__main__":
    unittest.main()
