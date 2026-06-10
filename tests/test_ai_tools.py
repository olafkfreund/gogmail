"""Tests for the AI tool registry and prompt generation in app.py."""
import json
import re
import unittest
from unittest import mock

from gogmail import app
from gogmail.app import TOOLS, SYSTEM_INSTRUCTION, execute_tool, VALID_THEMES
from gogmail.tui.screens import THEMES


class TestSystemInstruction(unittest.TestCase):
    def test_every_tool_named_in_prompt(self):
        for tool in TOOLS:
            self.assertIn(tool["name"], SYSTEM_INSTRUCTION)

    def test_embedded_json_blocks_are_valid(self):
        blocks = re.findall(r"```json\s*(.*?)\s*```", SYSTEM_INSTRUCTION, re.DOTALL)
        tool_objs = []
        for block in blocks:
            try:
                obj = json.loads(block)
            except json.JSONDecodeError:
                continue  # e.g. the intro sentence's literal ```json … ```
            self.assertIn("tool", obj)
            tool_objs.append(obj["tool"])
        # Exactly one valid schema block per registered tool.
        self.assertEqual(sorted(tool_objs), sorted(t["name"] for t in TOOLS))


class TestExecuteTool(unittest.IsolatedAsyncioTestCase):
    async def test_unknown_tool(self):
        self.assertIn("Unknown tool", await execute_tool(None, "nope", {}))

    async def test_missing_required_params_reported(self):
        # send_email requires to/subject/body; passing none must fail before any
        # handler runs (so app=None is safe).
        msg = await execute_tool(None, "send_email", {})
        self.assertIn("missing required parameter", msg)
        self.assertIn("to", msg)


class TestLazyLoaders(unittest.TestCase):
    def test_loaders_reference_known_views(self):
        from gogmail.app import TREE_VIEWS, TREE_LOADERS
        for node_type in TREE_LOADERS:
            self.assertIn(node_type, TREE_VIEWS)


class TestEmailBodyFallback(unittest.TestCase):
    def test_falls_back_to_plain_when_html_renders_empty(self):
        import base64
        from gogmail.tui.widgets import best_email_text
        # HTML that the TUI parser reduces to empty (just a comment/style).
        empty_html = "<html><head><style>a{color:red}</style></head><body></body></html>"
        enc = base64.urlsafe_b64encode(empty_html.encode()).decode()
        msg = {
            "message": {"payload": {"mimeType": "text/html", "body": {"data": enc}}},
            "body": "Plain text fallback content.",
        }
        self.assertEqual(best_email_text(msg), "Plain text fallback content.")

    def test_prefers_html_when_it_has_content(self):
        import base64
        from gogmail.tui.widgets import best_email_text
        html = "<html><body><p>Hello there</p></body></html>"
        enc = base64.urlsafe_b64encode(html.encode()).decode()
        msg = {"message": {"payload": {"mimeType": "text/html", "body": {"data": enc}}}, "body": "ignored"}
        self.assertIn("Hello there", best_email_text(msg))


class TestHtmlRendering(unittest.TestCase):
    def test_meta_and_link_tags_do_not_blank_the_body(self):
        # Regression: void <meta>/<link> tags used to leak ignore_depth and blank
        # the whole document.
        from gogmail.tui.widgets import format_email_body
        html = (
            '<html><head>'
            '<meta charset="utf-8">'
            '<link rel="stylesheet" href="x.css">'
            '<meta name="viewport" content="width=device-width">'
            '<style>.a{color:red}</style>'
            '</head><body><p>Visible content here</p></body></html>'
        )
        self.assertIn("Visible content here", format_email_body(html))

    def test_strip_html_to_text_fallback(self):
        from gogmail.tui.widgets import strip_html_to_text
        out = strip_html_to_text("<div>Hello <b>world</b></div><script>x()</script>")
        self.assertIn("Hello", out)
        self.assertIn("world", out)
        self.assertNotIn("x()", out)


class TestGmailThreadFallback(unittest.IsolatedAsyncioTestCase):
    async def test_falls_back_to_thread_when_get_404s(self):
        from gogmail import gog_api
        from gogmail.gog_api import GogAPI
        calls = []

        async def fake_run(args, parse_json=True, quiet=False):
            calls.append(args)
            if args[:2] == ["gmail", "get"] and args[2] == "THREAD":
                return False, "Google API error (404 notFound)"
            if args[:3] == ["gmail", "thread", "get"]:
                return True, {"thread": {"messages": [{"id": "MSG_OLD"}, {"id": "MSG_NEW"}]}}
            if args[:2] == ["gmail", "get"] and args[2] == "MSG_NEW":
                return True, {"headers": {"subject": "resolved"}, "body": "hi"}
            return False, "unexpected"

        with mock.patch.object(gog_api, "run_gog", fake_run):
            res = await GogAPI.gmail_get_message("THREAD")
        self.assertEqual(res.get("body"), "hi")
        self.assertIn(["gmail", "thread", "get", "THREAD"], calls)


class TestThemeRegistry(unittest.TestCase):
    def test_valid_themes_match_registry(self):
        self.assertEqual(VALID_THEMES, {key for key, _ in THEMES})

    def test_default_theme_is_valid(self):
        self.assertIn(app.DEFAULT_THEME, VALID_THEMES)


if __name__ == "__main__":
    unittest.main()
