from textual.screen import ModalScreen
from textual.widgets import Input, Button, Label, TextArea, Checkbox, OptionList
from textual.containers import Vertical, Horizontal
from textual.suggester import Suggester
from gogmail.gemini_api import GeminiAPI
from gogmail.gog_api import GogAPI


class ContactSuggester(Suggester):
    """Case-insensitive prefix autocomplete for recipient addresses.

    (Textual's SuggestFromList compares the typed value case-sensitively even
    when case_sensitive=False, so an uppercase first letter never matches a
    lowercased option — this does the matching explicitly.)
    """
    def __init__(self, options):
        super().__init__(use_cache=False, case_sensitive=True)
        self._options = list(options)

    async def get_suggestion(self, value: str):
        v = value.lower()
        if not v:
            return None
        for opt in self._options:
            if opt.lower().startswith(v):
                return opt
        return None
import os
import tempfile
import shutil
import subprocess

class ConfirmDialog(ModalScreen):
    """Yes/no confirmation for destructive actions. Dismisses with True/False."""
    def __init__(self, message: str, confirm_label: str = "Delete"):
        super().__init__()
        self.message = message
        self.confirm_label = confirm_label

    def compose(self):
        yield Vertical(
            Label(self.message, classes="dialog-title"),
            Horizontal(
                Button(self.confirm_label, variant="error", id="confirm-btn"),
                Button("Cancel", variant="primary", id="cancel-btn"),
                classes="btn-row"
            ),
            id="dialog-container"
        )

    def on_mount(self):
        # Cancel is focused by default: Enter must never destroy by accident.
        self.query_one("#cancel-btn").focus()

    def on_button_pressed(self, event: Button.Pressed):
        self.dismiss(event.button.id == "confirm-btn")


class GmailLabelScreen(ModalScreen):
    """Pick a label/folder for a thread from the existing list, or create one.

    Dismisses with {"label": name, "move": bool, "create": bool} or None.
    "move" also removes the thread from the Inbox (Gmail's move-to-folder).
    """
    def __init__(self, labels: list):
        super().__init__()
        self._labels = labels or []

    def compose(self):
        yield Vertical(
            Label("Label / Move conversation", classes="dialog-title"),
            Label("Choose an existing label:" if self._labels else "No labels yet — create one below."),
            OptionList(*self._labels, id="label-list"),
            Checkbox("Move here (also remove from Inbox)", value=False, id="label-move"),
            Label("…or create a new label:"),
            Input(placeholder="New label name", id="new-label"),
            Horizontal(
                Button("Apply", variant="success", id="label-apply-btn"),
                Button("Create & apply", variant="primary", id="label-create-btn"),
                Button("Cancel", id="cancel-btn"),
                classes="btn-row",
            ),
            id="dialog-container",
        )

    def on_mount(self):
        if self._labels:
            self.query_one("#label-list", OptionList).focus()
        else:
            self.query_one("#new-label", Input).focus()

    def _move(self) -> bool:
        return self.query_one("#label-move", Checkbox).value

    def on_button_pressed(self, event: Button.Pressed):
        if event.button.id == "label-apply-btn":
            ol = self.query_one("#label-list", OptionList)
            idx = ol.highlighted
            if idx is None or idx < 0 or idx >= len(self._labels):
                self.app.notify("Select a label, or use Create & apply.", severity="warning")
                return
            self.dismiss({"label": self._labels[idx], "move": self._move(), "create": False})
        elif event.button.id == "label-create-btn":
            name = self.query_one("#new-label", Input).value.strip()
            if not name:
                self.app.notify("Enter a name for the new label.", severity="warning")
                return
            self.dismiss({"label": name, "move": self._move(), "create": True})
        else:
            self.dismiss(None)


