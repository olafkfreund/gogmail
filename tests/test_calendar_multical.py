"""Tests for the multi-calendar picker + free/busy feature.

Mirrors tests/test_gog_api.py: the only mocked seam is gog_api.run_gog, so the
real arg construction and JSON parsing are exercised without a `gog` binary.
The CalendarTab merge logic is tested by stubbing GogAPI.calendar_events.
"""
import unittest
from unittest import mock

from gogmail import gog_api
from gogmail.gog_api import GogAPI


def _fake_run_gog(result):
    async def _run(args, parse_json=True, quiet=False, stdin_data=None):
        return result
    return _run


class TestFreeBusy(unittest.IsolatedAsyncioTestCase):
    async def test_freebusy_builds_args(self):
        seen = {}

        async def fake(args, parse_json=True, quiet=False, stdin_data=None):
            seen["args"] = args
            return True, {"calendars": {"primary": {"busy": []}}}

        with mock.patch.object(gog_api, "run_gog", fake):
            await GogAPI.calendar_freebusy("primary", "2026-06-13T00:00:00Z", "2026-06-13T23:59:59Z")
        self.assertEqual(
            seen["args"],
            ["calendar", "freebusy", "--cal", "primary",
             "--from", "2026-06-13T00:00:00Z", "--to", "2026-06-13T23:59:59Z"],
        )

    async def test_freebusy_parses_busy_for_matching_key(self):
        payload = {"calendars": {"primary": {"busy": [
            {"start": "2026-06-13T09:00:00Z", "end": "2026-06-13T09:30:00Z"},
        ]}}}
        with mock.patch.object(gog_api, "run_gog", _fake_run_gog((True, payload))):
            busy = await GogAPI.calendar_freebusy("primary", "a", "b")
        self.assertEqual(len(busy), 1)
        self.assertEqual(busy[0]["start"], "2026-06-13T09:00:00Z")

    async def test_freebusy_falls_back_to_single_resolved_key(self):
        # gog often keys the result by the *resolved* id, not what we requested.
        payload = {"calendars": {"someone@example.com": {"busy": [
            {"start": "x", "end": "y"},
        ]}}}
        with mock.patch.object(gog_api, "run_gog", _fake_run_gog((True, payload))):
            busy = await GogAPI.calendar_freebusy("someone", "a", "b")
        self.assertEqual(len(busy), 1)

    async def test_freebusy_empty_when_no_busy(self):
        with mock.patch.object(gog_api, "run_gog", _fake_run_gog((True, {"calendars": {"primary": {}}}))):
            self.assertEqual(await GogAPI.calendar_freebusy("primary", "a", "b"), [])

    async def test_freebusy_empty_on_failure(self):
        with mock.patch.object(gog_api, "run_gog", _fake_run_gog((False, "boom"))):
            self.assertEqual(await GogAPI.calendar_freebusy("primary", "a", "b"), [])


class TestCalendarList(unittest.IsolatedAsyncioTestCase):
    async def test_calendar_list_parses(self):
        payload = {"calendars": [
            {"id": "primary", "summary": "Me", "primary": True},
            {"id": "team@x.com", "summary": "Team"},
        ]}
        with mock.patch.object(gog_api, "run_gog", _fake_run_gog((True, payload))):
            cals = await GogAPI.calendar_list()
        self.assertEqual([c["id"] for c in cals], ["primary", "team@x.com"])


class TestMultiCalendarMerge(unittest.IsolatedAsyncioTestCase):
    """refresh_calendar must fetch events per selected calendar and tag each
    event with its source calendar. We drive the tab's merge logic directly,
    stubbing the per-calendar events read and the tasks reads."""

    def _make_tab(self):
        from gogmail.tui.widgets import CalendarTab
        tab = CalendarTab.__new__(CalendarTab)
        tab.selected_calendar_ids = []
        tab.tasks_data = []
        tab.events_data = []
        # Neutralise UI-touching methods so refresh_calendar runs headless.
        tab.post_message = lambda *a, **k: None
        tab.render_view = lambda: None
        return tab

    async def test_default_fetches_primary_only(self):
        tab = self._make_tab()
        calls = []

        async def fake_events(calendar_id="primary", *a, **k):
            calls.append(calendar_id)
            return [{"id": f"e-{calendar_id}", "summary": "x"}]

        with mock.patch.object(GogAPI, "calendar_events", fake_events), \
             mock.patch.object(GogAPI, "tasks_lists", mock.AsyncMock(return_value=[])):
            await tab.refresh_calendar()

        self.assertEqual(calls, ["primary"])
        self.assertEqual(len(tab.events_data), 1)
        self.assertEqual(tab.events_data[0]["_calendar"], "primary")

    async def test_selected_calendars_fetched_each_and_tagged(self):
        tab = self._make_tab()
        tab.selected_calendar_ids = ["primary", "team@x.com"]
        calls = []

        async def fake_events(calendar_id="primary", *a, **k):
            calls.append(calendar_id)
            return [{"id": f"e-{calendar_id}", "summary": "x"}]

        with mock.patch.object(GogAPI, "calendar_events", fake_events), \
             mock.patch.object(GogAPI, "tasks_lists", mock.AsyncMock(return_value=[])):
            await tab.refresh_calendar()

        self.assertEqual(calls, ["primary", "team@x.com"])
        self.assertEqual(len(tab.events_data), 2)
        tags = {e["_calendar"] for e in tab.events_data}
        self.assertEqual(tags, {"primary", "team@x.com"})


if __name__ == "__main__":
    unittest.main()
