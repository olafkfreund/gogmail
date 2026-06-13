from functools import partial

from textual.app import App, ComposeResult
from textual.command import DiscoveryHit, Hit, Provider
from textual.widgets import Header, Footer, Label, ContentSwitcher, Input, Button, RichLog, Tree, Static, TextArea
from textual.containers import Vertical, Horizontal, Container
from textual.binding import Binding
import asyncio
import datetime
import json
import os
import re
import subprocess

from rich.markup import escape as rich_escape

from gogmail.tui.widgets import (
    GmailTab, CalendarTab, DriveTab, DocsTab, SheetsTab,
    SlidesTab, FormsTab, MeetTab, ZoomTab, ContactsTab,
    TasksTab, ChatTab, StatusNotification
)
from gogmail.tui.screens import (
    ConfirmDialog, GmailLabelScreen, GmailAttachmentScreen, PromptDialog, SettingsScreen,
    TaskCreateScreen, CalendarCreateScreen, CalendarPickerScreen, ContactCreateScreen,
    GmailComposeScreen, ThemeSelectScreen, THEMES
)
from gogmail.gog_api import GogAPI, set_error_sink, set_account
from gogmail.gemini_api import GeminiAPI
from gogmail import voice

DEFAULT_THEME = "gruvbox"
BADGE = "Created using Claude Code"
VALID_THEMES = {key for key, _ in THEMES}


# --- Configuration ---------------------------------------------------------
def get_config_path() -> str:
    config_dir = os.path.expanduser("~/.config/gogmail")
    return os.path.join(config_dir, "settings.json")


def load_config() -> dict:
    """Load persisted settings, falling back to sensible defaults."""
    defaults = {"theme": DEFAULT_THEME, "ai_width": 40, "account": "",
                "voice_input": False, "spoken_replies": False,
                "tts_engine": "auto", "tts_voice": "Kore"}
    path = get_config_path()
    if os.path.exists(path):
        try:
            with open(path, "r") as f:
                defaults.update(json.load(f))
        except Exception:
            pass
    if defaults.get("theme") not in VALID_THEMES:
        defaults["theme"] = DEFAULT_THEME
    return defaults


def save_config(cfg: dict) -> None:
    path = get_config_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump(cfg, f, indent=2)
    except Exception:
        pass


class AISplitter(Static):
    """A vertical bar that can be dragged to resize the AI drawer."""
    def on_mount(self):
        self.dragging = False

    def on_mouse_down(self, event):
        self.dragging = True
        self.capture_mouse(True)

    def on_mouse_move(self, event):
        if self.dragging:
            screen_width = self.app.size.width
            new_width = screen_width - event.screen_x
            new_width = max(20, min(120, new_width))

            drawer = self.app.query_one("#ai-drawer")
            drawer.styles.width = new_width
            self.app.ai_width = new_width

    def on_mouse_up(self, event):
        self.dragging = False
        self.capture_mouse(False)
        self.app.save_settings()


# --- AI tool registry ------------------------------------------------------
# A single source of truth for the assistant's tools: each entry generates its
# own slice of the system prompt AND drives dispatch + argument validation, so
# the prompt the model sees can never drift from what execute_tool can run.
async def _tool_send_email(app, params) -> str:
    to = params.get("to")
    subject = params.get("subject", "No Subject")
    body = params.get("body", "")
    success, err = await GogAPI.gmail_send(to, subject, body)
    if success:
        await app.safe_refresh(GmailTab, "refresh_emails")
        return f"Successfully sent email to {to} with subject '{subject}'."
    return f"Failed to send email: {err}"


# Read tools return DATA (capped) for the model to summarize — not just status.
_MAX_ITEMS = 15
_MAX_CHARS = 4000


def _truncate(text: str, limit: int = _MAX_CHARS) -> str:
    return text if len(text) <= limit else text[:limit] + "\n…(truncated)"


async def _tool_search_emails(app, params) -> str:
    # Optional query (default to the inbox) so "show me my latest emails" can't
    # loop on a missing-parameter error. Full Gmail syntax works:
    # is:unread, newer_than:7d, label:X, from:…
    query = params.get("query") or "label:INBOX"
    try:  # also switch the visible Gmail view as a convenience
        app.query_one("#content-switcher").current = "gmail-view"
        app.title = "Google Workspace - Gmail (Search)"
        await app.query_one(GmailTab).set_query(query)
    except Exception:
        pass
    threads = await GogAPI.gmail_search(query)
    if not threads:
        return f"No emails matched '{query}'."
    rows = [f"- [{t.get('date', '')}] {t.get('from', '')[:40]} — "
            f"{t.get('subject', '(no subject)')}" for t in threads[:_MAX_ITEMS]]
    return _truncate(
        f"Emails matching '{query}' ({len(threads)} found, "
        f"showing {min(len(threads), _MAX_ITEMS)}):\n" + "\n".join(rows))


async def _tool_list_events(app, params) -> str:
    app.assistant_show("calendar-view", "refresh_calendar")
    rng = (params.get("range") or "week").lower()
    events = await GogAPI.calendar_events(
        "primary", time_range=rng, time_from=params.get("from"),
        time_to=params.get("to"), max_results=25)
    if not events:
        return f"No calendar events found for range '{rng}'."
    rows = []
    for e in events[:_MAX_ITEMS]:
        start = e.get("start", {}).get("dateTime") or e.get("start", {}).get("date", "")
        loc = f" @ {e.get('location')}" if e.get("location") else ""
        rows.append(f"- {start}: {e.get('summary', '(no title)')}{loc}")
    return _truncate(f"Calendar events ({rng}):\n" + "\n".join(rows))


async def _tool_list_tasks(app, params) -> str:
    app.assistant_show("tasks-view", "refresh_tasklists")
    tasks = await GogAPI.tasks_list("@default")
    if not tasks:
        return "No open tasks in your default list."
    rows = []
    for t in tasks[:_MAX_ITEMS]:
        status = "done" if t.get("status") == "completed" else "open"
        due = f" (due {t.get('due', '')[:10]})" if t.get("due") else ""
        rows.append(f"- [{status}] {t.get('title', '')}{due}")
    return _truncate("Tasks (default list):\n" + "\n".join(rows))


async def _tool_search_drive(app, params) -> str:
    query = params.get("query")
    if not query:
        return "Error: provide a search query (a keyword in the file name or content)."
    app.assistant_show("drive-view", "refresh_files", query)
    files = await GogAPI.drive_search(query)
    if not files:
        return f"No Drive files matched '{query}'."
    rows = [f"- {f.get('name', '')} ({f.get('mimeType', '').split('.')[-1]}) "
            f"id={f.get('id', '')}" for f in files[:_MAX_ITEMS]]
    return _truncate(f"Drive files matching '{query}':\n" + "\n".join(rows))


async def _tool_read_doc(app, params) -> str:
    doc_id = params.get("doc_id")
    if not doc_id:
        return "Error: provide doc_id (use search_drive to find it first)."
    text = await GogAPI.docs_cat(doc_id)
    if not text.strip():
        return "The document is empty or could not be read."
    return _truncate("Document text:\n" + text, 6000)


