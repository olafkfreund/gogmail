"""Tests for the Zoom REST client (requests is mocked; no network)."""
import asyncio
import os
import unittest
from unittest import mock

from gogmail.zoom_api import ZoomAPI

_ZOOM_KEYS = ("GOG_ZOOM_ACCOUNT_ID", "GOG_ZOOM_CLIENT_ID", "GOG_ZOOM_CLIENT_SECRET")


class _Resp:
    def __init__(self, status, payload=None, text=""):
        self.status_code = status
        self._payload = payload or {}
        self.text = text

    def json(self):
        return self._payload


CREDS = {
    "GOG_ZOOM_ACCOUNT_ID": "acct",
    "GOG_ZOOM_CLIENT_ID": "cid",
    "GOG_ZOOM_CLIENT_SECRET": "secret",
}


class TestZoomCreateMeeting(unittest.TestCase):
    def test_missing_credentials_returns_error(self):
        # Only remove the Zoom keys — clearing the whole environment wipes PATH
        # etc. and trips interpreter/thread teardown noise under the sandbox.
        with mock.patch.dict("os.environ", {}, clear=False):
            for k in _ZOOM_KEYS:
                os.environ.pop(k, None)
            ok, msg = asyncio.run(ZoomAPI.create_meeting())
        self.assertFalse(ok)
        self.assertIn("credentials not set", msg.lower())

    def test_successful_create_returns_join_and_start_urls(self):
        def fake_post(url, **kwargs):
            if url.endswith("/oauth/token"):
                return _Resp(200, {"access_token": "tok"})
            return _Resp(201, {"join_url": "https://zoom.us/j/1", "start_url": "https://zoom.us/s/1"})

        with mock.patch.dict("os.environ", CREDS), \
                mock.patch("gogmail.zoom_api.requests.post", side_effect=fake_post):
            ok, data = asyncio.run(ZoomAPI.create_meeting("Standup"))
        self.assertTrue(ok)
        self.assertEqual(data["join_url"], "https://zoom.us/j/1")
        self.assertEqual(data["start_url"], "https://zoom.us/s/1")

    def test_scope_error_surfaces(self):
        def fake_post(url, **kwargs):
            if url.endswith("/oauth/token"):
                return _Resp(200, {"access_token": "tok"})
            return _Resp(400, text='{"code":4711,"message":"Invalid access token, does not contain scopes:[meeting:write]"}')

        with mock.patch.dict("os.environ", CREDS), \
                mock.patch("gogmail.zoom_api.requests.post", side_effect=fake_post):
            ok, msg = asyncio.run(ZoomAPI.create_meeting())
        self.assertFalse(ok)
        self.assertIn("meeting:write", msg)


if __name__ == "__main__":
    unittest.main()