class GmailAttachmentScreen(ModalScreen):
    """Pick one attachment from an email and a destination directory to save it.

    Dismisses with {"attachment": <dict>, "dest_dir": <path>} or None.
    Each attachment dict carries filename/mimeType/sizeHuman/attachmentId/messageId.
    """
    def __init__(self, attachments: list):
        super().__init__()
        self._attachments = attachments or []
        # Pre-format human labels: "filename (size, mimeType)".
        self._labels = [
            "{}  ({})".format(
                a.get("filename") or "(unnamed)",
                a.get("sizeHuman") or _human_size(a.get("size")),
            )
            for a in self._attachments
        ]

    def compose(self):
        yield Vertical(
            Label("Download attachment", classes="dialog-title"),
            Label("Choose an attachment:"),
            OptionList(*self._labels, id="attachment-list"),
            Label("Save to directory:"),
            Input(value=os.path.expanduser("~/Downloads"), id="attachment-dest"),
            Horizontal(
                Button("Download", variant="success", id="attachment-dl-btn"),
                Button("Cancel", id="cancel-btn"),
                classes="btn-row",
            ),
            id="dialog-container",
        )

    def on_mount(self):
        self.query_one("#attachment-list", OptionList).focus()

    def on_button_pressed(self, event: Button.Pressed):
        if event.button.id == "attachment-dl-btn":
            ol = self.query_one("#attachment-list", OptionList)
            idx = ol.highlighted
            if idx is None or idx < 0 or idx >= len(self._attachments):
                self.app.notify("Select an attachment to download.", severity="warning")
                return
            dest_dir = self.query_one("#attachment-dest", Input).value.strip()
            if not dest_dir:
                self.app.notify("Enter a destination directory.", severity="warning")
                return
            self.dismiss({"attachment": self._attachments[idx], "dest_dir": dest_dir})
        else:
            self.dismiss(None)


def _human_size(size) -> str:
    """Fallback humaniser for byte counts when gog gives no sizeHuman."""
    try:
        n = float(size)
    except (TypeError, ValueError):
        return "?"
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


class SettingsScreen(ModalScreen):
    """Preferences page. Dismisses with the updated settings dict, or None."""
    def __init__(self, settings: dict):
        super().__init__()
        self._settings = settings or {}

    def compose(self):
        yield Vertical(
            Label("Settings", classes="dialog-title"),
            Label("Voice & Speech", classes="settings-section"),
            Checkbox(
                "Enable voice input (push-to-talk mic button)",
                value=bool(self._settings.get("voice_input", False)),
                id="set-voice-input",
            ),
            Checkbox(
                "Speak assistant replies (text-to-speech)",
                value=bool(self._settings.get("spoken_replies", False)),
                id="set-spoken-replies",
            ),
            Checkbox(
                "Use the natural cloud voice (Gemini TTS) — uncheck for the offline system voice",
                value=self._settings.get("tts_engine", "auto") != "system",
                id="set-natural-voice",
            ),
            Label(
                "Voice input records your microphone and sends the clip to Gemini "
                "to transcribe (needs pw-record/arecord/ffmpeg/sox). Spoken replies "
                "use Gemini's natural voice by default, played via aplay/ffplay; "
                "the offline fallback is espeak-ng/spd-say/say.",
                classes="settings-hint",
            ),
            Horizontal(
                Button("Save", variant="success", id="settings-save-btn"),
                Button("Cancel", id="cancel-btn"),
                classes="btn-row",
            ),
            id="dialog-container",
        )

    def on_button_pressed(self, event: Button.Pressed):
        if event.button.id == "settings-save-btn":
            self.dismiss({
                "voice_input": self.query_one("#set-voice-input", Checkbox).value,
                "spoken_replies": self.query_one("#set-spoken-replies", Checkbox).value,
                "tts_engine": "auto" if self.query_one("#set-natural-voice", Checkbox).value else "system",
            })
        else:
            self.dismiss(None)