async def _tool_search_contacts(app, params) -> str:
    query = params.get("query")
    app.assistant_show("contacts-view", "refresh_contacts", query)
    contacts = await (GogAPI.contacts_search(query) if query else GogAPI.contacts_list())
    if not contacts:
        return "No contacts found." if query else "Your contact list is empty."
    rows = [f"- {GogAPI.contact_name(c) or '(no name)'} "
            f"<{GogAPI.contact_email(c) or 'no email'}>" for c in contacts[:_MAX_ITEMS]]
    return _truncate("Contacts:\n" + "\n".join(rows))


async def _tool_create_event(app, params) -> str:
    summary = params.get("summary")
    start = params.get("start")
    end = params.get("end")
    success, err = await GogAPI.calendar_create_event(
        "primary", summary, start, end,
        params.get("description", ""), params.get("location", "")
    )
    if success:
        await app.safe_refresh(CalendarTab, "refresh_calendar")
        return f"Successfully created calendar event '{summary}' from {start} to {end}."
    return f"Failed to create event: {err}"


async def _tool_create_meet(app, params) -> str:
    success, link = await GogAPI.meet_create()
    return f"Successfully created Google Meet space: {link}" if success else f"Failed to create Meet space: {link}"


async def _tool_summarize_thread(app, params) -> str:
    from gogmail.tui.widgets import best_email_text
    tab = app.query_one(GmailTab)
    thread_id = params.get("thread_id") or getattr(tab, "selected_thread_id", None)
    if not thread_id:
        return "Error: no email is selected and no thread_id was given."
    stubs = await GogAPI.gmail_thread_messages(thread_id)
    ids = [m.get("id") for m in stubs if m.get("id")][-5:] or [thread_id]
    parts = []
    for mid in ids:
        msg = await GogAPI.gmail_get_message(mid)
        if msg:
            h = msg.get("headers", {})
            parts.append(f"From: {h.get('from', '')}\nDate: {h.get('date', '')}\n"
                         f"{best_email_text(msg)[:2000]}")
    if not parts:
        return "Error: could not fetch the thread content."
    return ("Thread content (oldest first):\n\n" + "\n\n---\n\n".join(parts)
            + "\n\nNow answer the user's request about this thread (summarize it "
              "in a few bullets unless they asked for something else).")


async def _tool_draft_reply(app, params) -> str:
    from gogmail.tui.widgets import best_email_text
    tab = app.query_one(GmailTab)
    msg = getattr(tab, "selected_msg", None)
    if not msg:
        return "Error: no email is selected to reply to."
    headers = msg.get("headers", {})
    draft = await GeminiAPI.draft_reply(
        original_subject=headers.get("subject", ""),
        original_sender=headers.get("from", ""),
        original_body=best_email_text(msg),
        user_instructions=params.get("instructions", "Write an appropriate reply."),
    )
    app.open_compose_dialog(
        to=headers.get("from", ""),
        subject=f"Re: {headers.get('subject', '')}",
        body=draft,
        thread_id=msg.get("threadId"),
        reply_to_message_id=msg.get("messageId"),
    )
    return "Opened a compose window with the drafted reply for the user to review and send."


async def _tool_add_task(app, params) -> str:
    title = params.get("title")
    lists = await GogAPI.tasks_lists()
    if not lists:
        return "Error: No Google Tasks lists found."
    tasklist_id = lists[0].get("id")
    if await GogAPI.tasks_add(tasklist_id, title, params.get("notes", "")):
        await app.safe_refresh(TasksTab, "refresh_tasks")
        return f"Successfully added task '{title}' to list '{lists[0].get('title')}'."
    return "Failed to add task."


# Each param: (name, required, example). Optional params have a "" example default.
TOOLS = [
    {
        "name": "send_email",
        "description": "Send an email",
        "params": [("to", True, "recipient@example.com"), ("subject", True, "Subject"),
                   ("body", True, "Body content")],
        "handler": _tool_send_email,
    },
    {
        "name": "search_emails",
        "description": "Show/search emails and return the matching list. Use Gmail "
                       "query syntax. Omit query for the latest inbox mail.",
        "params": [("query", False, "is:unread newer_than:7d")],
        "handler": _tool_search_emails,
    },
    {
        "name": "list_events",
        "description": "Show calendar events for a time range and return the list",
        "params": [("range", False, "week"), ("from", False, "today"), ("to", False, "monday")],
        "handler": _tool_list_events,
    },
    {
        "name": "list_tasks",
        "description": "Show the open tasks in the default Google Tasks list",
        "params": [],
        "handler": _tool_list_tasks,
    },
    {
        "name": "search_drive",
        "description": "Search Google Drive by keyword and return matching files",
        "params": [("query", True, "quarterly report")],
        "handler": _tool_search_drive,
    },
    {
        "name": "read_doc",
        "description": "Read a Google Doc's text (find its id with search_drive first)",
        "params": [("doc_id", True, "1AbC…")],
        "handler": _tool_read_doc,
    },
    {
        "name": "search_contacts",
        "description": "Look up contacts by name/email (omit query to list all)",
        "params": [("query", False, "Beatriz")],
        "handler": _tool_search_contacts,
    },
    {
        "name": "create_event",
        "description": "Create a calendar event (times are RFC3339)",
        "params": [("summary", True, "Meeting Title"), ("start", True, "2026-06-11T10:00:00Z"),
                   ("end", True, "2026-06-11T11:00:00Z"), ("description", False, "Optional Description"),
                   ("location", False, "Optional Location")],
        "handler": _tool_create_event,
    },
    {
        "name": "create_meet",
        "description": "Create a Google Meet space",
        "params": [],
        "handler": _tool_create_meet,
    },
    {
        "name": "add_task",
        "description": "Add a task to the default list",
        "params": [("title", True, "Task title"), ("notes", False, "Optional notes")],
        "handler": _tool_add_task,
    },
    {
        "name": "summarize_thread",
        "description": "Fetch the full content of the selected email thread (call this before "
                       "summarizing or answering questions about the open email)",
        "params": [("thread_id", False, "")],
        "handler": _tool_summarize_thread,
    },
    {
        "name": "draft_reply",
        "description": "Draft a reply to the selected email and open it in the compose window "
                       "for the user to review",
        "params": [("instructions", False, "politely accept the invitation")],
        "handler": _tool_draft_reply,
    },
]
TOOL_BY_NAME = {t["name"]: t for t in TOOLS}


def _build_system_instruction(tools) -> str:
    lines = [
        "You are an AI assistant built into the GogMail TUI, a terminal client for Google Workspace.",
        "Help the user manage their workspace. Keep responses concise, direct, and formatted in clean "
        "markdown text. Avoid lengthy introductions or fluff.",
        "",
        "You can both READ and ACT on the user's workspace through tools. To SHOW or answer "
        "questions about emails, calendar, tasks, Drive files, docs or contacts, FIRST call the "
        "matching read tool (search_emails, list_events, list_tasks, search_drive, read_doc, "
        "search_contacts) to fetch the data, THEN summarize the result for the user. Never claim "
        "you can't see something before trying its read tool. The context gives you the current "
        "time, so resolve relative dates like 'this week' yourself.",
        "",
        "These read tools ALSO open the matching client view (Tasks, Calendar, Drive, Contacts, "
        "Gmail) populated with the results. So after one runs, do NOT re-list every item in chat — "
        "reply with a short confirmation or a one-line highlight (e.g. 'Showing your 5 tasks — 2 are "
        "due this week') and point the user to the view.",
        "",
        "To call a tool, output a SINGLE JSON code block wrapped in ```json and ``` and nothing "
        "else. When you have the data and are answering the user, reply in plain markdown with no "
        "JSON block.",
        "",
        "Tool JSON Schemas:",
    ]
    for i, tool in enumerate(tools, 1):
        obj = {"tool": tool["name"]}
        for name, _required, example in tool["params"]:
            obj[name] = example
        lines.append(f"{i}. {tool['description']}:")
        lines.append("```json")
        lines.append(json.dumps(obj, indent=2))
        lines.append("```")
    return "\n".join(lines)


