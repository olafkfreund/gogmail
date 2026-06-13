"""Minimal Zoom REST client for creating instant meetings.

gogcli only manages Zoom auth (setup/doctor) — it has no meeting commands —
so meeting creation talks to the Zoom REST API directly, the same way
`gemini_api` talks to Gemini. Credentials come from the same environment
variables gogcli itself accepts:

    GOG_ZOOM_ACCOUNT_ID, GOG_ZOOM_CLIENT_ID, GOG_ZOOM_CLIENT_SECRET

The Server-to-Server OAuth app must have a `meeting:write` scope for creation
to succeed (a user-read scope alone only validates the connection).
"""
import asyncio
import logging
import os

import requests

_TOKEN_URL = "https://zoom.us/oauth/token"
_MEETINGS_URL = "https://api.zoom.us/v2/users/me/meetings"


class ZoomAPI:
    @staticmethod
    def _creds() -> tuple:
        return (
            os.environ.get("GOG_ZOOM_ACCOUNT_ID"),
            os.environ.get("GOG_ZOOM_CLIENT_ID"),
            os.environ.get("GOG_ZOOM_CLIENT_SECRET"),
        )

    @classmethod
    def _access_token(cls) -> str:
        account_id, client_id, client_secret = cls._creds()
        if not (account_id and client_id and client_secret):
            raise RuntimeError(
                "Zoom credentials not set. Export GOG_ZOOM_ACCOUNT_ID, "
                "GOG_ZOOM_CLIENT_ID and GOG_ZOOM_CLIENT_SECRET."
            )
        # Server-to-Server OAuth: Basic-auth the client id/secret, account id in
        # the query. Token lives ~1h; instant meetings are rare enough that we
        # mint a fresh one per create rather than cache it.
        resp = requests.post(
            _TOKEN_URL,
            params={"grant_type": "account_credentials", "account_id": account_id},
            auth=(client_id, client_secret),
            timeout=20,
        )
        if resp.status_code != 200:
            raise RuntimeError(f"Zoom token request failed: {resp.status_code} {resp.text[:200]}")
        token = resp.json().get("access_token")
        if not token:
            raise RuntimeError("Zoom token response had no access_token.")
        return token

    @classmethod
    def _create_meeting_sync(cls, topic: str) -> dict:
        token = cls._access_token()
        resp = requests.post(
            _MEETINGS_URL,
            headers={"Authorization": f"Bearer {token}"},
            json={"topic": topic, "type": 1},  # type 1 = instant meeting
            timeout=20,
        )
        if resp.status_code not in (200, 201):
            raise RuntimeError(f"Zoom create meeting failed: {resp.status_code} {resp.text[:300]}")
        return resp.json()

    @classmethod
    async def create_meeting(cls, topic: str = "GogMail meeting") -> tuple:
        """Create an instant meeting. Returns (success, dict|error_message).

        On success the dict has at least `join_url` (share with others) and
        `start_url` (opens the host's Zoom client to start the meeting).
        """
        try:
            data = await asyncio.to_thread(cls._create_meeting_sync, topic or "GogMail meeting")
            return True, data
        except Exception as e:
            logging.error(f"Zoom create_meeting failed: {e}")
            return False, str(e)