class PromptDialog(ModalScreen):
    """Generic text input popup dialog."""
    def __init__(self, title: str, placeholder: str = "", default: str = ""):
        super().__init__()
        self.title_text = title
        self.placeholder = placeholder
        self.default_val = default

    def compose(self):
        yield Vertical(
            Label(self.title_text, classes="dialog-title"),
            Input(placeholder=self.placeholder, value=self.default_val, id="dialog-input"),
            Horizontal(
                Button("OK", variant="primary", id="ok-btn"),
                Button("Cancel", id="cancel-btn"),
                classes="btn-row"
            ),
            id="dialog-container"
        )

    def on_mount(self):
        self.query_one("#dialog-input").focus()

    def on_button_pressed(self, event: Button.Pressed):
        if event.button.id == "ok-btn":
            val = self.query_one("#dialog-input").value
            self.dismiss(val)
        else:
            self.dismiss(None)

class TaskCreateScreen(ModalScreen):
    """Modal screen for creating or editing a Google Task.

    Pass a prefill dict (title/notes/due) to open in edit mode, mirroring
    CalendarCreateScreen.
    """
    def __init__(self, tasklist_id: str, prefill: dict = None):
        super().__init__()
        self.tasklist_id = tasklist_id
        self.prefill = prefill or {}

    def compose(self):
        p = self.prefill
        editing = bool(p)
        yield Vertical(
            Label("Edit Task" if editing else "Add New Task", classes="dialog-title"),
            Label("Title:"),
            Input(value=p.get("title", ""), placeholder="Task title", id="task-title"),
            Label("Notes:"),
            TextArea(p.get("notes", ""), id="task-notes", classes="multi-line-input"),
            Label("Due date (YYYY-MM-DD, optional):"),
            Input(value=p.get("due", ""), placeholder="2026-06-13", id="task-due"),
            Horizontal(
                Button("Save" if editing else "Add", variant="success", id="add-btn"),
                Button("Cancel", id="cancel-btn"),
                classes="btn-row"
            ),
            id="dialog-container"
        )

    def on_mount(self):
        self.query_one("#task-title").focus()

    def on_button_pressed(self, event: Button.Pressed):
        if event.button.id == "add-btn":
            title = self.query_one("#task-title").value
            notes = self.query_one("#task-notes").text
            due = self.query_one("#task-due").value
            self.dismiss({"title": title, "notes": notes, "due": due})
        else:
            self.dismiss(None)

class CalendarCreateScreen(ModalScreen):
    """Modal screen for creating or editing a Google Calendar event."""
    def __init__(self, calendar_id: str = "primary", prefill: dict = None):
        super().__init__()
        self.calendar_id = calendar_id
        self.prefill = prefill or {}

    def compose(self):
        p = self.prefill
        editing = bool(p)
        yield Vertical(
            Horizontal(
                Label("Edit Calendar Event" if editing else "Create Calendar Event", classes="dialog-title"),
                Button("⛶ Fullscreen", id="cal-fullscreen-btn", classes="compose-win-btn"),
                classes="compose-titlebar",
            ),
            Label("Summary / Title:"),
            Input(value=p.get("summary", ""), placeholder="Event Title", id="event-summary"),
            Label("Start Time (RFC3339, e.g. 2026-06-11T10:00:00Z):"),
            Input(value=p.get("start", ""), placeholder="2026-06-11T10:00:00Z", id="event-start"),
            Label("End Time (RFC3339, e.g. 2026-06-11T11:00:00Z):"),
            Input(value=p.get("end", ""), placeholder="2026-06-11T11:00:00Z", id="event-end"),
            Label("Description:"),
            Input(value=p.get("description", ""), placeholder="Event description", id="event-desc"),
            Label("Location:"),
            Input(value=p.get("location", ""), placeholder="Event location", id="event-loc"),
            Horizontal(
                Button("Save" if editing else "Create", variant="success", id="create-btn"),
                Button("Cancel", id="cancel-btn"),
                classes="btn-row"
            ),
            id="calendar-create-container",
            classes="side-panel",
        )

    def on_mount(self):
        self.query_one("#event-summary").focus()

    def on_button_pressed(self, event: Button.Pressed):
        if event.button.id == "cal-fullscreen-btn":
            container = self.query_one("#calendar-create-container")
            container.toggle_class("fullscreen")
            event.button.label = "⛶ Restore" if container.has_class("fullscreen") else "⛶ Fullscreen"
            return
        if event.button.id == "create-btn":
            summary = self.query_one("#event-summary").value
            start = self.query_one("#event-start").value
            end = self.query_one("#event-end").value
            desc = self.query_one("#event-desc").value
            loc = self.query_one("#event-loc").value
            self.dismiss({
                "summary": summary,
                "start": start,
                "end": end,
                "description": desc,
                "location": loc
            })
        else:
            self.dismiss(None)