SYSTEM_INSTRUCTION = _build_system_instruction(TOOLS)


async def _speak_reply(config, text: str) -> None:
    """Speak an assistant reply. 'auto' uses Gemini's natural TTS (same API key)
    and falls back to a local engine; 'system' forces the local engine."""
    engine = (config or {}).get("tts_engine", "auto")
    if engine in ("auto", "gemini") and os.environ.get("GEMINI_API_KEY"):
        voice_name = (config or {}).get("tts_voice", "Kore")
        wav = await GeminiAPI.synthesize_speech(text, voice_name)
        if wav and await asyncio.to_thread(voice.play_wav, wav):
            return
        if engine == "gemini":
            return  # explicit choice: don't fall back to the robotic engine
    await asyncio.to_thread(voice.speak, text)


def _extract_tool_call(response_text: str):
    """Pull a tool-call dict out of a model response, tolerant of fence variants.

    Tries a ```json``` (or bare ```) fenced object first, then the first
    balanced {...} block. Returns the dict only if it has a tool key, else None
    (so prose responses fall through to being shown as text)."""
    candidates = []
    m = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', response_text, re.DOTALL)
    if m:
        candidates.append(m.group(1))
    # Only treat a bare object as a call when the WHOLE reply is that object —
    # never scan mid-prose, so an email subject like {"tool":"send_email"} can't
    # be echoed into an unintended (and possibly destructive) tool dispatch.
    stripped = response_text.strip()
    if stripped.startswith("{") and stripped.endswith("}"):
        candidates.append(stripped)
    for cand in candidates:
        try:
            parsed = json.loads(cand)
        except Exception:
            continue
        if isinstance(parsed, dict) and any(k in parsed for k in ("tool", "tool_name", "tool_code")):
            return parsed
    return None


async def execute_tool(app, tool_name: str, params: dict) -> str:
    tool = TOOL_BY_NAME.get(tool_name)
    if tool is None:
        return f"Unknown tool: {tool_name}"
    missing = [(name, ex) for name, required, ex in tool["params"]
               if required and not params.get(name)]
    if missing:
        # Echo the expected example so the model can self-correct instead of
        # re-issuing the same malformed call.
        hints = "; ".join(f"'{n}' (e.g. {ex})" for n, ex in missing)
        return f"Error: missing required parameter(s): {hints}. Re-issue the call including them."
    return await tool["handler"](app, params)


# Sidebar node type -> (ContentSwitcher view id, title label).
# Gmail and select-theme are handled separately (they need extra steps).
TREE_VIEWS = {
    "calendar": ("calendar-view", "Calendar"),
    "drive": ("drive-view", "Drive"),
    "docs": ("docs-view", "Docs"),
    "sheets": ("sheets-view", "Sheets"),
    "slides": ("slides-view", "Slides"),
    "forms": ("forms-view", "Forms"),
    "meet": ("meet-view", "Meet"),
    "zoom": ("zoom-view", "Zoom"),
    "contacts": ("contacts-view", "Contacts"),
    "tasks": ("tasks-view", "Tasks"),
    "chat": ("chat-view", "Chat"),
}

# Node type -> the tab's initial-load coroutine method. Tabs load lazily on first
# view (not on mount) so unused/unconfigured services (e.g. Chat without the API
# enabled) don't fire gog calls — and error toasts — at startup. Meet/Zoom have
# no data to fetch, so they have no loader.
TREE_LOADERS = {
    "calendar": "refresh_calendar",
    "drive": "refresh_files",
    "docs": "refresh_list",
    "sheets": "refresh_list",
    "slides": "refresh_list",
    "forms": "refresh_list",
    "contacts": "refresh_contacts",
    "tasks": "refresh_tasklists",
    "chat": "refresh_spaces",
}


