"""Tests for Contacts create / update / delete (issue #4).

Mocks the single `run_gog` seam to assert each GogAPI contacts method builds
the correct `gog contacts ...` argv and parses its result, mirroring the style
of tests/test_gog_api.py.
"""
import unittest
from unittest import mock

from gogmail import gog_api
from gogmail.gog_api import GogAPI


class TestSplitName(unittest.TestCase):
    def test_split_name_variants(self):
        self.assertEqual(GogAPI._split_name("Ada Lovelace"), ("Ada", "Lovelace"))
        self.assertEqual(GogAPI._split_name("Madonna"), ("Madonna", ""))
        self.assertEqual(GogAPI._split_name("Carl Friedrich Gauss"),
                         ("Carl Friedrich", "Gauss"))
        self.assertEqual(GogAPI._split_name("  Grace  Hopper  "), ("Grace", "Hopper"))
        self.assertEqual(GogAPI._split_name(""), ("", ""))


class TestContactsCreate(unittest.IsolatedAsyncioTestCase):
    async def _run_capture(self, coro_factory, result):
        seen = {}

        async def fake(args, parse_json=True, quiet=False, stdin_data=None):
            seen["args"] = args
            return result
        with mock.patch.object(gog_api, "run_gog", fake):
            ret = await coro_factory()
        return seen.get("args"), ret

    async def test_create_full_builds_args_and_returns_str_tuple(self):
        args, (ok, msg) = await self._run_capture(
            lambda: GogAPI.contacts_create("Ada Lovelace", "ada@x.com", "+1 555"),
            (True, {"id": "people/c1"}),
        )
        self.assertEqual(args, [
            "contacts", "create", "--given", "Ada", "--family", "Lovelace",
            "--email", "ada@x.com", "--phone", "+1 555",
        ])
        self.assertTrue(ok)
        self.assertEqual(msg, '{"id": "people/c1"}')

    async def test_create_name_only_omits_optional_flags(self):
        args, (ok, _msg) = await self._run_capture(
            lambda: GogAPI.contacts_create("Madonna"),
            (True, {}),
        )
        # No family (single token), no email, no phone.
        self.assertEqual(args, ["contacts", "create", "--given", "Madonna"])
        self.assertTrue(ok)

    async def test_create_failure_returns_error(self):
        args, (ok, msg) = await self._run_capture(
            lambda: GogAPI.contacts_create("Ada Lovelace"),
            (False, "boom"),
        )
        self.assertIn("--given", args)
        self.assertFalse(ok)
        self.assertEqual(msg, "boom")


class TestContactsUpdate(unittest.IsolatedAsyncioTestCase):
    async def _run_capture(self, coro_factory, result):
        seen = {}

        async def fake(args, parse_json=True, quiet=False, stdin_data=None):
            seen["args"] = args
            return result
        with mock.patch.object(gog_api, "run_gog", fake):
            ret = await coro_factory()
        return seen.get("args"), ret

    async def test_update_all_fields(self):
        args, (ok, _msg) = await self._run_capture(
            lambda: GogAPI.contacts_update(
                "people/c1", name="Grace Hopper", email="grace@x.com", phone="555"),
            (True, {}),
        )
        self.assertEqual(args, [
            "contacts", "update", "people/c1",
            "--given", "Grace", "--family", "Hopper",
            "--email", "grace@x.com", "--phone", "555",
        ])
        self.assertTrue(ok)

    async def test_update_only_provided_fields_change(self):
        # name is None -> no --given/--family; email/phone are absent -> not passed.
        args, _ = await self._run_capture(
            lambda: GogAPI.contacts_update("people/c1", email="new@x.com"),
            (True, {}),
        )
        self.assertEqual(args, ["contacts", "update", "people/c1", "--email", "new@x.com"])

    async def test_update_empty_string_clears_field(self):
        # An explicit empty string is passed through (gog treats "" as clear).
        args, _ = await self._run_capture(
            lambda: GogAPI.contacts_update("people/c1", email="", phone=""),
            (True, {}),
        )
        self.assertEqual(args,
                         ["contacts", "update", "people/c1", "--email", "", "--phone", ""])


class TestContactsDelete(unittest.IsolatedAsyncioTestCase):
    async def test_delete_uses_force_and_returns_bool(self):
        seen = {}

        async def fake(args, parse_json=True, quiet=False, stdin_data=None):
            seen["args"] = args
            return True, {}
        with mock.patch.object(gog_api, "run_gog", fake):
            ok = await GogAPI.contacts_delete("people/c1")
        self.assertEqual(seen["args"], ["contacts", "delete", "people/c1", "--force"])
        self.assertTrue(ok)

    async def test_delete_failure_returns_false(self):
        async def fake(args, parse_json=True, quiet=False, stdin_data=None):
            return False, "not found"
        with mock.patch.object(gog_api, "run_gog", fake):
            ok = await GogAPI.contacts_delete("people/missing")
        self.assertFalse(ok)


if __name__ == "__main__":
    unittest.main()