class ContactCreateScreen(ModalScreen):
    """Modal screen for creating or editing a Google contact.

    Pass a `prefill` dict (with name/email/phone) to edit an existing contact;
    omit it to create a new one. Returns {name, email, phone} on save.
    """
    def __init__(self, prefill: dict = None):
        super().__init__()
        self.prefill = prefill or {}

    def compose(self):
        p = self.prefill
        editing = bool(p)
        yield Vertical(
            Label("Edit Contact" if editing else "New Contact", classes="dialog-title"),
            Label("Name:"),
            Input(value=p.get("name", ""), placeholder="Full name", id="contact-name"),
            Label("Email:"),
            Input(value=p.get("email", ""), placeholder="name@example.com", id="contact-email"),
            Label("Phone:"),
            Input(value=p.get("phone", ""), placeholder="+1 555 123 4567", id="contact-phone"),
            Horizontal(
                Button("Save" if editing else "Create", variant="success", id="create-btn"),
                Button("Cancel", id="cancel-btn"),
                classes="btn-row"
            ),
            id="contact-create-container",
            classes="side-panel",
        )

    def on_mount(self):
        self.query_one("#contact-name").focus()

    def on_button_pressed(self, event: Button.Pressed):
        if event.button.id == "create-btn":
            self.dismiss({
                "name": self.query_one("#contact-name").value.strip(),
                "email": self.query_one("#contact-email").value.strip(),
                "phone": self.query_one("#contact-phone").value.strip(),
            })
        else:
            self.dismiss(None)