class AIAssistantPanel(Vertical):
    """The side panel for chatting with the Gemini AI assistant."""
    def compose(self):
        yield Label(" Gemini ", id="ai-header")
        yield RichLog(id="ai-chat-history", highlight=True, markup=True, wrap=True, min_width=0)
        yield Horizontal(
            Input(placeholder="Ask Gemini anything... (e.g. 'draft a reply')", id="ai-input"),
            Button("Talk", id="ai-mic-btn"),
            id="ai-input-row",
        )

    def on_mount(self):
        log = self.query_one("#ai-chat-history")
        log.write("[bold green]Gemini Assistant Ready![/bold green]")
        log.write("I am context-aware. Type below to ask questions about your emails, documents, or tasks.")
        self.chat_history = []
        self._recorder = voice.Recorder()
        # Hidden by default; the app calls apply_settings() once config is loaded
        # (this on_mount can run before the app sets self.config).
        self.query_one("#ai-mic-btn").display = False

    def apply_settings(self):
        """Show/hide the push-to-talk mic button per the voice_input setting."""
        try:
            self.query_one("#ai-mic-btn").display = bool(getattr(self.app, "config", {}).get("voice_input", False))
        except Exception:
            pass

    async def on_button_pressed(self, event: Button.Pressed):
        if event.button.id != "ai-mic-btn":
            return
        btn = event.button
        if not self._recorder.recording:
            if not self._recorder.start():
                self.app.notify_status(
                    "No microphone recorder found (install pw-record, arecord, ffmpeg or sox).",
                    error=True)
                return
            btn.label = "Stop"
            self.app.notify_status("Listening… click Stop when you're done speaking.")
            return
        # Stop, transcribe, then feed the transcript through the normal pipeline.
        btn.label = "Talk"
        path = await asyncio.to_thread(self._recorder.stop)
        if not path:
            self.app.notify_status("No audio captured.", error=True)
            return
        self.app.notify_status("Transcribing…")
        try:
            with open(path, "rb") as f:
                audio = f.read()
        finally:
            try:
                os.remove(path)
            except OSError:
                pass
        text = await GeminiAPI.transcribe_audio(audio)
        text = (text or "").strip()
        if not text or text.startswith(("Error", "Exception")):
            self.app.notify_status("Could not transcribe the audio.", error=True)
            return
        await self.submit_prompt(text)

    def _gather_context(self) -> str:
        """Pull a context blurb from whichever tab is currently active."""
        main_app = self.app
        active_tab_id = main_app.query_one("#content-switcher").current
        if active_tab_id == "gmail-view":
            tab = main_app.query_one(GmailTab)
            if getattr(tab, "selected_msg", None):
                headers = tab.selected_msg.get("headers", {})
                return (
                    f"Active View: Gmail\n"
                    f"Selected Email Subject: {headers.get('subject', '')}\n"
                    f"Selected Email From: {headers.get('from', '')}\n"
                    f"Selected Email Body:\n{tab.selected_msg.get('body', '')}\n"
                )
        elif active_tab_id == "docs-view":
            viewer = main_app.query_one(DocsTab).query_one("#doc-viewer")
            return f"Active View: Docs\nDocument Text:\n{viewer.text}\n"
        elif active_tab_id == "tasks-view":
            tab = main_app.query_one(TasksTab)
            if hasattr(tab, "tasks_data"):
                titles = [t.get("title") for t in tab.tasks_data]
                return f"Active View: Tasks\nCurrent Task Titles: {', '.join(titles)}\n"
        return ""

    async def on_input_submitted(self, event: Input.Submitted):
        if event.input.id != "ai-input":
            return
        if not event.value:
            return
        event.input.value = ""
        await self.submit_prompt(event.value)

    async def _speak(self, text: str) -> None:
        await _speak_reply(self.app.config, text)

    async def submit_prompt(self, prompt: str):
        """Run one assistant turn for `prompt` (typed or transcribed from voice)."""
        if not prompt:
            return

        log = self.query_one("#ai-chat-history")
        log.write(f"\n[bold yellow]You:[/bold yellow] {rich_escape(prompt)}")

        now_str = datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S%z")
        account = getattr(self.app, "account", "") or "unknown"
        try:
            active_view = self.app.query_one("#content-switcher").current
        except Exception:
            active_view = ""
        context_desc = self._gather_context()
        prompt_with_ctx = (f"Context:\nCurrent local time: {now_str}\n"
                           f"Active account: {account}\nActive view: {active_view}\n")
        if context_desc:
            prompt_with_ctx += context_desc
        prompt_with_ctx += f"\n---\nQuestion:\n{prompt}"

        self.chat_history.append({"role": "user", "parts": [{"text": prompt_with_ctx}]})
        log.write("[italic green]Gemini is thinking...[/italic green]")

        async def run_ai():
            max_steps = 8
            for step in range(max_steps):
                response_text = await GeminiAPI.generate_chat(self.chat_history, SYSTEM_INSTRUCTION)
                self.chat_history.append({"role": "model", "parts": [{"text": response_text}]})

                tool_data = _extract_tool_call(response_text)

                if tool_data is None:
                    log.write(f"[bold green]Gemini:[/bold green] {rich_escape(response_text)}")
                    if self.app.config.get("spoken_replies"):
                        await self._speak(response_text)
                    return

                tool_name = tool_data.get("tool") or tool_data.get("tool_name") or tool_data.get("tool_code")
                params = tool_data.get("parameters") or tool_data
                if not tool_name:
                    log.write(f"[bold green]Gemini:[/bold green] {rich_escape(response_text)}")
                    return
                # Escape tool_name too: it comes from the model and could carry markup.
                log.write(f"[italic green]Executing {rich_escape(str(tool_name))}...[/italic green]")
                result_msg = await execute_tool(self.app, tool_name, params)
                # Show only a one-line confirmation in the chat — the full result
                # goes to the model (below) and the data itself is shown in the
                # relevant client view, so we don't dump it into the panel.
                summary_line = (result_msg.splitlines() or [""])[0][:80]
                log.write(f"[dim green]✓ {rich_escape(str(tool_name))}: {rich_escape(summary_line)}[/dim green]")
                # Cap what re-enters the history (display is already capped) and keep
                # a sliding window so long sessions can't outgrow the context window.
                self.chat_history.append(
                    {"role": "user", "parts": [{"text": f"Tool '{tool_name}' result: {result_msg[:3000]}"}]}
                )
                if len(self.chat_history) > 24:
                    self.chat_history = self.chat_history[-24:]
                log.write("[italic green]Gemini is thinking...[/italic green]")

            log.write(f"[bold red]Gemini: Execution limit reached (max {max_steps} tool calls).[/bold red]")

        async def run_ai_safely():
            # An unhandled exception in a bare create_task is silently dropped,
            # leaving "thinking..." on screen forever — surface it instead.
            try:
                await run_ai()
            except Exception as e:
                log.write(f"[bold red]Gemini error: {rich_escape(str(e))}[/bold red]")

        self._ai_task = asyncio.create_task(run_ai_safely())


class GogMailCommands(Provider):
    """ctrl+p command palette entries for every GogMail action and view."""

    def _commands(self):
        app = self.app
        goto = lambda nt: partial(app.run_worker, app.goto_view(nt))
        yield from (
            ("Compose email", app.open_compose_dialog, "Write a new email"),
            ("New calendar event", app.open_calendar_create_dialog, "Create a calendar event"),
            ("New Google Doc", app.open_doc_create_dialog, "Create a document"),
            ("New Google Sheet", app.open_sheet_create_dialog, "Create a spreadsheet"),
            ("New Google Slides", app.open_slide_create_dialog, "Create a presentation"),
            ("New Google Form", app.open_form_create_dialog, "Create a form"),
            ("New Drive folder", app.open_drive_mkdir_dialog, "Create a folder in Drive"),
            ("Upload file to Drive", app.open_drive_upload_dialog, "Upload a local file"),
            ("Settings", app.open_settings_dialog, "Voice, speech and preferences"),
            ("Select theme", app.open_theme_dialog, "Switch the color theme"),
            ("Toggle sidebar", app.action_toggle_sidebar, "Show/hide the sidebar (F2)"),
            ("Toggle AI panel", app.action_toggle_ai, "Show/hide the Gemini drawer (F3)"),
        )
        yield ("Go to Gmail", goto("gmail"), "Open the Gmail inbox")
        for node_type, (_view_id, label) in TREE_VIEWS.items():
            yield (f"Go to {label}", goto(node_type), f"Open the {label} view")

    async def search(self, query: str):
        matcher = self.matcher(query)
        for name, callback, help_text in self._commands():
            score = matcher.match(name)
            if score > 0:
                yield Hit(score, matcher.highlight(name), callback, help=help_text)

    async def discover(self):
        for name, callback, help_text in self._commands():
            yield DiscoveryHit(name, callback, help=help_text)


