"""Tests for the Gmail attachment list/download wrappers (issue #5).

Like the rest of the suite, these mock the single `gog_api.run_gog` seam so no
real `gog` binary is needed; we assert both the args sent to gog and the parsed
return shape.
"""
import unittest
from unittest import mock

from gogmail import gog_api
from gogmail.gog_api import GogAPI


def _fake_run_gog(result):
    async def _run(args, parse_json=True, quiet=False, stdin_data=None):
        return result
    return _run


# A realistic `gog gmail thread attachments <id>` JSON payload.
_ATTACHMENTS_PAYLOAD = {
    "threadId": "T1",
    "attachments": [
        {
            "messageId": "M1",
            "filename": "Invoice.pdf",
            "size": 32202,
            "sizeHuman": "31.4 KB",
            "mimeType": "application/pdf",
            "attachmentId": "ANGjdJ8_aaa",
        },
        {
            "messageId": "M1",
            "filename": "Receipt.pdf",
            "size": 33386,
            "sizeHuman": "32.6 KB",
            "mimeType": "application/pdf",
            "attachmentId": "ANGjdJ-bbb",
        },
    ],
}


class TestGmailListAttachments(unittest.IsolatedAsyncioTestCase):
    async def test_builds_thread_attachments_args(self):
        seen = {}

        async def fake(args, parse_json=True, quiet=False, stdin_data=None):
            seen["args"] = args
            return True, _ATTACHMENTS_PAYLOAD

        with mock.patch.object(gog_api, "run_gog", fake):
            result = await GogAPI.gmail_list_attachments("T1")

        self.assertEqual(seen["args"], ["gmail", "thread", "attachments", "T1"])
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["filename"], "Invoice.pdf")
        self.assertEqual(result[0]["attachmentId"], "ANGjdJ8_aaa")
        self.assertEqual(result[1]["messageId"], "M1")

    async def test_empty_on_failure(self):
        with mock.patch.object(gog_api, "run_gog", _fake_run_gog((False, "boom"))):
            self.assertEqual(await GogAPI.gmail_list_attachments("T1"), [])

    async def test_empty_when_no_attachments_key(self):
        with mock.patch.object(gog_api, "run_gog", _fake_run_gog((True, {"threadId": "T1"}))):
            self.assertEqual(await GogAPI.gmail_list_attachments("T1"), [])


class TestGmailDownloadAttachment(unittest.IsolatedAsyncioTestCase):
    async def test_builds_attachment_args_with_out(self):
        seen = {}

        async def fake(args, parse_json=True, quiet=False, stdin_data=None):
            seen["args"] = args
            return True, {"path": "/home/me/Downloads/Invoice.pdf"}

        with mock.patch.object(gog_api, "run_gog", fake):
            ok, msg = await GogAPI.gmail_download_attachment(
                "M1", "ANGjdJ8_aaa", "/home/me/Downloads/Invoice.pdf")

        self.assertTrue(ok)
        self.assertEqual(
            seen["args"],
            ["gmail", "attachment", "M1", "ANGjdJ8_aaa", "--out", "/home/me/Downloads/Invoice.pdf"],
        )
        # Mutations coerce the result to a string (tuple[bool, str]).
        self.assertEqual(msg, '{"path": "/home/me/Downloads/Invoice.pdf"}')

    async def test_download_failure_returns_message(self):
        with mock.patch.object(gog_api, "run_gog", _fake_run_gog((False, "404 not found"))):
            ok, msg = await GogAPI.gmail_download_attachment("M1", "A1", "/tmp/x")
        self.assertFalse(ok)
        self.assertEqual(msg, "404 not found")


if __name__ == "__main__":
    unittest.main()
