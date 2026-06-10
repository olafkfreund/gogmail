from textual.app import App, ComposeResult
from textual.widgets import Header, Footer, Label, ContentSwitcher, Input, Button, RichLog, Tree, Static
from textual.containers import Vertical, Horizontal, Container
from textual.binding import Binding
import asyncio
import datetime
import json
import os
import re
import subprocess

from gogmail.tui.widgets import (
    GmailTab, CalendarTab, DriveTab, DocsTab, SheetsTab,
    SlidesTab, FormsTab, MeetTab, ZoomTab, ContactsTab,
    TasksTab, ChatTab, StatusNotification
)
from gogmail.tui.screens import (
    PromptDialog, TaskCreateScreen, CalendarCreateScreen, GmailComposeScreen,
    ThemeSelectScreen, THEMES
)
from gogmail.gog_api import GogAPI, set_error_sink, set_account
from gogmail.gemini_api import GeminiAPI

DEFAULT_THEME = "gruvbox"
BADGE = "✨ Created using Claude Code"
VALID_THEMES = {key for key, _ in THEMES}


# --- Configuration ---------------------------------------------------------
def get_config_path() -> str:
    config_dir = os.path.expanduser("~/.config/gogmail")
    return os.path.join(config_dir, "settings.json")


def load_config() -> dict:
    """Load persisted settings, falling back to sensible defaults."""
    defaults = {"theme": DEFAULT_THEME, "ai_width": 40, "account": ""}
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


async def _tool_search_emails(app, params) -> str:
    query = params.get("query")
    try:
        app.query_one("#content-switcher").current = "gmail-view"
        app.title = f"Google Workspace - Gmail (Search: {query})"
        await app.query_one(GmailTab).set_query(query)
        return f"Switched to Gmail and searched for '{query}'."
    except Exception as e:
        return f"Error searching emails: {e}"


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
        "description": "Search emails",
        "params": [("query", True, "is:unread")],
        "handler": _tool_search_emails,
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
]
TOOL_BY_NAME = {t["name"]: t for t in TOOLS}