class GmailComposeScreen(ModalScreen):
    """Modal screen to compose or reply to emails with built-in Gemini drafting."""
    def __init__(self, to: str = "", subject: str = "", body: str = "", thread_id: str = None, reply_to_message_id: str = None):
        super().__init__()
        self.to_default = to
        self.subject_default = subject
        self.body_default = body
        self.thread_id = thread_id
        self.reply_to_message_id = reply_to_message_id

    def compose(self):
        title = "Reply to Email" if self.thread_id else "Compose Email"
        yield Vertical(
            # Title bar with a Gmail-style fullscreen toggle.
            Horizontal(
                Label(title, classes="dialog-title"),
                Button("⛶ Fullscreen", id="compose-fullscreen-btn", classes="compose-win-btn"),
                classes="compose-titlebar",
            ),
            Label("To:"),
            Input(value=self.to_default, placeholder="recipient@example.com", id="email-to"),
            Label("Subject:"),
            Input(value=self.subject_default, placeholder="Subject", id="email-subject"),
            Label("Body:"),
            TextArea(self.body_default, id="email-body"),
            
            # Gemini Assistant section
            Label("Gemini Drafting Assistant:", id="gemini-label"),
            Input(placeholder="Instructions (e.g. 'draft a polite rejection', 'tell them I am free on Friday')", id="gemini-prompt"),
            Horizontal(
                Button("Generate AI Draft", variant="primary", id="ai-draft-btn"),
                Button("Editor", variant="primary", id="external-editor-btn"),
                Button("Save Draft", variant="primary", id="save-draft-btn"),
                Button("Send", variant="success", id="send-btn"),
                Button("Cancel", id="cancel-btn"),
                classes="btn-row"
            ),
            id="gmail-compose-container",
            classes="side-panel",
        )

    def on_mount(self):
        if self.thread_id:
            self.query_one("#gemini-prompt").focus()
        else:
            self.query_one("#email-to").focus()
        # Populate recipient autocomplete from the address book in the background.
        self.run_worker(self._load_recipient_suggestions(), exclusive=True)

    async def _load_recipient_suggestions(self):
        try:
            suggestions = await GogAPI.contact_suggestions()
        except Exception:
            return
        if suggestions:
            self.query_one("#email-to", Input).suggester = ContactSuggester(suggestions)

    async def on_button_pressed(self, event: Button.Pressed):
        if event.button.id == "compose-fullscreen-btn":
            # Toggle between the right-docked panel and full width.
            container = self.query_one("#gmail-compose-container")
            container.toggle_class("fullscreen")
            event.button.label = "⛶ Restore" if container.has_class("fullscreen") else "⛶ Fullscreen"
            return
        if event.button.id == "cancel-btn":
            self.dismiss(None)
        elif event.button.id in ("send-btn", "save-draft-btn"):
            self.dismiss({
                "action": "draft" if event.button.id == "save-draft-btn" else "send",
                "to": self.query_one("#email-to").value,
                "subject": self.query_one("#email-subject").value,
                "body": self.query_one("#email-body").text,
                "thread_id": self.thread_id,
                "reply_to_message_id": self.reply_to_message_id
            })
        elif event.button.id == "external-editor-btn":
            body_area = self.query_one("#email-body")
            initial_text = body_area.text
            
            def launch_editor():
                editor = os.environ.get("EDITOR")
                if not editor:
                    for candidate in ["nvim", "vim", "nano", "vi"]:
                        if shutil.which(candidate):
                            editor = candidate
                            break
                if not editor:
                    editor = "nano"
                
                with tempfile.NamedTemporaryFile(suffix=".txt", delete=False, mode="w", encoding="utf-8") as f:
                    f.write(initial_text)
                    temp_path = f.name
                
                try:
                    subprocess.run([editor, temp_path])
                    with open(temp_path, "r", encoding="utf-8") as f:
                        return f.read()
                except Exception:
                    return initial_text
                finally:
                    if os.path.exists(temp_path):
                        os.remove(temp_path)

            with self.app.suspend():
                edited_text = launch_editor()
            
            body_area.text = edited_text
        elif event.button.id == "ai-draft-btn":
            await self.generate_ai_draft()

    async def generate_ai_draft(self):
        user_instructions = self.query_one("#gemini-prompt").value
        if not user_instructions:
            return

        body_area = self.query_one("#email-body")
        previous_text = body_area.text
        body_area.text = "Generating draft with Gemini..."

        # Awaited (not a bare create_task) so a failure can never strand the
        # placeholder text; on error, restore what the user had written.
        try:
            draft_text = await GeminiAPI.draft_reply(
                original_subject=self.subject_default,
                original_sender=self.to_default,
                original_body=self.body_default,
                user_instructions=user_instructions
            )
            body_area.text = draft_text
        except Exception as e:
            body_area.text = previous_text
            self.app.notify(f"AI draft failed: {e}", severity="error")

    async def on_input_submitted(self, event: Input.Submitted):
        if event.input.id == "gemini-prompt":
            await self.generate_ai_draft()


# Single source of truth for available themes: (key, display label).
# `key` must match a `.theme-<key>` rule in styles.tcss.
THEMES = [
    ("gruvbox", "Gruvbox Dark"),
    ("catppuccin", "Catppuccin Mocha"),
    ("nord", "Nord"),
    ("dracula", "Dracula"),
    ("monokai", "Monokai"),
    ("solarized", "Solarized Dark"),
]

THEME_PREFIX = "theme-btn-"


class ThemeSelectScreen(ModalScreen):
    """Modal screen for selecting app theme."""
    def compose(self):
        yield Vertical(
            Label("Select Theme", classes="dialog-title"),
            *(Button(label, id=f"{THEME_PREFIX}{key}") for key, label in THEMES),
            Button("Cancel", id="cancel-btn"),
            id="dialog-container"
        )

    def on_button_pressed(self, event: Button.Pressed):
        if event.button.id and event.button.id.startswith(THEME_PREFIX):
            # removeprefix (not split) so multi-word theme keys stay intact.
            self.dismiss(event.button.id.removeprefix(THEME_PREFIX))
        else:
            self.dismiss(None)
