"""Tests for the niche read-only services (Photos / YouTube / Classroom / Sites).

These all funnel through `run_gog`, so mocking that one seam exercises the
GogAPI read methods without a real `gog` binary. Each test asserts both the
exact args the method builds and that it parses the right envelope key.
"""
import unittest
from unittest import mock

from gogmail import gog_api
from gogmail.gog_api import GogAPI


def _capture():
    """Return (seen, fake_run_gog) where `seen['args']` holds the args passed."""
    seen = {}

    def make(result):
        async def fake(args, parse_json=True, quiet=False, stdin_data=None):
            seen["args"] = args
            return result
        return fake

    return seen, make


class TestNicheReads(unittest.IsolatedAsyncioTestCase):
    async def test_photos_list_builds_args_and_parses(self):
        seen, make = _capture()
        payload = (True, {"mediaItems": [{"id": "p1", "filename": "a.jpg"}]})
        with mock.patch.object(gog_api, "run_gog", make(payload)):
            result = await GogAPI.photos_list()
        self.assertEqual(seen["args"], ["photos", "list"])
        self.assertEqual(result, [{"id": "p1", "filename": "a.jpg"}])

    async def test_photos_list_empty_on_failure(self):
        _, make = _capture()
        with mock.patch.object(gog_api, "run_gog", make((False, "boom"))):
            self.assertEqual(await GogAPI.photos_list(), [])

    async def test_youtube_list_builds_args_and_parses(self):
        seen, make = _capture()
        payload = (True, {"items": [{"id": "pl1", "snippet": {"title": "Mix"}}]})
        with mock.patch.object(gog_api, "run_gog", make(payload)):
            result = await GogAPI.youtube_list()
        self.assertEqual(seen["args"], ["youtube", "playlists", "list", "--mine"])
        self.assertEqual(result, [{"id": "pl1", "snippet": {"title": "Mix"}}])

    async def test_youtube_list_empty_on_failure(self):
        _, make = _capture()
        with mock.patch.object(gog_api, "run_gog", make((False, "boom"))):
            self.assertEqual(await GogAPI.youtube_list(), [])

    async def test_classroom_list_builds_args_and_parses(self):
        seen, make = _capture()
        payload = (True, {"courses": [{"id": "c1", "name": "Math"}]})
        with mock.patch.object(gog_api, "run_gog", make(payload)):
            result = await GogAPI.classroom_list()
        self.assertEqual(seen["args"], ["classroom", "courses", "list"])
        self.assertEqual(result, [{"id": "c1", "name": "Math"}])

    async def test_classroom_list_empty_on_failure(self):
        _, make = _capture()
        with mock.patch.object(gog_api, "run_gog", make((False, "boom"))):
            self.assertEqual(await GogAPI.classroom_list(), [])

    async def test_sites_list_builds_args_and_parses(self):
        seen, make = _capture()
        payload = (True, {"files": [{"id": "s1", "name": "FreundCloud"}]})
        with mock.patch.object(gog_api, "run_gog", make(payload)):
            result = await GogAPI.sites_list()
        self.assertEqual(seen["args"], ["sites", "list"])
        self.assertEqual(result, [{"id": "s1", "name": "FreundCloud"}])

    async def test_sites_list_empty_on_failure(self):
        _, make = _capture()
        with mock.patch.object(gog_api, "run_gog", make((False, "boom"))):
            self.assertEqual(await GogAPI.sites_list(), [])

    async def test_reads_ignore_wrong_envelope_key(self):
        # A success with the wrong key must yield [] (not crash).
        _, make = _capture()
        with mock.patch.object(gog_api, "run_gog", make((True, {"wrong": [1]}))):
            self.assertEqual(await GogAPI.photos_list(), [])
            self.assertEqual(await GogAPI.youtube_list(), [])
            self.assertEqual(await GogAPI.classroom_list(), [])
            self.assertEqual(await GogAPI.sites_list(), [])


if __name__ == "__main__":
    unittest.main()