def _build_system_instruction(tools) -> str:
    lines = [
        "You are an AI assistant built into the GogMail TUI, a terminal client for Google Workspace.",
        "Help the user manage their workspace. Keep responses concise, direct, and formatted in clean "
        "markdown text. Avoid lengthy introductions or fluff.",
        "",
        "If the user asks you to perform an action, you MUST invoke the appropriate tool by outputting a "
        "single JSON code block wrapped in ```json and ```. Do not include any other text if calling a tool.",
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


async def execute_tool(app, tool_name: str, params: dict) -> str:
    tool = TOOL_BY_NAME.get(tool_name)
    if tool is None:
        return f"Unknown tool: {tool_name}"
    missing = [name for name, required, _ in tool["params"] if required and not params.get(name)]
    if missing:
        return f"Error: missing required parameter(s): {', '.join(missing)}."
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
        yield Input(placeholder="Ask Gemini anything... (e.g. 'draft a reply')", id="ai-input")

    def on_mount(self):
        log = self.query_one("#ai-chat-history")
        log.write("[bold green]Gemini Assistant Ready![/bold green]")
        log.write("I am context-aware. Type below to ask questions about your emails, documents, or tasks.")
        self.chat_history = []

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
        prompt = event.value
        if not prompt:
            return

        log = self.query_one("#ai-chat-history")
        log.write(f"\n[bold yellow]You:[/bold yellow] {prompt}")
        event.input.value = ""

        now_str = datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S%z")
        context_desc = self._gather_context()
        prompt_with_ctx = f"Context:\nCurrent local time: {now_str}\n"
        if context_desc:
            prompt_with_ctx += context_desc
        prompt_with_ctx += f"\n---\nQuestion:\n{prompt}"

        self.chat_history.append({"role": "user", "parts": [{"text": prompt_with_ctx}]})
        log.write("[italic green]Gemini is thinking...[/italic green]")

        async def run_ai():
            max_steps = 5
            for step in range(max_steps):
                response_text = await GeminiAPI.generate_chat(self.chat_history, SYSTEM_INSTRUCTION)
                self.chat_history.append({"role": "model", "parts": [{"text": response_text}]})

                json_match = re.search(r'```json\s*(.*?)\s*```', response_text, re.DOTALL)
                json_str = json_match.group(1).strip() if json_match else response_text.strip()

                tool_data = None
                try:
                    parsed = json.loads(json_str)
                    if isinstance(parsed, dict) and any(k in parsed for k in ("tool", "tool_name", "tool_code")):
                        tool_data = parsed
                except Exception:
                    pass

                if tool_data is None:
                    log.write(f"[bold green]Gemini:[/bold green] {response_text}")
                    return

                tool_name = tool_data.get("tool") or tool_data.get("tool_name") or tool_data.get("tool_code")
                params = tool_data.get("parameters") or tool_data
                log.write(f"[italic green]Executing {tool_name}...[/italic green]")
                result_msg = await execute_tool(self.app, tool_name, params)
                log.write(f"[bold green]Gemini (Tool Executed):[/bold green] {result_msg}")
                self.chat_history.append(
                    {"role": "user", "parts": [{"text": f"Tool '{tool_name}' execution result: {result_msg}"}]}
                )
                log.write("[italic green]Gemini is thinking...[/italic green]")

            log.write("[bold red]Gemini: Execution limit reached (max 5 tool calls).[/bold red]")

        asyncio.create_task(run_ai())


class GogMailApp(App):
    """Main GogMail TUI application with tree-based navigation."""
    CSS_PATH = "tui/styles.tcss"

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
        Binding("q", "quit", "Quit"),
    ]

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

        # Verify gog is installed/authenticated and resolve the real account.
        self.run_worker(self._preflight(), exclusive=False)

    def _build_sidebar(self):
        tree = self.query_one("#sidebar-tree")
        tree.show_root = False

        gmail = tree.root.add("✉ Gmail", expand=True)
        gmail.add_leaf("📥 Inbox", data={"type": "gmail", "query": "label:INBOX"})
        gmail.add_leaf("★ Starred", data={"type": "gmail", "query": "is:starred"})
        gmail.add_leaf("↗ Sent", data={"type": "gmail", "query": "is:sent"})
        gmail.add_leaf("✎ Drafts", data={"type": "gmail", "query": "is:draft"})
        gmail.add_leaf("🗑 Trash", data={"type": "gmail", "query": "is:trash"})

        tree.root.add_leaf("📅 Calendar", data={"type": "calendar"})

        drive = tree.root.add("📁 Drive", expand=True)
        drive.add_leaf("🗎 All Files", data={"type": "drive"})
        drive.add_leaf("📝 Docs", data={"type": "docs"})
        drive.add_leaf("📊 Sheets", data={"type": "sheets"})
        drive.add_leaf("⧉ Slides", data={"type": "slides"})
        drive.add_leaf("⎔ Forms", data={"type": "forms"})

        tree.root.add_leaf("📹 Meet", data={"type": "meet"})
        tree.root.add_leaf("📞 Zoom", data={"type": "zoom"})
        tree.root.add_leaf("👤 Contacts", data={"type": "contacts"})
        tree.root.add_leaf("✓ Tasks", data={"type": "tasks"})
        tree.root.add_leaf("💬 Chat", data={"type": "chat"})

        # Populated asynchronously from `gog auth list` in _preflight.
        self._accounts_node = tree.root.add("👤 Accounts", expand=True)

        settings = tree.root.add("⚙ Settings", expand=True)
        settings.add_leaf("🎨 Select Theme", data={"type": "select-theme"})

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
            marker = "● " if email == self.account else "  "
            node.add_leaf(f"{marker}{email}", data={"type": "account", "email": email})

    async def switch_account(self, email: str) -> None:
        if email == self.account:
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
            except Exception:
                pass

    async def safe_refresh(self, tab_cls, method: str) -> None:
        try:
            await getattr(self.query_one(tab_cls), method)()
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
            self.title = f"Google Workspace - Gmail ({event.node.label})"
            await self.query_one(GmailTab).set_query(data.get("query", "label:INBOX"))
        elif node_type == "select-theme":
            self.open_theme_dialog()
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
        if getattr(tab, "_loaded", False):
            return
        tab._loaded = True
        self.run_worker(getattr(tab, loader)())

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "sidebar-compose-btn":
            self.open_compose_dialog()

    def on_status_notification(self, event: StatusNotification):
        self.notify_status(event.message, error=getattr(event, "is_error", False))

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
        self._open_prompt(
            "Apply Label", "Label name (e.g. Receipts, Work)",
            lambda v: GogAPI.gmail_modify_labels(thread_id, add=v),
            "Applying label...", "Label applied.",
            lambda: self.query_one(GmailTab).refresh_emails(),
        )

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

    def open_task_create_dialog(self, tasklist_id: str):
        async def handle_dismiss(result):
            if result:
                await self._run_mutation(
                    "Creating task...",
                    GogAPI.tasks_add(tasklist_id=tasklist_id, title=result.get("title"), notes=result.get("notes")),
                    "Task created.",
                    lambda: self.query_one(TasksTab).refresh_tasks(),
                )
        self.push_screen(TaskCreateScreen(tasklist_id), handle_dismiss)

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
        """Copy text to system clipboard (wl-copy or xclip)."""
        self._clipboard_local = text
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
