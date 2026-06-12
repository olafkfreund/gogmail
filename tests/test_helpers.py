"""Unit tests for display helpers and the AI tool registry additions."""
import unittest
from datetime import datetime, timedelta

from gogmail.tui.widgets import human_size, relative_date
from gogmail.app import TOOL_BY_NAME, SYSTEM_INSTRUCTION


class TestHumanSize(unittest.TestCase):
    def test_bytes(self):
        self.assertEqual(human_size("512"), "512 B")

    def test_kilobytes(self):
        self.assertEqual(human_size(284113), "277.5 KB")

    def test_megabytes(self):
        self.assertEqual(human_size(1048576), "1 MB")

    def test_missing_or_zero(self):
        self.assertEqual(human_size(None), "—")
        self.assertEqual(human_size(""), "—")
        self.assertEqual(human_size("0"), "—")


class TestRelativeDate(unittest.TestCase):
    def test_today_shows_time(self):
        now = datetime.now()
        self.assertEqual(relative_date(now.strftime("%Y-%m-%d %H:%M")),
                         now.strftime("%H:%M"))

    def test_this_week_shows_weekday(self):
        d = datetime.now() - timedelta(days=3)
        self.assertEqual(relative_date(d.strftime("%Y-%m-%d %H:%M")),
                         d.strftime("%a %H:%M"))

    def test_unparseable_passes_through(self):
        self.assertEqual(relative_date("yesterday-ish"), "yesterday-ish")
        self.assertEqual(relative_date(""), "")


class TestNewAITools(unittest.TestCase):
    def test_tools_registered(self):
        for name in ("summarize_thread", "draft_reply"):
            self.assertIn(name, TOOL_BY_NAME)
            self.assertIn(name, SYSTEM_INSTRUCTION)

    def test_new_tools_have_no_required_params(self):
        # Both must be callable with no arguments (they act on the selection).
        for name in ("summarize_thread", "draft_reply"):
            required = [p for p, req, _ in TOOL_BY_NAME[name]["params"] if req]
            self.assertEqual(required, [])


if __name__ == "__main__":
    unittest.main()