class GogMailApp(App):
    """Main GogMail TUI application with tree-based navigation."""
    CSS_PATH = "tui/styles.tcss"
    COMMANDS = App.COMMANDS | {GogMailCommands}

    BINDINGS = [
        # F2 is the tmux-safe sidebar toggle (ctrl+b is the default tmux prefix
        # and never reaches the app inside tmux).
        Binding("f2", "toggle_sidebar", "Toggle Sidebar"),
        Binding("ctrl+b", "toggle_sidebar", "Toggle Sidebar", show=False),
        Binding("f3", "toggle_ai", "Toggle AI"),
        Binding("alt+a", "toggle_ai", "Toggle AI Panel", show=False),
        Binding("alt+left", "resize_ai('decrease')", "AI Width -"),
        Binding("alt+right", "resize_ai('increase')", "AI Width +"),
        Binding("alt+h", "resize_ai('decrease')", "AI Width -", show=False),
        Binding("alt+l", "resize_ai('increase')", "AI Width +", show=False),
        # Mail-client muscle memory. Plain letters never fire while an Input
        # or TextArea is focused (the widget consumes the key first).
        Binding("c", "compose", "Compose"),
        Binding("slash", "focus_search", "Search", show=False),
        # ctrl+v pastes the system clipboard into the focused field (terminal
        # bracketed paste also works; this covers tmux/remote sessions where it
        # doesn't reach the app). Copy: select text with the mouse, ctrl+c.
        Binding("ctrl+v", "paste_clipboard", "Paste", show=False, priority=True),
        Binding("q", "quit", "Quit"),
    ]

    # Active view -> its search input (for the `/` binding).
    SEARCH_INPUTS = {
        "gmail-view": "#email-search-input",
        "drive-view": "#drive-search-input",
        "contacts-view": "#contacts-search-input",
    }

    def compose(self) -> ComposeResult:
        yield Header()
        with Container(id="app-layout-with-ai"):
            with Vertical(id="sidebar"):
                yield Label("Google Workspace", id="sidebar-header")
                yield Button("Compose", variant="success", id="sidebar-compose-btn")
                yield Tree("Workspace", id="sidebar-tree")
                yield Label(BADGE, id="sidebar-badge")

            with ContentSwitcher(id="content-switcher", initial="gmail-view"):
                yield GmailTab(id="gmail-view")
                yield CalendarTab(id="calendar-view")
                yield DriveTab(id="drive-view")
                yield DocsTab(id="docs-view")
                yield SheetsTab(id="sheets-view")
                yield SlidesTab(id="slides-view")
                yield FormsTab(id="forms-view")
                yield MeetTab(id="meet-view")
                yield ZoomTab(id="zoom-view")
                yield ContactsTab(id="contacts-view")
                yield TasksTab(id="tasks-view")
                yield ChatTab(id="chat-view")

            yield AISplitter(id="ai-splitter")
            yield AIAssistantPanel(id="ai-drawer")

        yield Footer()

    def on_mount(self):
        self.title = "Google Workspace"
        self._clipboard_local = ""
        self._temp_files = []
        self._seen_errors = set()
        self.account = ""

        cfg = load_config()
        self.config = cfg
        self.theme_name = cfg["theme"]
        self.classes = f"theme-{self.theme_name}"

        self.sidebar_visible = True
        self.ai_visible = True
        self.ai_width = cfg.get("ai_width", 40)

        self.notify_status(f"Theme: {self.theme_name}")

        # Apply a provisional active account (persisted or $GOG_ACCOUNT) so even
        # the first calls are scoped; _preflight reconciles it against the real list.
        self.account = cfg.get("account") or os.environ.get("GOG_ACCOUNT", "")
        set_account(self.account)

        # Surface every gog failure to the user instead of letting it look like empty data.
        set_error_sink(self._on_gog_error)

        self._build_sidebar()

        # Apply voice/speech prefs now that config is loaded (the AI panel's own
        # on_mount can run before this, so it defers the mic toggle to here).
        try:
            self.query_one(AIAssistantPanel).apply_settings()
        except Exception:
            pass

        # Verify gog is installed/authenticated and resolve the real account.
        self.run_worker(self._preflight(), exclusive=False)

    def _build_sidebar(self):
        tree = self.query_one("#sidebar-tree")
        tree.show_root = False

        # Single-width geometric markers only: emoji render at inconsistent
        # widths across terminals/fonts and make the sidebar look ragged.
        gmail = tree.root.add("▪ Gmail", expand=True)
        gmail.add_leaf("• Inbox", data={"type": "gmail", "query": "label:INBOX"})
        gmail.add_leaf("• Starred", data={"type": "gmail", "query": "is:starred"})
        gmail.add_leaf("• Sent", data={"type": "gmail", "query": "is:sent"})
        gmail.add_leaf("• Drafts", data={"type": "gmail", "query": "is:draft"})
        gmail.add_leaf("• Trash", data={"type": "gmail", "query": "is:trash"})

        tree.root.add_leaf("▪ Calendar", data={"type": "calendar"})

        drive = tree.root.add("▪ Drive", expand=True)
        drive.add_leaf("• All Files", data={"type": "drive"})
        drive.add_leaf("• Docs", data={"type": "docs"})
        drive.add_leaf("• Sheets", data={"type": "sheets"})
        drive.add_leaf("• Slides", data={"type": "slides"})
        drive.add_leaf("• Forms", data={"type": "forms"})

        tree.root.add_leaf("▪ Meet", data={"type": "meet"})
        tree.root.add_leaf("▪ Zoom", data={"type": "zoom"})
        tree.root.add_leaf("▪ Contacts", data={"type": "contacts"})
        tree.root.add_leaf("▪ Tasks", data={"type": "tasks"})
        tree.root.add_leaf("▪ Chat", data={"type": "chat"})

        # Populated asynchronously from `gog auth list` in _preflight.
        self._accounts_node = tree.root.add("▪ Accounts", expand=True)

        settings = tree.root.add("▪ Settings", expand=True)
        settings.add_leaf("• Preferences", data={"type": "settings"})
        settings.add_leaf("• Select Theme", data={"type": "select-theme"})

        tree.select_node(gmail.children[0])

    # --- Identity / status -------------------------------------------------
    @property
    def account_label(self) -> str:
        return self.account or "GogMail"

    def notify_status(self, message: str, error: bool = False) -> None:
        """Single status surface: updates the subtitle and toasts on error."""
        self.sub_title = f"{self.account_label} | {message}"
        if error:
            try:
                self.notify(message, severity="error", timeout=12)
            except Exception:
                pass

    def _on_gog_error(self, message: str) -> None:
        # Surface each distinct error once as a toast; repeats only update the
        # status line so optional features (e.g. Chat without the API enabled)
        # don't spam toasts on every refresh.
        first_time = message not in self._seen_errors
        self._seen_errors.add(message)
        self.notify_status(message, error=first_time)

    async def _preflight(self) -> None:
        ok, info = await GogAPI.preflight()
        if not ok:
            self.account = ""
            self.notify_status(info, error=True)
            return

        # Resolve the account list and reconcile the active account.
        accounts = await GogAPI.list_accounts()
        self.accounts = accounts
        if accounts and self.account not in accounts:
            self.account = info if info in accounts else accounts[0]
        elif not self.account:
            self.account = info or (accounts[0] if accounts else "")
        set_account(self.account)
        self._populate_accounts(accounts)
        self.notify_status("Connected" + (f" as {self.account}" if self.account else ""))

    def _populate_accounts(self, accounts: list) -> None:
        node = getattr(self, "_accounts_node", None)
        if node is None:
            return
        node.remove_children()
        for email in accounts:
            marker = "● " if email == self.account else "○ "
            # Ellipsize: the 26-col sidebar wraps long addresses awkwardly.
            shown = email if len(email) <= 20 else email[:19] + "…"
            node.add_leaf(f"{marker}{shown}", data={"type": "account", "email": email})

    async def switch_account(self, email: str) -> None:
        if not email or email == self.account:
            return
        self.account = email
        set_account(email)
        self.config["account"] = email
        self.save_settings()
        self._populate_accounts(getattr(self, "accounts", [email]))
        # Force every tab to reload under the new account on next view.
        self._reset_tab_loads()
        self.notify_status(f"Switched to {email}")
        # Reload the Gmail inbox immediately (it's the default visible tab).
        try:
            await self.query_one(GmailTab).refresh_emails()
        except Exception:
            pass

    def _reset_tab_loads(self) -> None:
        for view_id, _label in TREE_VIEWS.values():
            try:
                self.query_one(f"#{view_id}")._loaded = False
            except Exception:
                pass

    def register_temp_file(self, path: str) -> None:
        """Track a temp file (e.g. exported email HTML) for cleanup on exit."""
        self._temp_files.append(path)

    def on_unmount(self) -> None:
        for path in getattr(self, "_temp_files", []):
            try:
                os.remove(path)
                # Drive previews live in private gogmail-* dirs; remove those too.
                parent = os.path.dirname(path)
                if os.path.basename(parent).startswith("gogmail-"):
                    os.rmdir(parent)
            except Exception:
                pass

    async def safe_refresh(self, tab_cls, method: str) -> None:
        try:
            await getattr(self.query_one(tab_cls), method)()
        except Exception:
            pass

    def assistant_show(self, view_id: str, loader: str = None, *loader_args) -> None:
        """Used by read-tools: switch the client to the relevant tab and refresh
        it, so the assistant's results also appear in the proper window (not just
        the chat panel). Best-effort; never raises into the tool handler."""
        try:
            switcher = self.query_one("#content-switcher")
            switcher.current = view_id
            label = next((lbl for vid, lbl in TREE_VIEWS.values() if vid == view_id), None)
            if label:
                self.title = f"Google Workspace - {label}"
            if loader:
                tab = self.query_one(f"#{view_id}")
                tab._loaded = True
                self.run_worker(getattr(tab, loader)(*loader_args))
        except Exception:
            pass

    async def on_tree_node_selected(self, event: Tree.NodeSelected) -> None:
        data = event.node.data
        if not data:
            return
        switcher = self.query_one("#content-switcher")
        node_type = data.get("type")

        if node_type == "gmail":
            switcher.current = "gmail-view"
            self.title = f"Google Workspace - Gmail ({str(event.node.label).lstrip('• ')})"
            await self.query_one(GmailTab).set_query(data.get("query", "label:INBOX"))
        elif node_type == "select-theme":
            self.open_theme_dialog()
        elif node_type == "settings":
            self.open_settings_dialog()
        elif node_type == "account":
            await self.switch_account(data.get("email"))
        elif node_type in TREE_VIEWS:
            view_id, label = TREE_VIEWS[node_type]
            switcher.current = view_id
            self.title = f"Google Workspace - {label}"
            await self._ensure_tab_loaded(node_type, view_id)

    async def _ensure_tab_loaded(self, node_type: str, view_id: str) -> None:
        """Run a tab's initial fetch the first time it is viewed (once)."""
        loader = TREE_LOADERS.get(node_type)
        if not loader:
            return
        tab = self.query_one(f"#{view_id}")
        if getattr(tab, "_loaded", False) or getattr(tab, "_loading", False):
            return
        tab._loading = True

        async def load():
            # Mark loaded only after success: a failed/cancelled first load must
            # not leave the tab permanently empty.
            try:
                await getattr(tab, loader)()
                tab._loaded = True
            finally:
                tab._loading = False

        self.run_worker(load())

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "sidebar-compose-btn":
            self.open_compose_dialog()

    def on_status_notification(self, event: StatusNotification):
        self.notify_status(event.message, error=getattr(event, "is_error", False))

    async def goto_view(self, node_type: str) -> None:
        """Navigate to a view by node type (used by the command palette)."""
        switcher = self.query_one("#content-switcher")
        if node_type == "gmail":
            switcher.current = "gmail-view"
            self.title = "Google Workspace - Gmail (Inbox)"
            await self.query_one(GmailTab).set_query("label:INBOX")
            return
        view_id, label = TREE_VIEWS[node_type]
        switcher.current = view_id
        self.title = f"Google Workspace - {label}"
        await self._ensure_tab_loaded(node_type, view_id)

    def action_compose(self):
        self.open_compose_dialog()

    def action_paste_clipboard(self):
        """Insert the system clipboard into the focused Input/TextArea."""
        text = self.clipboard
        if not text:
            return
        focused = self.focused
        if isinstance(focused, Input):
            focused.insert_text_at_cursor(text)
        elif isinstance(focused, TextArea):
            focused.insert(text)

    def action_focus_search(self):
        selector = self.SEARCH_INPUTS.get(self.query_one("#content-switcher").current)
        if selector:
            self.query_one(selector).focus()

    def action_toggle_sidebar(self):
        self.sidebar_visible = not self.sidebar_visible
        self.query_one("#sidebar").display = self.sidebar_visible

    def action_toggle_ai(self):
        self.ai_visible = not self.ai_visible
        self.query_one("#ai-drawer").display = self.ai_visible
        self.query_one("#ai-splitter").display = self.ai_visible

    def action_resize_ai(self, direction: str) -> None:
        if direction == "increase":
            self.ai_width = min(120, self.ai_width + 5)
        else:
            self.ai_width = max(20, self.ai_width - 5)
        self.query_one("#ai-drawer").styles.width = self.ai_width
        self.save_settings()

    def save_settings(self) -> None:
        self.config.update({"theme": self.theme_name, "ai_width": self.ai_width, "account": self.account})
        save_config(self.config)

    def confirm(self, message: str, on_confirm, confirm_label: str = "Delete") -> None:
        """Ask before a destructive action; runs the async on_confirm if accepted."""
        async def handle(result):
            if result:
                await on_confirm()
        self.push_screen(ConfirmDialog(message, confirm_label), handle)

    # --- Generic dialog dispatch ------------------------------------------
    async def _run_mutation(self, working_msg, coro, success_msg, refresh=None) -> bool:
        """Run one create/mutate coroutine with consistent status + refresh.

        Accepts coroutines returning either bool or (success, error) tuples.
        """
        self.notify_status(working_msg)
        result = await coro
        success, err = result if isinstance(result, tuple) else (bool(result), "")
        if success:
            self.notify_status(success_msg)
            if refresh is not None:
                await refresh()
        else:
            self.notify_status(f"Failed: {err}" if err else "Failed.", error=True)
        return success

    def _open_prompt(self, title, placeholder, action, working_msg, success_msg, refresh=None, default=""):
        """Push a single-input PromptDialog wired to a mutation + optional refresh."""
        async def handle(value):
            if value:
                await self._run_mutation(working_msg, action(value), success_msg, refresh)
        self.push_screen(PromptDialog(title, placeholder, default), handle)

    # --- Screen Modal Openers ---
    def open_compose_dialog(self, to="", subject="", body="", thread_id=None, reply_to_message_id=None):
        async def handle_dismiss(result):
            if not result:
                return
            if result.get("action") == "send":
                await self._run_mutation(
                    "Sending email...",
                    GogAPI.gmail_send(
                        to=result.get("to"), subject=result.get("subject"), body=result.get("body"),
                        thread_id=result.get("thread_id"), reply_to_message_id=result.get("reply_to_message_id"),
                    ),
                    "Email sent successfully.",
                    lambda: self.query_one(GmailTab).refresh_emails(),
                )
            elif result.get("action") == "draft":
                await self._run_mutation(
                    "Saving draft...",
                    GogAPI.gmail_create_draft(
                        to=result.get("to") or "", subject=result.get("subject") or "",
                        body=result.get("body") or "",
                    ),
                    "Draft saved.",
                )
        self.push_screen(GmailComposeScreen(to, subject, body, thread_id, reply_to_message_id), handle_dismiss)

    def open_gmail_label_dialog(self, thread_id: str):
        async def load_and_show():
            labels = await GogAPI.gmail_labels_list()
            names = [l.get("name") for l in labels
                     if l.get("type") == "user" and l.get("name")]

            def handle(result):
                if not result:
                    return
                self.run_worker(self._apply_label(thread_id, result))

            self.push_screen(GmailLabelScreen(names), handle)

        self.run_worker(load_and_show())

    async def _apply_label(self, thread_id: str, result: dict) -> None:
        name = result.get("label")
        if not name:
            return
        moving = result.get("move")
        verb = "Moving" if moving else "Applying label"
        self.notify_status(f"{verb}…")
        if result.get("create"):
            ok, err = await GogAPI.gmail_labels_create(name)
            if not ok:
                self.notify_status(f"Failed to create label: {err}", error=True)
                return
        ok = await GogAPI.gmail_modify_labels(
            thread_id, add=name, remove="INBOX" if moving else "")
        if not ok:
            self.notify_status("Failed to update labels.", error=True)
            return
        self.notify_status(f"Moved to {name}." if moving else f"Labeled {name}.")
        gmail = self.query_one(GmailTab)
        # A moved conversation has left the inbox — go back to the list.
        if moving:
            try:
                gmail.query_one("#gmail-switcher").current = "gmail-list-view"
            except Exception:
                pass
        await gmail.refresh_emails()

    def open_gmail_attachments_dialog(self, thread_id: str):
        """List a thread's attachments in a picker, then download the chosen one."""
        async def load_and_show():
            self.notify_status("Listing attachments…")
            attachments = await GogAPI.gmail_list_attachments(thread_id)
            if not attachments:
                self.notify_status("This email has no attachments.")
                return

            def handle(result):
                if not result:
                    return
                att = result["attachment"]
                dest_dir = os.path.expanduser(result["dest_dir"])
                filename = att.get("filename") or att.get("attachmentId") or "attachment"
                dest = os.path.join(dest_dir, filename)
                self.run_worker(self._run_mutation(
                    f"Downloading {filename}…",
                    GogAPI.gmail_download_attachment(
                        att.get("messageId"), att.get("attachmentId"), dest),
                    f"Saved {filename} to {dest}.",
                ))

            self.push_screen(GmailAttachmentScreen(attachments), handle)

        self.run_worker(load_and_show())

    def open_calendar_create_dialog(self):
        async def handle_dismiss(result):
            if result:
                await self._run_mutation(
                    "Creating calendar event...",
                    GogAPI.calendar_create_event(
                        calendar_id="primary", summary=result.get("summary"),
                        start_time=result.get("start"), end_time=result.get("end"),
                        description=result.get("description"), location=result.get("location"),
                    ),
                    "Event created.",
                    lambda: self.query_one(CalendarTab).refresh_calendar(),
                )
        self.push_screen(CalendarCreateScreen(), handle_dismiss)

    def open_calendar_edit_dialog(self, ev: dict):
        start, end = ev.get("start", {}), ev.get("end", {})
        prefill = {
            "summary": ev.get("summary", ""),
            "start": start.get("dateTime") or start.get("date") or "",
            "end": end.get("dateTime") or end.get("date") or "",
            "description": ev.get("description", ""),
            "location": ev.get("location", ""),
        }
        event_id = ev.get("id")

        async def handle_dismiss(result):
            if result:
                await self._run_mutation(
                    "Updating event...",
                    GogAPI.calendar_update_event(
                        "primary", event_id, summary=result.get("summary"),
                        start_time=result.get("start"), end_time=result.get("end"),
                        description=result.get("description"), location=result.get("location")),
                    "Event updated.",
                    lambda: self.query_one(CalendarTab).refresh_calendar(),
                )
        self.push_screen(CalendarCreateScreen(prefill=prefill), handle_dismiss)

    def open_calendar_picker_dialog(self, tab):
        """Show the calendar picker for the Calendar tab; persist the choice on
        the tab and reload its events when applied."""
        async def load_and_show():
            calendars = await GogAPI.calendar_list()
            # Cache id -> display name on the tab so the detail panel can label events.
            tab.calendar_names = {
                c.get("id", ""): (c.get("summary") or c.get("summaryOverride") or c.get("id", ""))
                for c in calendars if c.get("id")
            }

            def handle(result):
                if result is None:  # cancelled
                    return
                tab.selected_calendar_ids = result
                self.run_worker(tab.refresh_calendar())

            self.push_screen(CalendarPickerScreen(calendars, tab.selected_calendar_ids), handle)

        self.run_worker(load_and_show())

    def open_freebusy_dialog(self, tab):
        """Prompt for a calendar id / email and a day, then show busy intervals
        for that day in the Calendar tab's detail panel."""
        def ask_day(who):
            who = (who or "").strip() or "primary"
            today = datetime.date.today().isoformat()

            def handle_day(day):
                day = (day or "").strip()
                if not day:
                    return
                self.run_worker(self._run_freebusy(tab, who, day))

            self.push_screen(
                PromptDialog("Free/Busy: which day? (YYYY-MM-DD)", "2026-06-13", today),
                handle_day,
            )

        self.push_screen(
            PromptDialog("Free/Busy: calendar id or email", "primary or someone@example.com", "primary"),
            ask_day,
        )

    async def _run_freebusy(self, tab, who, day):
        # Query the whole calendar day in UTC. gog requires RFC3339 from/to.
        time_from = f"{day}T00:00:00Z"
        time_to = f"{day}T23:59:59Z"
        self.notify_status(f"Querying free/busy for {who}…")
        busy = await GogAPI.calendar_freebusy(who, time_from, time_to)
        tab.show_freebusy(who, day, busy)
        self.notify_status(f"Free/busy: {len(busy)} busy interval(s) for {who} on {day}.")

    def open_contact_create_dialog(self):
        async def handle_dismiss(result):
            if result and result.get("name"):
                await self._run_mutation(
                    "Creating contact...",
                    GogAPI.contacts_create(
                        name=result.get("name"),
                        email=result.get("email") or None,
                        phone=result.get("phone") or None,
                    ),
                    "Contact created.",
                    lambda: self.query_one(ContactsTab).refresh_contacts(),
                )
        self.push_screen(ContactCreateScreen(), handle_dismiss)

    def open_contact_edit_dialog(self, contact: dict):
        resource = contact.get("resource") or contact.get("resourceName") or ""
        prefill = {
            "name": GogAPI.contact_name(contact),
            "email": GogAPI.contact_email(contact),
            "phone": ContactsTab._contact_phone(contact),
        }

        async def handle_dismiss(result):
            if result:
                await self._run_mutation(
                    "Updating contact...",
                    GogAPI.contacts_update(
                        resource,
                        name=result.get("name"),
                        email=result.get("email", ""),
                        phone=result.get("phone", ""),
                    ),
                    "Contact updated.",
                    lambda: self.query_one(ContactsTab).refresh_contacts(),
                )
        self.push_screen(ContactCreateScreen(prefill=prefill), handle_dismiss)

    def open_task_create_dialog(self, tasklist_id: str):
        async def handle_dismiss(result):
            if result:
                await self._run_mutation(
                    "Creating task...",
                    GogAPI.tasks_add(tasklist_id=tasklist_id, title=result.get("title"),
                                     notes=result.get("notes"), due=result.get("due") or ""),
                    "Task created.",
                    lambda: self.query_one(TasksTab).refresh_tasks(),
                )
        self.push_screen(TaskCreateScreen(tasklist_id), handle_dismiss)

    def open_task_edit_dialog(self, tasklist_id: str, task: dict):
        prefill = {
            "title": task.get("title", ""),
            "notes": task.get("notes", ""),
            # Google Tasks stores due as an RFC3339 timestamp; show just the date.
            "due": (task.get("due") or "")[:10],
        }
        task_id = task.get("id")

        async def handle_dismiss(result):
            if result:
                await self._run_mutation(
                    "Updating task...",
                    GogAPI.tasks_edit(tasklist_id, task_id, title=result.get("title"),
                                      notes=result.get("notes"), due=result.get("due") or ""),
                    "Task updated.",
                    lambda: self.query_one(TasksTab).refresh_tasks(),
                )
        self.push_screen(TaskCreateScreen(tasklist_id, prefill=prefill), handle_dismiss)

    def open_tasklist_create_dialog(self):
        self._open_prompt(
            "Create New Task List", "Enter task list title",
            lambda v: GogAPI.tasks_lists_create(v),
            "Creating task list...", "Task list created.",
            lambda: self.query_one(TasksTab).refresh_tasklists(),
        )

    def open_drive_mkdir_dialog(self):
        self._open_prompt(
            "Create New Folder", "Enter folder name",
            lambda v: GogAPI.drive_mkdir(v),
            "Creating folder...", "Folder created.",
            lambda: self.query_one(DriveTab).refresh_files(),
        )

    def open_drive_upload_dialog(self):
        self._open_prompt(
            "Upload Local File", "Enter absolute local file path",
            lambda v: GogAPI.drive_upload(v),
            "Uploading...", "Upload complete.",
            lambda: self.query_one(DriveTab).refresh_files(),
        )

    def open_drive_download_dialog(self, file_id: str, file_name: str):
        self._open_prompt(
            "Download File", "Enter download path",
            lambda v: GogAPI.drive_download(file_id, v),
            "Downloading...", "Download complete.",
            default=f"/tmp/{file_name}",
        )

    def open_drive_share_dialog(self, file_id, file_name):
        self._open_prompt(
            f"Share “{file_name}”", "Recipient email (shared as viewer)",
            lambda v: GogAPI.drive_share(file_id, v, role="reader", notify=True),
            "Sharing...", "Shared.",
            lambda: self.query_one(DriveTab).refresh_files(),
        )

    def open_drive_rename_dialog(self, file_id, file_name):
        self._open_prompt(
            "Rename file", "New name",
            lambda v: GogAPI.drive_rename(file_id, v),
            "Renaming...", "Renamed.",
            lambda: self.query_one(DriveTab).refresh_files(),
            default=file_name or "",
        )

    def open_drive_move_dialog(self, file_id, file_name):
        self._open_prompt(
            f"Move “{file_name}”", "Destination folder ID",
            lambda v: GogAPI.drive_move(file_id, v),
            "Moving...", "Moved.",
            lambda: self.query_one(DriveTab).refresh_files(),
        )

    def open_doc_create_dialog(self):
        self._open_prompt(
            "Create Google Doc", "Enter document title",
            lambda v: GogAPI.docs_create(v),
            "Creating Google Doc...", "Doc created.",
            lambda: self.query_one(DocsTab).refresh_list(),
        )

    def open_sheet_create_dialog(self):
        self._open_prompt(
            "Create Google Sheet", "Enter spreadsheet title",
            lambda v: GogAPI.sheets_create(v),
            "Creating spreadsheet...", "Spreadsheet created.",
            lambda: self.query_one(SheetsTab).refresh_list(),
        )

    def open_slide_create_dialog(self):
        self._open_prompt(
            "Create Google Slides", "Enter presentation title",
            lambda v: GogAPI.slides_create(v),
            "Creating presentation...", "Presentation created.",
            lambda: self.query_one(SlidesTab).refresh_list(),
        )

    def open_form_create_dialog(self):
        self._open_prompt(
            "Create Google Form", "Enter form title",
            lambda v: GogAPI.forms_create(v),
            "Creating form...", "Form created.",
            lambda: self.query_one(FormsTab).refresh_list(),
        )

    def open_settings_dialog(self):
        def handle_dismiss(result):
            if not result:
                return
            self.config.update(result)
            self.save_settings()
            # Apply immediately: show/hide the mic button per the new setting.
            try:
                self.query_one(AIAssistantPanel).apply_settings()
            except Exception:
                pass
            self.notify_status("Settings saved.")
        self.push_screen(SettingsScreen(self.config), handle_dismiss)

    def open_theme_dialog(self):
        async def handle_dismiss(result):
            if result and result in VALID_THEMES:
                self.classes = f"theme-{result}"
                self.theme_name = result
                self.save_settings()
                self.notify_status(f"Theme set to {result}")
        self.push_screen(ThemeSelectScreen(), handle_dismiss)

    # --- System Clipboard Integration ---
    @property
    def clipboard(self) -> str:
        """Get text from system clipboard (wl-paste or xclip)."""
        if "WAYLAND_DISPLAY" in os.environ:
            try:
                res = subprocess.run(["wl-paste", "--no-newline"], capture_output=True, check=True)
                return res.stdout.decode('utf-8', errors='ignore')
            except Exception:
                pass
        try:
            res = subprocess.run(["xclip", "-selection", "clipboard", "-o"], capture_output=True, check=True)
            return res.stdout.decode('utf-8', errors='ignore')
        except Exception:
            pass
        try:
            res = subprocess.run(["xsel", "--clipboard", "--output"], capture_output=True, check=True)
            return res.stdout.decode('utf-8', errors='ignore')
        except Exception:
            pass
        return getattr(self, "_clipboard_local", "")

    def copy_to_clipboard(self, text: str) -> None:
        """Copy text to system clipboard (OSC 52 + wl-copy/xclip/xsel).

        OSC 52 reaches the terminal even over SSH and inside tmux (with
        `set-clipboard on`); the subprocess copiers cover local sessions.
        """
        self._clipboard_local = text
        try:
            super().copy_to_clipboard(text)
        except Exception:
            pass
        if "WAYLAND_DISPLAY" in os.environ:
            try:
                subprocess.run(["wl-copy"], input=text.encode('utf-8'), check=True)
                return
            except Exception:
                pass
        try:
            subprocess.run(["xclip", "-selection", "clipboard"], input=text.encode('utf-8'), check=True)
            return
        except Exception:
            pass
        try:
            subprocess.run(["xsel", "--clipboard", "--input"], input=text.encode('utf-8'), check=True)
            return
        except Exception:
            pass


def main():
    app = GogMailApp()
    app.run()


if __name__ == "__main__":
    main()
