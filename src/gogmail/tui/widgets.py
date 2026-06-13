from textual.widgets import Static, DataTable, Label, Input, Button, RichLog, ContentSwitcher
from textual.containers import Vertical, Horizontal, Container
from textual.message import Message
from gogmail.gog_api import GogAPI
from gogmail import images
from gogmail.llm import get_provider
from gogmail.zoom_api import ZoomAPI
import asyncio
import base64
import calendar
import logging
import html
import re
import os
import shutil
import subprocess
import tempfile
import webbrowser
from datetime import datetime, date, timedelta
from html.parser import HTMLParser
from urllib.parse import urlparse
from rich.text import Text
from rich.markup import escape as rich_escape

class TUIHTMLParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.text = []
        self.ignore_depth = 0
        # Only tags with real closing tags belong here. meta/link are void
        # elements (no end tag) — including them leaked ignore_depth on every
        # <meta>/<link> and silently blanked the rest of the document.
        self.hide_tags = {'style', 'script', 'head', 'title'}
        self.current_link = None
        self.link_text = []
        self.style_stack = []
        # Record every <img src> so callers can offer to load remote images
        # (a tracking-pixel vector — never auto-fetched, see GmailTab).
        self.image_srcs = []

    def handle_starttag(self, tag, attrs):
        if tag in self.hide_tags:
            self.ignore_depth += 1
            return
        if self.ignore_depth > 0:
            return

        attrs_dict = dict(attrs)
        if tag == 'br':
            self.text.append('\n')
        elif tag in {'p', 'div', 'tr', 'blockquote'}:
            self.text.append('\n')
        elif tag == 'h1':
            self.text.append('\n\n[bold underline magenta]')
            self.style_stack.append('[/bold underline magenta]')
        elif tag in {'h2', 'h3', 'h4'}:
            self.text.append('\n\n[bold cyan]')
            self.style_stack.append('[/bold cyan]')
        elif tag in {'h5', 'h6'}:
            self.text.append('\n\n[bold yellow]')
            self.style_stack.append('[/bold yellow]')
        elif tag in {'b', 'strong'}:
            self.text.append('[bold]')
            self.style_stack.append('[/bold]')
        elif tag in {'i', 'em'}:
            self.text.append('[italic]')
            self.style_stack.append('[/italic]')
        elif tag == 'a':
            self.current_link = attrs_dict.get('href', '')
            self.link_text = []
        elif tag == 'img':
            src = (attrs_dict.get('src') or '').strip()
            if src:
                self.image_srcs.append(src)
            alt = attrs_dict.get('alt') or ''
            alt = alt.strip()
            if not alt:
                alt = 'Image'
            placeholder = rich_escape(f"[🖼️  {alt}]")
            if self.current_link:
                self.text.append(f"\n[link={self.current_link}]{placeholder}[/link]\n")
            else:
                self.text.append(f"\n{placeholder}\n")
        elif tag == 'li':
            self.text.append('\n  • ')

    def handle_endtag(self, tag):
        if tag in self.hide_tags:
            self.ignore_depth = max(0, self.ignore_depth - 1)
            return
        if self.ignore_depth > 0:
            return

        if tag in {'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'b', 'strong', 'i', 'em'}:
            if self.style_stack:
                close_style = self.style_stack.pop()
                self.text.append(close_style)
            if tag in {'h1', 'h2', 'h3', 'h4', 'h5', 'h6'}:
                self.text.append('\n')
        elif tag in {'p', 'div', 'tr', 'blockquote'}:
            self.text.append('\n')
        elif tag == 'a':
            if self.current_link:
                link_str = "".join(self.link_text).strip()
                link_str = clean_text_entities(link_str)
                if link_str:
                    if link_str.lower() == self.current_link.lower() or link_str.startswith("http") or len(link_str) > 50:
                        display_text = clean_url_display(link_str)
                    else:
                        display_text = link_str
                    display_text = rich_escape(display_text)
                    self.text.append(f"[link={self.current_link}][underline blue]{display_text}[/underline blue][/link]")
            self.current_link = None
            self.link_text = []

    def handle_data(self, data):
        if self.ignore_depth == 0:
            if self.current_link is not None:
                self.link_text.append(data)
            else:
                cleaned = clean_text_entities(data)
                cleaned = re.sub(r'[ \t\r\f]+', ' ', cleaned)
                if cleaned:
                    # Escape so user content can't break the Rich markup we emit
                    # (an stray "[/x]" would otherwise raise MarkupError on render).
                    self.text.append(rich_escape(cleaned))

    def get_text(self):
        content = "".join(self.text)
        content = re.sub(r'(\s*\n){3,}', '\n\n', content)
        lines = []
        for line in content.split('\n'):
            if line.startswith('  • '):
                lines.append(line.rstrip())
            else:
                lines.append(line.strip())
        content = "\n".join(lines)
        return content.strip()

def clean_url_display(url: str) -> str:
    try:
        parsed = urlparse(url)
        netloc = parsed.netloc
        path = parsed.path
        if len(path) > 15:
            path = path[:12] + "..."
        display = f"{netloc}{path}"
        if len(display) > 40:
            display = display[:37] + "..."
        return display
    except Exception:
        if len(url) > 40:
            return url[:37] + "..."
        return url

def clean_text_entities(text: str) -> str:
    if not text:
        return ""
    for _ in range(3):
        decoded = html.unescape(text)
        if decoded == text:
            break
        text = decoded
    
    text = re.sub(r'[\xa0\u2007\u2008\u2009\u200a\u202f\u205f\u3000]', ' ', text)
    text = re.sub(r'[\u200b\u200c\u200d\u200e\u200f\u2060\u034f]', '', text)
    text = re.sub(r' +', ' ', text)
    return text

def extract_html_body(msg: dict) -> str:
    if not msg or "message" not in msg:
        return ""

    def get_html_from_part(part):
        mtype = part.get("mimeType", "")
        if mtype == "text/html":
            data = part.get("body", {}).get("data", "")
            if data:
                try:
                    return base64.urlsafe_b64decode(data.encode("utf-8")).decode("utf-8", errors="ignore")
                except Exception:
                    pass
        for p in part.get("parts", []):
            ret = get_html_from_part(p)
            if ret:
                return ret
        return ""
        
    return get_html_from_part(msg["message"].get("payload", {}))

def format_email_body(body: str) -> str:
    if not body:
        return ""
    if '<html' in body.lower() or '<div' in body.lower() or '<p>' in body.lower() or '<br' in body.lower():
        parser = TUIHTMLParser()
        try:
            parser.feed(body)
            return parser.get_text()
        except Exception:
            pass
    return clean_text_entities(body).strip()


def strip_html_to_text(html: str) -> str:
    """Last-resort HTML -> text: drop script/style, strip tags, tidy whitespace.

    Guarantees readable output for any HTML, even when the rich TUI parser can't
    produce styled output.
    """
    if not html:
        return ""
    text = re.sub(r'(?is)<(script|style)[^>]*>.*?</\1>', ' ', html)
    text = re.sub(r'(?i)<br\s*/?>', '\n', text)
    text = re.sub(r'(?i)</(p|div|tr|li|h[1-6])>', '\n', text)
    text = re.sub(r'<[^>]+>', '', text)
    text = clean_text_entities(text)
    lines = [ln.strip() for ln in text.split('\n')]
    text = '\n'.join(ln for ln in lines if ln)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def best_email_text(msg: dict) -> str:
    """Most readable body for a message.

    Try, in order: the rendered HTML part, gog's `body`, and finally a plain
    tag-strip of whichever HTML we have — so an email never renders blank.
    """
    html_body = extract_html_body(msg)
    body = msg.get("body", "")
    for candidate in (html_body, body):
        if candidate:
            rendered = format_email_body(candidate)
            if rendered.strip():
                return rendered
    # Last resort: brute-force strip tags from whatever HTML we have.
    for candidate in (html_body, body):
        plain = strip_html_to_text(candidate)
        if plain.strip():
            return plain
    return ""


def extract_inline_images(msg: dict) -> list:
    """Walk a message payload for embedded (CID) image parts.

    Returns a list of {"name", "data": bytes} for every `image/*` part that
    carries base64 `body.data` (URL-safe). These are part of the message — no
    network is touched — so they are safe to render automatically.
    """
    if not msg or "message" not in msg:
        return []

    found = []

    def walk(part):
        mtype = (part.get("mimeType") or "").lower()
        data = part.get("body", {}).get("data", "")
        if mtype.startswith("image/") and data:
            try:
                raw = base64.urlsafe_b64decode(data.encode("utf-8"))
            except Exception:
                raw = b""
            if raw:
                name = part.get("filename") or mtype.split("/", 1)[-1] or "image"
                found.append({"name": name, "data": raw})
        for p in part.get("parts", []):
            walk(p)

    walk(msg["message"].get("payload", {}))
    return found


def collect_remote_image_urls(html_body: str) -> list:
    """Return the distinct https:// `<img src>` URLs in an email's HTML.

    Used by the privacy-gated "Load images" action; remote images are a
    tracking-pixel vector, so they are never fetched without an explicit opt-in.
    """
    if not html_body:
        return []
    parser = TUIHTMLParser()
    try:
        parser.feed(html_body)
    except Exception:
        pass
    seen, urls = set(), []
    for src in parser.image_srcs:
        if src.lower().startswith("https://") and src not in seen:
            seen.add(src)
            urls.append(src)
    return urls


def view_media_file(app, file_path: str):

    ext = os.path.splitext(file_path)[1].lower()
    
    # 1. Image Viewing
    if ext in ['.png', '.jpg', '.jpeg', '.gif', '.bmp', '.webp']:
        if shutil.which("timg"):
            with app.suspend():
                subprocess.run(["timg", "-g", f"{app.size.width}x{app.size.height}", file_path])
                input("\nPress Enter to return...")
            return
        elif shutil.which("viu"):
            with app.suspend():
                subprocess.run(["viu", file_path])
                input("\nPress Enter to return...")
            return
        elif shutil.which("wezterm") and os.environ.get("TERM_PROGRAM") == "WezTerm":
            with app.suspend():
                subprocess.run(["wezterm", "imgcat", file_path])
                input("\nPress Enter to return...")
            return
        elif shutil.which("kitty"):
            with app.suspend():
                subprocess.run(["kitty", "+kitten", "icat", file_path])
                input("\nPress Enter to return...")
            return
        
        try:
            subprocess.Popen(["xdg-open", file_path])
            app.notify("Opened image in system viewer.")
        except Exception as e:
            app.notify(f"Failed to open: {str(e)}", severity="error")
            
    # 2. PDF Viewing
    elif ext == '.pdf':
        if shutil.which("pdftotext"):
            # List-form pipe (no shell): a crafted filename must never reach sh.
            with app.suspend():
                pdf = subprocess.Popen(["pdftotext", file_path, "-"], stdout=subprocess.PIPE)
                subprocess.run(["less"], stdin=pdf.stdout)
                pdf.wait()
            return
        
        try:
            subprocess.Popen(["xdg-open", file_path])
            app.notify("Opened PDF in system viewer.")
        except Exception as e:
            app.notify(f"Failed to open: {str(e)}", severity="error")
            
    else:
        try:
            subprocess.Popen(["xdg-open", file_path])
            app.notify("Opened file in system viewer.")
        except Exception as e:
            app.notify(f"Failed to open: {str(e)}", severity="error")

def human_size(size) -> str:
    """'284113' -> '277 KB'. Google Docs-native files have no size -> '—'."""
    try:
        n = int(size)
    except (TypeError, ValueError):
        return "—"
    if n <= 0:
        return "—"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}".replace(".0 ", " ")
        n /= 1024
    return f"{n} B"


def relative_date(date_str: str) -> str:
    """Compact mail-client date: time today, weekday this week, 'Jun 10' else."""
    if not date_str:
        return ""
    try:
        dt = datetime.strptime(date_str[:16], "%Y-%m-%d %H:%M")
    except ValueError:
        return date_str
    now = datetime.now()
    if dt.date() == now.date():
        return dt.strftime("%H:%M")
    if (now.date() - dt.date()).days < 7:
        return dt.strftime("%a %H:%M")
    if dt.year == now.year:
        return dt.strftime("%b %d")
    return dt.strftime("%Y-%m-%d")


class StatusNotification(Message):
    """Notify main app to update status bar. Set is_error to surface a toast."""
    def __init__(self, message: str, is_error: bool = False):
        super().__init__()
        self.message = message
        self.is_error = is_error

# --- GMAIL TAB ---
class GmailTab(Vertical):
    def compose(self):
        yield Horizontal(
            Label(" Gmail Inbox & Search ", classes="view-header"),
            Input(placeholder="Search emails (e.g. is:unread, from:boss)...", id="email-search-input"),
            id="gmail-header-row"
        )
        
        with ContentSwitcher(id="gmail-switcher", initial="gmail-list-view"):
            # 1. List View
            with Vertical(id="gmail-list-view"):
                yield Horizontal(
                    Button("Compose", variant="success", id="gmail-compose-btn"),
                    Button("Refresh", variant="primary", id="gmail-refresh-btn"),
                    Button("Load more", variant="primary", id="gmail-load-more-btn"),
                    classes="btn-row"
                )
                yield DataTable(id="email-table")
                
            # 2. Detail View
            with Vertical(id="gmail-detail-view"):
                yield Horizontal(
                    Button("← Back", variant="primary", id="gmail-back-btn"),
                    Button("Reply", variant="success", id="gmail-reply-btn"),
                    Button("Star", variant="primary", id="gmail-star-btn"),
                    Button("Label", variant="primary", id="gmail-label-btn"),
                    Button("Archive", variant="primary", id="gmail-archive-btn"),
                    Button("Trash", variant="error", id="gmail-trash-btn"),
                    Button("Attachments", variant="primary", id="gmail-attachments-btn"),
                    Button("Load images", variant="primary", id="gmail-images-btn"),
                    Button("AI Summary", variant="primary", id="gmail-summary-btn"),
                    Button("Browser", variant="primary", id="gmail-browser-btn"),
                    Button("Copy Body", variant="primary", id="gmail-copy-btn"),
                    classes="btn-row"
                )
                yield RichLog(id="email-body-view", highlight=True, markup=True, wrap=True, min_width=0)

    # Page size for each gog gmail search request. Larger than gog's default of
    # 10 so big mailboxes page in fewer, chunkier requests.
    PAGE_SIZE = 25

    def on_mount(self):
        table = self.query_one("#email-table")
        table.cursor_type = "row"
        table.add_columns("Date", "From", "Subject", "Labels")
        # No results loaded yet, so there's nothing more to load.
        self.next_token = ""
        self.query_one("#gmail-load-more-btn").display = False

    async def set_query(self, query: str):
        self.query_one("#email-search-input").value = query
        await self.refresh_emails(query)

    async def refresh_emails(self, query: str = None):
        # Remember the active query so Back / archive / trash reload the same view
        # (e.g. the Inbox) instead of snapping back to the default is:unread.
        if query is None:
            query = getattr(self, "current_query", "is:unread")
        self.current_query = query

        # Cancel any existing refresh task to avoid race conditions/duplicate keys
        if hasattr(self, "_refresh_task") and self._refresh_task and not self._refresh_task.done():
            self._refresh_task.cancel()
            try:
                await self._refresh_task
            except asyncio.CancelledError:
                pass
        
        self._refresh_task = asyncio.create_task(self._do_refresh_emails(query))
        try:
            await self._refresh_task
        except asyncio.CancelledError:
            pass

    async def _do_refresh_emails(self, query: str):
        # A fresh search/refresh resets pagination: clear the table + data and
        # start from the first page.
        self.post_message(StatusNotification("Fetching emails..."))
        table = self.query_one("#email-table")
        table.clear()
        self.threads_data = []
        self.next_token = ""

        threads, next_token = await GogAPI.gmail_search_page(query, max_results=self.PAGE_SIZE)
        self._append_threads(threads, next_token)
        self._notify_loaded()

    def _append_threads(self, threads: list, next_token: str):
        """Append fetched threads to the table + threads_data and update the
        Load more button from the new page token (shown only when more remain)."""
        table = self.query_one("#email-table")
        hidden_labels = {"CATEGORY_UPDATES", "CATEGORY_PERSONAL", "UNREAD"}
        for t in threads:
            labels = t.get("labels", [])
            labels_str = ", ".join(l for l in labels if l not in hidden_labels)
            # Unread rows are bold (the UNREAD label itself is hidden as noise).
            style = "bold" if "UNREAD" in labels else ""
            table.add_row(
                Text(relative_date(t.get("date", "")), style=style),
                Text(t.get("from", "")[:30], style=style),
                Text(t.get("subject", ""), style=style),
                Text(labels_str, style="dim"),
                key=t.get("id")
            )
        self.threads_data = getattr(self, "threads_data", []) + threads
        self.next_token = next_token
        self.query_one("#gmail-load-more-btn").display = bool(next_token)

    def _notify_loaded(self):
        n = len(getattr(self, "threads_data", []))
        suffix = " (more available)" if self.next_token else ""
        self.post_message(StatusNotification(f"Loaded {n}{suffix}."))

    async def _load_more_emails(self):
        """Fetch the next page and APPEND its rows (never clears the table)."""
        if not getattr(self, "next_token", ""):
            return
        self.post_message(StatusNotification("Loading more emails..."))
        threads, next_token = await GogAPI.gmail_search_page(
            getattr(self, "current_query", "is:unread"),
            max_results=self.PAGE_SIZE, page_token=self.next_token)
        self._append_threads(threads, next_token)
        self._notify_loaded()

    async def on_input_submitted(self, event: Input.Submitted):
        if event.input.id == "email-search-input":
            await self.refresh_emails(event.value)

    async def on_data_table_row_selected(self, event: DataTable.RowSelected):
        thread_id = event.row_key.value
        self.selected_thread_id = thread_id
        thread = next((t for t in getattr(self, "threads_data", []) if t.get("id") == thread_id), {})
        self.selected_labels = thread.get("labels", [])
        self.post_message(StatusNotification(f"Loading email {thread_id}..."))

        body_view = self.query_one("#email-body-view")
        body_view.clear()

        # Switch to the detail view immediately and show a placeholder while the
        # (potentially slow) fetch runs, so the pane is never blank. NB: do NOT
        # use RichLog.loading here — toggling that reactive wipes the log's
        # written content once the overlay has actually rendered (a real,
        # non-instant await), which silently blanked slow-loading emails.
        self.query_one("#gmail-switcher").current = "gmail-detail-view"
        body_view.write("[dim]Loading email…[/dim]")
        msg = await GogAPI.gmail_get_message(thread_id)
        body_view.clear()
        self.selected_msg = msg

        if msg:
            headers = msg.get("headers", {})
            # Escape header values: a subject/sender containing "[...]" would
            # otherwise be parsed as Rich markup and could raise MarkupError.
            body_view.write(f"[bold magenta]From:[/bold magenta] {rich_escape(headers.get('from', ''))}")
            body_view.write(f"[bold magenta]Subject:[/bold magenta] {rich_escape(headers.get('subject', ''))}")
            body_view.write(f"[bold magenta]Date:[/bold magenta] {rich_escape(headers.get('date', ''))}")
            body_view.write("-" * 40 + "\n")
            rendered = best_email_text(msg)
            if not rendered.strip():
                body_view.write("[dim](This email has no readable text content.)[/dim]")
            else:
                try:
                    body_view.write(rendered)
                except Exception:
                    # Last-resort safety net: render literally so a stray bracket
                    # can never leave the body blank.
                    body_view.write(Text(rendered))

            # Inline (CID) images travel inside the message — safe, no network —
            # so render them automatically below the text.
            self._render_inline_images(msg)

            # Remote images (<img src="https://…">) are a tracking-pixel vector;
            # auto-load only if the user has opted in, otherwise wait for the
            # "Load images" button.
            if self._remote_images_enabled() and collect_remote_image_urls(extract_html_body(msg)):
                await self._load_remote_images()

            # Auto-mark read in background
            await GogAPI.gmail_mark_read(thread_id)
            self.post_message(StatusNotification("Email loaded."))
        else:
            body_view.write("[red]Failed to load email contents (is your gog token still valid?).[/red]")
            self.post_message(StatusNotification("Failed to load email.", is_error=True))

    # Roughly the pane width to scale images into (half-block cells, 1px wide).
    IMAGE_MAX_COLS = 60
    IMAGE_MAX_ROWS = 20
    IMAGE_LIMIT = 6  # cap how many images we render so the pane isn't flooded

    def _remote_images_enabled(self) -> bool:
        try:
            return bool(self.app.config.get("load_remote_images", False))
        except Exception:
            return False

    def _image_cols(self) -> int:
        try:
            w = self.query_one("#email-body-view").size.width
        except Exception:
            w = 0
        return max(20, min(self.IMAGE_MAX_COLS, w or self.IMAGE_MAX_COLS))

    def _render_inline_images(self, msg: dict):
        """Render embedded (CID) image parts into the body pane."""
        body_view = self.query_one("#email-body-view")
        inline = extract_inline_images(msg)
        if not inline:
            return
        cols = self._image_cols()
        body_view.write("\n[dim]── Inline images ──[/dim]")
        for item in inline[: self.IMAGE_LIMIT]:
            name = item.get("name", "image")
            body_view.write(f"[dim]{rich_escape(name)}[/dim]")
            rendered = images.render_image(item.get("data", b""), max_cols=cols, max_rows=self.IMAGE_MAX_ROWS)
            if rendered is not None:
                try:
                    body_view.write(rendered)
                except Exception:
                    body_view.write(f"[dim][image: {rich_escape(name)}][/dim]")
            else:
                body_view.write(f"[dim][image: {rich_escape(name)}][/dim]")

    async def _load_remote_images(self):
        """Fetch and render the remote https <img> images for the current message."""
        if not hasattr(self, "selected_msg") or not self.selected_msg:
            return
        urls = collect_remote_image_urls(extract_html_body(self.selected_msg))
        body_view = self.query_one("#email-body-view")
        if not urls:
            body_view.write("\n[dim](No remote images in this email.)[/dim]")
            return
        cols = self._image_cols()
        body_view.write("\n[dim]── Remote images ──[/dim]")
        self.post_message(StatusNotification(f"Loading {len(urls)} remote image(s)…"))
        shown = 0
        for url in urls[: self.IMAGE_LIMIT]:
            data = await asyncio.to_thread(images.fetch_image_bytes, url)
            label = clean_url_display(url)
            body_view.write(f"[dim]{rich_escape(label)}[/dim]")
            rendered = images.render_image(data, max_cols=cols, max_rows=self.IMAGE_MAX_ROWS) if data else None
            if rendered is not None:
                try:
                    body_view.write(rendered)
                    shown += 1
                    continue
                except Exception:
                    pass
            body_view.write(f"[dim][image: {rich_escape(label)}][/dim]")
        self.post_message(StatusNotification(f"Loaded {shown} remote image(s)."))

    async def on_button_pressed(self, event: Button.Pressed):
        if event.button.id == "gmail-compose-btn":
            self.app.open_compose_dialog()
            return
        elif event.button.id == "gmail-refresh-btn":
            await self.refresh_emails()
            return
        elif event.button.id == "gmail-load-more-btn":
            await self._load_more_emails()
            return
        elif event.button.id == "gmail-back-btn":
            self.query_one("#gmail-switcher").current = "gmail-list-view"
            await self.refresh_emails()
            return

        # Buttons below require a selected message
        table = self.query_one("#email-table")
        if table.cursor_row is None or not hasattr(self, "threads_data") or not self.threads_data:
            return

        selected_row_idx = table.cursor_row
        thread_id = table.ordered_rows[selected_row_idx].key.value
        
        if event.button.id == "gmail-star-btn":
            starred = "STARRED" in getattr(self, "selected_labels", [])
            ok = await GogAPI.gmail_modify_labels(
                thread_id, remove="STARRED" if starred else "", add="" if starred else "STARRED")
            if ok:
                self.selected_labels = [l for l in getattr(self, "selected_labels", []) if l != "STARRED"]
                if not starred:
                    self.selected_labels.append("STARRED")
                self.post_message(StatusNotification("Unstarred." if starred else "Starred."))
        elif event.button.id == "gmail-label-btn":
            self.app.open_gmail_label_dialog(thread_id)
        elif event.button.id == "gmail-attachments-btn":
            self.app.open_gmail_attachments_dialog(thread_id)
        elif event.button.id == "gmail-images-btn" and hasattr(self, "selected_msg"):
            await self._load_remote_images()
        elif event.button.id == "gmail-archive-btn":
            await GogAPI.gmail_archive(thread_id)
            self.post_message(StatusNotification(f"Archived {thread_id}"))
            self.query_one("#gmail-switcher").current = "gmail-list-view"
            await self.refresh_emails()
        elif event.button.id == "gmail-trash-btn":
            async def do_trash():
                await GogAPI.gmail_trash(thread_id)
                self.post_message(StatusNotification(f"Trashed {thread_id}"))
                self.query_one("#gmail-switcher").current = "gmail-list-view"
                await self.refresh_emails()
            self.app.confirm("Move this conversation to Trash?", do_trash, "Trash")
        elif event.button.id == "gmail-read-btn":
            await GogAPI.gmail_mark_read(thread_id)
            self.post_message(StatusNotification(f"Marked {thread_id} as read"))
            await self.refresh_emails()
        elif event.button.id == "gmail-browser-btn" and hasattr(self, "selected_msg"):
            msg = self.selected_msg
            html_body = extract_html_body(msg)
            body = html_body if html_body else msg.get("body", "")
            try:
                with tempfile.NamedTemporaryFile(suffix=".html", delete=False, mode="w", encoding="utf-8") as f:
                    f.write(body)
                    temp_path = f.name
                # The browser needs the file after we return, so defer removal to app exit.
                self.app.register_temp_file(temp_path)
                webbrowser.open(f"file://{os.path.abspath(temp_path)}")
                self.post_message(StatusNotification("Opened email in web browser."))
            except Exception as e:
                self.post_message(StatusNotification(f"Failed to open in browser: {str(e)}"))
        elif event.button.id == "gmail-copy-btn" and hasattr(self, "selected_msg"):
            body_to_copy = best_email_text(self.selected_msg)
            self.app.copy_to_clipboard(body_to_copy)
            self.post_message(StatusNotification("Email body copied to system clipboard."))
        elif event.button.id == "gmail-summary-btn" and hasattr(self, "selected_msg"):
            msg = self.selected_msg
            headers = msg.get("headers", {})
            self.post_message(StatusNotification("Generating summary with Gemini..."))
            body_view = self.query_one("#email-body-view")
            body_view.clear()
            body_view.write("[dim]Generating summary with Gemini…[/dim]")
            summary = await get_provider().summarize_email(
                subject=headers.get("subject", ""),
                sender=headers.get("from", ""),
                # best_email_text handles HTML-only messages; the raw body
                # field would feed Gemini a wall of tags.
                body=best_email_text(msg)
            )
            # Display summary in the text pane
            body_view.clear()
            body_view.write("[bold green]=== GEMINI SUMMARY ===[/bold green]\n")
            body_view.write(rich_escape(summary))
            body_view.write("\n" + "-" * 40 + "\n")
            try:
                body_view.write(best_email_text(msg))
            except Exception:
                body_view.write(Text(best_email_text(msg)))
            self.post_message(StatusNotification("Summary generated."))
        elif event.button.id == "gmail-reply-btn" and hasattr(self, "selected_msg"):
            msg = self.selected_msg
            headers = msg.get("headers", {})
            orig_body = format_email_body(msg.get("body", ""))
            quoted_lines = [f"> {line}" for line in orig_body.split("\n")]
            quoted_body = f"\n\nOn {headers.get('date', '')}, {headers.get('from', '')} wrote:\n" + "\n".join(quoted_lines)
            
            self.app.open_compose_dialog(
                to=headers.get("from", ""),
                subject=f"Re: {headers.get('subject', '')}",
                body=quoted_body,
                thread_id=msg.get("threadId"),
                reply_to_message_id=msg.get("messageId")
            )


# --- CALENDAR TAB ---
class CalendarTab(Vertical):
    def compose(self):
        yield Horizontal(
            Label(" Google Calendar ", classes="view-header"),
            Horizontal(
                Button("◀", id="cal-prev-btn", classes="cal-nav-btn"),
                Button("Today", id="cal-today-btn", classes="cal-nav-btn"),
                Button("▶", id="cal-next-btn", classes="cal-nav-btn"),
                Button("Month", id="cal-view-month", classes="cal-view-toggle"),
                Button("Week", id="cal-view-week", classes="cal-view-toggle"),
                Button("Day", id="cal-view-day", classes="cal-view-toggle"),
                Button("Calendars", id="cal-pick-btn"),
                Button("Free/Busy", id="cal-freebusy-btn"),
                Button("Add Event", variant="success", id="cal-add-btn"),
                Button("Edit Event", variant="primary", id="cal-edit-btn"),
                Button("Delete Event", variant="error", id="cal-del-btn"),
                Button("RSVP Yes", id="cal-rsvp-yes"),
                Button("RSVP No", id="cal-rsvp-no"),
                classes="header-buttons"
            ),
            classes="view-header-row"
        )
        yield Horizontal(
            DataTable(id="calendar-table"),
            RichLog(id="calendar-detail", highlight=True, markup=True, wrap=True, min_width=0),
            id="calendar-content-row"
        )

    def on_mount(self):
        self.current_date = date.today()
        self.current_view = "month"
        self.events_data = []
        self.tasks_data = []
        self.cell_map = {}
        self.selected_event = None
        # Calendars chosen via the picker. Empty = primary only (default).
        self.selected_calendar_ids = []
        # Map calendar id -> display summary, populated when the picker loads.
        self.calendar_names = {}
        
        table = self.query_one("#calendar-table")
        table.cursor_type = "cell"

    async def refresh_calendar(self):
        self.post_message(StatusNotification("Fetching calendar events & tasks..."))
        
        # 1. Fetch calendar events. With no picker selection this is the original
        #    "primary" call; otherwise fetch each selected calendar concurrently
        #    and merge, tagging every event with the calendar it came from so the
        #    detail panel can show which calendar an event belongs to.
        cal_ids = self.selected_calendar_ids or ["primary"]
        per_cal = await asyncio.gather(
            *(GogAPI.calendar_events(cal_id) for cal_id in cal_ids)
        )
        self.events_data = []
        for cal_id, events in zip(cal_ids, per_cal):
            for e in events:
                e["_calendar"] = cal_id
            self.events_data.extend(events)

        # 2. Fetch all tasks (one gog call per list, run concurrently)
        self.tasks_data = []
        try:
            tasklists = await GogAPI.tasks_lists()
            per_list = await asyncio.gather(
                *(GogAPI.tasks_list(tl["id"]) for tl in tasklists)
            )
            for list_tasks in per_list:
                self.tasks_data.extend(list_tasks)
        except Exception as e:
            logging.error(f"Error fetching tasks for calendar: {e}")
            
        # 3. Render the active view
        self.render_view()
        self.post_message(StatusNotification(
            f"Loaded {len(self.events_data)} events, {len(self.tasks_data)} tasks."))

    def render_view(self):

        table = self.query_one("#calendar-table")
        table.clear(columns=True)
        self.cell_map = {}

        # Index tasks by due-date once per render so per-cell lookups are O(1)
        # instead of re-scanning every task for each of the (up to 42) grid cells.
        self._tasks_by_day = {}
        for t in self.tasks_data:
            due = t.get("due", "")
            if due:
                self._tasks_by_day.setdefault(due[:10], []).append(t)
        
        # Style active buttons in the header row
        for view_name in ["month", "week", "day"]:
            btn = self.query_one(f"#cal-view-{view_name}")
            if self.current_view == view_name:
                btn.variant = "primary"
            else:
                btn.variant = "default"
                
        # Format the title Label in the header
        title_lbl = self.query_one(".view-header")
        if self.current_view == "month":
            title_lbl.update(f" Google Calendar - {self.current_date.strftime('%B %Y')} ")
        elif self.current_view == "week":
            start_of_week = self.current_date - timedelta(days=self.current_date.weekday())
            title_lbl.update(f" Google Calendar - Week of {start_of_week.strftime('%b %d')} ")
        elif self.current_view == "day":
            title_lbl.update(f" Google Calendar - {self.current_date.strftime('%A, %b %d, %Y')} ")
            
        if self.current_view == "month":
            table.cursor_type = "cell"
            table.add_columns("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
            
            first_day_of_month = self.current_date.replace(day=1)
            start_weekday = first_day_of_month.weekday()
            grid_start = first_day_of_month - timedelta(days=start_weekday)
            
            for week_idx in range(6):
                row_cells = []
                for day_idx in range(7):
                    day_date = grid_start + timedelta(days=week_idx * 7 + day_idx)
                    self.cell_map[(week_idx, day_idx)] = (day_date, "all-day")
                    
                    day_str = str(day_date.day)
                    if day_date.month == self.current_date.month:
                        day_text = Text(day_str, style="bold")
                    else:
                        day_text = Text(day_str, style="dim italic")
                        
                    day_events = self.get_day_events(day_date)
                    day_tasks = self.get_day_tasks(day_date)
                    
                    cell_content = Text()
                    cell_content.append(day_text)
                    cell_content.append("\n")
                    
                    count = 0
                    for e in day_events:
                        if count >= 3:
                            cell_content.append("• ...\n", style="cyan")
                            break
                        summary = e.get("summary", "(No Title)")
                        start_time = e.get("start", {}).get("dateTime", "")
                        time_prefix = f"{start_time[11:16]} " if start_time else ""
                        cell_content.append(f"• {time_prefix}{summary[:12]}\n", style="green")
                        count += 1
                        
                    for t in day_tasks:
                        if count >= 4:
                            cell_content.append("✓ ...\n", style="yellow")
                            break
                        status = "✓" if t.get("status") == "completed" else "○"
                        cell_content.append(f"{status} {t.get('title', '')[:10]}\n", style="yellow")
                        count += 1
                        
                    row_cells.append(cell_content)
                # height=None: month cells hold several event/task lines; the
                # default height of 1 squashes the grid to single-line rows.
                table.add_row(*row_cells, height=None)
                
        elif self.current_view == "week":
            table.cursor_type = "cell"
            start_of_week = self.current_date - timedelta(days=self.current_date.weekday())
            
            cols = ["Hour"]
            week_days = []
            for i in range(7):
                d = start_of_week + timedelta(days=i)
                week_days.append(d)
                cols.append(d.strftime("%a %d"))
            table.add_columns(*cols)
            
            row_cells = [Text("All Day", style="bold")]
            for col_idx, d in enumerate(week_days):
                self.cell_map[(0, col_idx + 1)] = (d, "all-day")
                cell_text = Text()
                all_day_events = [e for e in self.get_day_events(d) if not e.get("start", {}).get("dateTime")]
                tasks = self.get_day_tasks(d)
                
                for e in all_day_events:
                    cell_text.append(f"• {e.get('summary', '(No Title)')[:12]}\n", style="green")
                for t in tasks:
                    status = "✓" if t.get("status") == "completed" else "○"
                    cell_text.append(f"{status} {t.get('title', '')[:10]}\n", style="yellow")
                row_cells.append(cell_text)
            table.add_row(*row_cells, height=None)

            for hour in range(24):
                row_cells = [Text(f"{hour:02d}:00", style="dim")]
                row_idx = hour + 1
                for col_idx, d in enumerate(week_days):
                    self.cell_map[(row_idx, col_idx + 1)] = (d, hour)
                    
                    hourly_events = []
                    for e in self.get_day_events(d):
                        start_time = e.get("start", {}).get("dateTime")
                        if start_time:
                            try:
                                h = int(start_time[11:13])
                                if h == hour:
                                    hourly_events.append(e)
                            except Exception:
                                pass
                                
                    cell_text = Text()
                    for e in hourly_events:
                        cell_text.append(f"• {e.get('summary', '(No Title)')[:12]}\n", style="green")
                    row_cells.append(cell_text)
                table.add_row(*row_cells, height=None)
                
        elif self.current_view == "day":
            table.cursor_type = "row"
            table.add_columns("Time", "Events & Tasks")
            
            self.cell_map[0] = (self.current_date, "all-day")
            cell_text = Text()
            all_day_events = [e for e in self.get_day_events(self.current_date) if not e.get("start", {}).get("dateTime")]
            tasks = self.get_day_tasks(self.current_date)
            for e in all_day_events:
                cell_text.append(f"• {e.get('summary', '(No Title)')} (All Day)\n", style="green")
            for t in tasks:
                status = "✓" if t.get("status") == "completed" else "○"
                cell_text.append(f"{status} {t.get('title', '')} (Task)\n", style="yellow")
            table.add_row("All Day", cell_text, height=None)
            
            for hour in range(24):
                row_idx = hour + 1
                self.cell_map[row_idx] = (self.current_date, hour)
                
                hourly_events = []
                for e in self.get_day_events(self.current_date):
                    start_time = e.get("start", {}).get("dateTime")
                    if start_time:
                        try:
                            h = int(start_time[11:13])
                            if h == hour:
                                hourly_events.append(e)
                        except Exception:
                            pass
                            
                cell_text = Text()
                for e in hourly_events:
                    start_time = e.get("start", {}).get("dateTime", "")
                    end_time = e.get("end", {}).get("dateTime", "")
                    time_range = f"{start_time[11:16]} - {end_time[11:16]}" if start_time else ""
                    cell_text.append(f"• [{time_range}] {e.get('summary', '(No Title)')} (Location: {e.get('location', 'N/A')})\n", style="green")
                table.add_row(f"{hour:02d}:00", cell_text, height=None)

        self.update_detail_panel()

    def get_day_events(self, date_obj) -> list:
        day_str = date_obj.strftime("%Y-%m-%d")
        results = []
        for e in self.events_data:
            start = e.get("start", {}).get("dateTime", e.get("start", {}).get("date", ""))
            end = e.get("end", {}).get("dateTime", e.get("end", {}).get("date", ""))
            if start[:10] <= day_str <= end[:10]:
                results.append(e)
        return results

    def get_day_tasks(self, date_obj) -> list:
        day_str = date_obj.strftime("%Y-%m-%d")
        return getattr(self, "_tasks_by_day", {}).get(day_str, [])

    def update_detail_panel(self):
        table = self.query_one("#calendar-table")
        detail_view = self.query_one("#calendar-detail")
        detail_view.clear()
        self.selected_event = None
        
        selected_date = self.current_date
        selected_time = "all-day"
        
        if self.current_view in ["month", "week"]:
            coord = table.cursor_coordinate
            if coord:
                cell_info = self.cell_map.get((coord.row, coord.column))
                if cell_info:
                    selected_date, selected_time = cell_info
        elif self.current_view == "day":
            row_idx = table.cursor_row
            if row_idx is not None:
                cell_info = self.cell_map.get(row_idx)
                if cell_info:
                    selected_date, selected_time = cell_info
                    
        day_events = self.get_day_events(selected_date)
        day_tasks = self.get_day_tasks(selected_date)
        
        if isinstance(selected_time, int):
            filtered_events = []
            for e in day_events:
                start_time = e.get("start", {}).get("dateTime")
                if start_time:
                    try:
                        h = int(start_time[11:13])
                        if h == selected_time:
                            filtered_events.append(e)
                    except Exception:
                        pass
            day_events = filtered_events
            day_tasks = []
            
        detail_view.write(f"[bold yellow]Calendar Details for {selected_date.strftime('%A, %b %d, %Y')}[/bold yellow]")
        if isinstance(selected_time, int):
            detail_view.write(f"[bold cyan]Hour: {selected_time:02d}:00[/bold cyan]\n")
        else:
            detail_view.write(f"[bold cyan]Scope: All Day / Day View[/bold cyan]\n")
            
        detail_view.write("[bold green]--- Events ({}) ---[/bold green]".format(len(day_events)))
        for idx, e in enumerate(day_events):
            if idx == 0:
                self.selected_event = e
                
            start = e.get("start", {}).get("dateTime", e.get("start", {}).get("date", ""))
            end = e.get("end", {}).get("dateTime", e.get("end", {}).get("date", ""))
            time_str = rich_escape(f"[{start[11:16]}-{end[11:16]}]" if "T" in start else "[All Day]")
            # Escape all user/API-supplied event fields so a "[...]" in a title
            # or description renders literally instead of breaking the markup.
            detail_view.write(f"• [bold green]{time_str}[/bold green] {rich_escape(e.get('summary', '(No Title)'))}")
            # Only show the source calendar when more than one is on screen.
            if self.selected_calendar_ids and len(self.selected_calendar_ids) > 1:
                cal_id = e.get("_calendar", "")
                cal_label = self.calendar_names.get(cal_id, cal_id)
                if cal_label:
                    detail_view.write(f"  [magenta]Calendar:[/magenta] {rich_escape(cal_label)}")
            detail_view.write(f"  [magenta]ID:[/magenta] {rich_escape(e.get('id', ''))}")
            if e.get("organizer", {}).get("email"):
                detail_view.write(f"  [magenta]Organizer:[/magenta] {rich_escape(e.get('organizer', {}).get('email'))}")
            if e.get("location"):
                detail_view.write(f"  [magenta]Location:[/magenta] {rich_escape(e.get('location'))}")
            if e.get("description"):
                detail_view.write(f"  [dim]{rich_escape(e.get('description'))}[/dim]")
            if e.get("hangoutLink"):
                detail_view.write(f"  [blue][link={e.get('hangoutLink')}]Meet Link[/link][/blue]")
            detail_view.write("")

        detail_view.write("\n[bold yellow]--- Google Tasks ({}) ---[/bold yellow]".format(len(day_tasks)))
        for t in day_tasks:
            status = "[bold green]✓ Done[/bold green]" if t.get("status") == "completed" else "[bold red]○ Active[/bold red]"
            detail_view.write(f"• {status} {rich_escape(t.get('title', ''))}")
            if t.get("notes"):
                detail_view.write(f"  [dim]Notes: {rich_escape(t.get('notes'))}[/dim]")
            detail_view.write("")

    def show_freebusy(self, who: str, day_str: str, busy: list):
        """Render free/busy intervals for `who` on `day_str` into the detail panel.

        `busy` is the list of {"start","end"} blocks from GogAPI.calendar_freebusy.
        """
        detail_view = self.query_one("#calendar-detail")
        detail_view.clear()
        detail_view.write(f"[bold yellow]Free/Busy for {rich_escape(who)}[/bold yellow]")
        detail_view.write(f"[bold cyan]{rich_escape(day_str)}[/bold cyan]\n")
        if not busy:
            detail_view.write("[bold green]Free all day — no busy intervals.[/bold green]")
            return
        detail_view.write("[bold red]--- Busy ({}) ---[/bold red]".format(len(busy)))
        for block in busy:
            start = block.get("start", "")
            end = block.get("end", "")
            # Show HH:MM when the interval is within a day; otherwise the raw value.
            s = start[11:16] if "T" in start else start
            en = end[11:16] if "T" in end else end
            detail_view.write(f"• [bold red]{rich_escape(s)} – {rich_escape(en)}[/bold red]")

    async def on_data_table_cell_selected(self, event: DataTable.CellSelected):
        self.update_detail_panel()

    async def on_data_table_row_selected(self, event: DataTable.RowSelected):
        self.update_detail_panel()

    async def on_button_pressed(self, event: Button.Pressed):

        if event.button.id == "cal-prev-btn":
            if self.current_view == "month":
                m = self.current_date.month - 1
                y = self.current_date.year
                if m == 0:
                    m = 12
                    y -= 1
                self.current_date = self.current_date.replace(year=y, month=m, day=1)
            elif self.current_view == "week":
                self.current_date -= timedelta(days=7)
            elif self.current_view == "day":
                self.current_date -= timedelta(days=1)
            self.render_view()
            return
            
        elif event.button.id == "cal-next-btn":
            if self.current_view == "month":
                m = self.current_date.month + 1
                y = self.current_date.year
                if m == 13:
                    m = 1
                    y += 1
                self.current_date = self.current_date.replace(year=y, month=m, day=1)
            elif self.current_view == "week":
                self.current_date += timedelta(days=7)
            elif self.current_view == "day":
                self.current_date += timedelta(days=1)
            self.render_view()
            return
            
        elif event.button.id == "cal-today-btn":
            self.current_date = date.today()
            self.render_view()
            return
            
        elif event.button.id == "cal-view-month":
            self.current_view = "month"
            self.render_view()
            return
        elif event.button.id == "cal-view-week":
            self.current_view = "week"
            self.render_view()
            return
        elif event.button.id == "cal-view-day":
            self.current_view = "day"
            self.render_view()
            return
            
        elif event.button.id == "cal-add-btn":
            self.app.open_calendar_create_dialog()
            return

        elif event.button.id == "cal-pick-btn":
            self.app.open_calendar_picker_dialog(self)
            return

        elif event.button.id == "cal-freebusy-btn":
            self.app.open_freebusy_dialog(self)
            return

        if not self.selected_event:
            self.post_message(StatusNotification("No calendar event selected to action. Select a slot/event first."))
            return
            
        event_id = self.selected_event.get("id")

        if event.button.id == "cal-edit-btn":
            self.app.open_calendar_edit_dialog(self.selected_event)
            return
        elif event.button.id == "cal-del-btn":
            summary = self.selected_event.get("summary", "(No Title)")

            async def do_delete():
                await GogAPI.calendar_delete_event("primary", event_id)
                self.post_message(StatusNotification("Event deleted."))
                await self.refresh_calendar()
            self.app.confirm(f"Delete event “{summary}”?", do_delete)
        elif event.button.id in ["cal-rsvp-yes", "cal-rsvp-no"]:
            resp = "yes" if event.button.id == "cal-rsvp-yes" else "no"
            await GogAPI.calendar_respond_event("primary", event_id, resp)
            self.post_message(StatusNotification(f"RSVP'd {resp} to event."))
            await self.refresh_calendar()


# --- DRIVE TAB ---
class DriveTab(Vertical):
    def compose(self):
        yield Horizontal(
            Label(" Google Drive Files ", classes="view-header"),
            Input(placeholder="Search drive files...", id="drive-search-input"),
            id="drive-header-row"
        )
        yield Horizontal(
            Button("New Folder", variant="success", id="drive-mkdir-btn"),
            Button("View", variant="primary", id="drive-view-btn"),
            Button("Share", variant="primary", id="drive-share-btn"),
            Button("Rename", variant="primary", id="drive-rename-btn"),
            Button("Move", variant="primary", id="drive-move-btn"),
            Button("Download", id="drive-dl-btn"),
            Button("Upload File", id="drive-up-btn"),
            Button("Delete", variant="error", id="drive-del-btn"),
            classes="btn-row"
        )
        yield DataTable(id="drive-table")

    def on_mount(self):
        table = self.query_one("#drive-table")
        table.cursor_type = "row"
        table.add_columns("Name", "Type", "Size", "Owner")

    async def refresh_files(self, query: str = None):
        self.post_message(StatusNotification("Fetching Drive inventory..."))
        table = self.query_one("#drive-table")
        table.clear()
        
        if query:
            files = await GogAPI.drive_search(query)
        else:
            files = await GogAPI.drive_list()
        self.files_data = files
        
        for f in files:
            owner = f.get("owners", [{}])[0].get("displayName", "")
            table.add_row(
                f.get("name", ""),
                f.get("mimeType", "").split(".")[-1],
                human_size(f.get("size")),
                owner,
                key=f.get("id")
            )
        self.post_message(StatusNotification(f"Loaded {len(files)} files."))

    async def on_input_submitted(self, event: Input.Submitted):
        if event.input.id == "drive-search-input":
            await self.refresh_files(event.value)

    async def on_data_table_row_selected(self, event: DataTable.RowSelected):
        # Clicking a file opens it in the browser (Docs/Sheets/Slides open in their
        # editors for view + edit; other files open in the Drive preview).
        file_id = event.row_key.value
        f = next((x for x in getattr(self, "files_data", []) if x.get("id") == file_id), {})
        url = f.get("webViewLink") or f"https://drive.google.com/open?id={file_id}"
        try:
            webbrowser.open(url)
            self.post_message(StatusNotification(f"Opened {f.get('name', 'file')} in browser."))
        except Exception as e:
            self.post_message(StatusNotification(f"Could not open browser: {e}", is_error=True))

    async def on_button_pressed(self, event: Button.Pressed):
        table = self.query_one("#drive-table")
        
        if event.button.id == "drive-mkdir-btn":
            self.app.open_drive_mkdir_dialog()
            return
        elif event.button.id == "drive-up-btn":
            self.app.open_drive_upload_dialog()
            return
            
        if table.cursor_row is None:
            return
            
        selected_row_idx = table.cursor_row
        file_id = table.ordered_rows[selected_row_idx].key.value
        # Resolve the name by ID, not by row index: the visual order can diverge
        # from files_data after a search/partial repopulation.
        file_name = next(
            (f.get("name") for f in getattr(self, "files_data", []) if f.get("id") == file_id), "")
        
        if event.button.id == "drive-share-btn":
            self.app.open_drive_share_dialog(file_id, file_name)
        elif event.button.id == "drive-rename-btn":
            self.app.open_drive_rename_dialog(file_id, file_name)
        elif event.button.id == "drive-move-btn":
            self.app.open_drive_move_dialog(file_id, file_name)
        elif event.button.id == "drive-dl-btn":
            self.app.open_drive_download_dialog(file_id, file_name)
        elif event.button.id == "drive-view-btn":
            self.post_message(StatusNotification(f"Downloading {file_name} for preview..."))
            # Private per-download dir (0700): a fixed /tmp/<name> path is
            # predictable and writable by other local users.
            temp_dir = tempfile.mkdtemp(prefix="gogmail-")
            temp_path = os.path.join(temp_dir, file_name)
            self.app.register_temp_file(temp_path)
            
            async def run_view():
                success, err = await GogAPI.drive_download(file_id, temp_path)
                if success:
                    view_media_file(self.app, temp_path)
                else:
                    self.post_message(StatusNotification(f"Download failed: {err}"))
            
            self.run_worker(run_view())
        elif event.button.id == "drive-del-btn":
            async def do_delete():
                await GogAPI.drive_delete(file_id)
                self.post_message(StatusNotification("Moved file to trash."))
                await self.refresh_files()
            self.app.confirm(f"Delete “{file_name}”?", do_delete)


# --- DRIVE MIME-TYPE TABS (Docs / Sheets / Slides / Forms) ---
class DriveMimeTab(Vertical):
    """Base for tabs that list Drive files of a single MIME type.

    Subclasses set the class attributes below; Docs/Sheets additionally override
    compose() and on_data_table_row_selected() for their viewer/grid panes.
    """
    HEADER = ""
    MIME = ""
    NOUN = "items"            # used in status messages
    LIST_TABLE_ID = ""
    LIST_COLUMNS = ("Name", "ID")
    NEW_BTN_ID = ""
    NEW_LABEL = "New"
    REF_BTN_ID = ""
    BROWSER_BTN_ID = ""
    NEW_DIALOG = ""           # GogMailApp method name to open the create dialog
    OPEN_ON_SELECT = False    # row click opens the file in the browser

    def _header(self):
        return Horizontal(
            Label(self.HEADER, classes="view-header"),
            Horizontal(
                Button(self.NEW_LABEL, variant="success", id=self.NEW_BTN_ID),
                Button("Browser", variant="primary", id=self.BROWSER_BTN_ID),
                Button("Refresh", id=self.REF_BTN_ID),
                classes="header-buttons"
            ),
            classes="view-header-row"
        )

    def compose(self):
        yield self._header()
        yield DataTable(id=self.LIST_TABLE_ID)

    def on_mount(self):
        table = self.query_one(f"#{self.LIST_TABLE_ID}")
        table.cursor_type = "row"
        table.add_columns(*self.LIST_COLUMNS)

    async def refresh_list(self):
        self.post_message(StatusNotification(f"Searching for {self.NOUN}..."))
        table = self.query_one(f"#{self.LIST_TABLE_ID}")
        table.clear()

        self.files_data = await GogAPI.drive_search(f"mimeType = '{self.MIME}'")
        for f in self.files_data:
            table.add_row(f.get("name", ""), f.get("id", ""), key=f.get("id"))
        self.post_message(StatusNotification(f"{self.NOUN} updated."))

    def open_in_browser(self, file_id: str):
        f = next((x for x in getattr(self, "files_data", []) if x.get("id") == file_id), {})
        url = f.get("webViewLink") or f"https://drive.google.com/open?id={file_id}"
        try:
            webbrowser.open(url)
            self.post_message(StatusNotification(f"Opened {f.get('name', 'file')} in browser."))
        except Exception as e:
            self.post_message(StatusNotification(f"Could not open browser: {e}", is_error=True))

    async def on_button_pressed(self, event: Button.Pressed):
        if event.button.id == self.NEW_BTN_ID:
            getattr(self.app, self.NEW_DIALOG)()
        elif event.button.id == self.REF_BTN_ID:
            await self.refresh_list()
        elif event.button.id == self.BROWSER_BTN_ID:
            table = self.query_one(f"#{self.LIST_TABLE_ID}")
            if table.cursor_row is None:
                self.post_message(StatusNotification(f"Select one of the {self.NOUN} first.", is_error=True))
                return
            self.open_in_browser(table.ordered_rows[table.cursor_row].key.value)

    async def on_data_table_row_selected(self, event: DataTable.RowSelected):
        # Slides/Forms have no in-TUI preview: clicking opens the web editor.
        if self.OPEN_ON_SELECT and event.data_table.id == self.LIST_TABLE_ID:
            self.open_in_browser(event.row_key.value)


class DocsTab(DriveMimeTab):
    HEADER = " Google Docs "
    MIME = "application/vnd.google-apps.document"
    NOUN = "Google Docs"
    LIST_TABLE_ID = "docs-table"
    LIST_COLUMNS = ("Document Name", "ID")
    NEW_BTN_ID = "doc-new-btn"
    NEW_LABEL = "New Doc"
    REF_BTN_ID = "doc-ref-btn"
    BROWSER_BTN_ID = "doc-browser-btn"
    NEW_DIALOG = "open_doc_create_dialog"

    def compose(self):
        yield self._header()
        yield Horizontal(
            DataTable(id="docs-table"),
            RichLog(id="doc-viewer", highlight=True, markup=True, wrap=True, min_width=0),
            id="docs-content-row"
        )

    async def on_data_table_row_selected(self, event: DataTable.RowSelected):
        doc_id = event.row_key.value
        self.post_message(StatusNotification(f"Reading Doc {doc_id}..."))

        viewer = self.query_one("#doc-viewer")
        viewer.clear()
        viewer.write("[dim]Reading document…[/dim]")
        text = await GogAPI.docs_cat(doc_id)
        viewer.clear()
        if text.strip():
            # Text(): document content must render literally — markdown links
            # like [name](url) are Rich markup and raised MarkupError, leaving
            # the viewer blank.
            viewer.write(Text(text))
        else:
            viewer.write("[dim](Empty document.)[/dim]")
        self.post_message(StatusNotification("Doc read successfully."))


class SheetsTab(DriveMimeTab):
    HEADER = " Google Sheets "
    MIME = "application/vnd.google-apps.spreadsheet"
    NOUN = "Spreadsheets"
    LIST_TABLE_ID = "sheets-list-table"
    LIST_COLUMNS = ("Sheet Name", "ID")
    NEW_BTN_ID = "sheet-new-btn"
    NEW_LABEL = "New Sheet"
    REF_BTN_ID = "sheet-ref-btn"
    BROWSER_BTN_ID = "sheet-browser-btn"
    NEW_DIALOG = "open_sheet_create_dialog"
    GRID_RANGE = "A1:G20"

    # Tracks the spreadsheet whose values are currently shown in the grid, so
    # the Edit Cell / Append Row buttons know what to mutate and reload.
    current_spreadsheet_id = None

    def _header(self):
        return Horizontal(
            Label(self.HEADER, classes="view-header"),
            Horizontal(
                Button(self.NEW_LABEL, variant="success", id=self.NEW_BTN_ID),
                Button("Edit Cell", variant="primary", id="sheet-edit-cell-btn"),
                Button("Append Row", variant="primary", id="sheet-append-row-btn"),
                Button("Browser", variant="primary", id=self.BROWSER_BTN_ID),
                Button("Refresh", id=self.REF_BTN_ID),
                classes="header-buttons"
            ),
            classes="view-header-row"
        )

    def compose(self):
        yield self._header()
        yield Horizontal(
            DataTable(id="sheets-list-table"),
            DataTable(id="sheet-grid"),
            id="sheets-content-row"
        )

    def on_mount(self):
        super().on_mount()
        self.query_one("#sheet-grid").cursor_type = "cell"

    async def reload_grid(self):
        """(Re)populate the grid from the currently-tracked spreadsheet."""
        spreadsheet_id = self.current_spreadsheet_id
        if not spreadsheet_id:
            return
        self.post_message(StatusNotification(f"Fetching sheet values for {spreadsheet_id}..."))

        res = await GogAPI.sheets_get(spreadsheet_id, self.GRID_RANGE)

        grid = self.query_one("#sheet-grid")
        # columns=True: stale headers from a previously-viewed (wider) sheet
        # would otherwise misalign the new data.
        grid.clear(columns=True)

        values = res.get("values", [])
        if not values:
            grid.add_columns("Empty Sheet")
            grid.add_row(f"No values in range {self.GRID_RANGE}.")
        else:
            max_cols = max(len(row) for row in values)
            grid.add_columns(*[chr(65 + i) for i in range(max_cols)])
            for row in values:
                grid.add_row(*(row + [""] * (max_cols - len(row))))

        self.post_message(StatusNotification("Sheet data loaded."))

    async def on_data_table_row_selected(self, event: DataTable.RowSelected):
        if event.data_table.id != "sheets-list-table":
            return
        self.current_spreadsheet_id = event.row_key.value
        await self.reload_grid()

    async def on_button_pressed(self, event: Button.Pressed):
        if event.button.id == "sheet-edit-cell-btn":
            if not self.current_spreadsheet_id:
                self.post_message(StatusNotification("Open a spreadsheet first.", is_error=True))
                return
            grid = self.query_one("#sheet-grid")
            coord = grid.cursor_coordinate  # Coordinate(row, column)
            cell = f"{chr(65 + coord.column)}{coord.row + 1}"
            self.app.open_sheet_edit_cell_dialog(self.current_spreadsheet_id, cell)
        elif event.button.id == "sheet-append-row-btn":
            if not self.current_spreadsheet_id:
                self.post_message(StatusNotification("Open a spreadsheet first.", is_error=True))
                return
            self.app.open_sheet_append_dialog(self.current_spreadsheet_id)
        else:
            await super().on_button_pressed(event)


class SlidesTab(DriveMimeTab):
    HEADER = " Google Slides Presentations "
    MIME = "application/vnd.google-apps.presentation"
    NOUN = "Presentations"
    LIST_TABLE_ID = "slides-table"
    LIST_COLUMNS = ("Presentation Name", "ID")
    NEW_BTN_ID = "slide-new-btn"
    NEW_LABEL = "New Presentation"
    REF_BTN_ID = "slide-ref-btn"
    BROWSER_BTN_ID = "slide-browser-btn"
    NEW_DIALOG = "open_slide_create_dialog"
    OPEN_ON_SELECT = True


class FormsTab(DriveMimeTab):
    HEADER = " Google Forms "
    MIME = "application/vnd.google-apps.form"
    NOUN = "Forms"
    LIST_TABLE_ID = "forms-table"
    LIST_COLUMNS = ("Form Name", "ID")
    NEW_BTN_ID = "form-new-btn"
    NEW_LABEL = "New Form"
    REF_BTN_ID = "form-ref-btn"
    BROWSER_BTN_ID = "form-browser-btn"
    NEW_DIALOG = "open_form_create_dialog"
    OPEN_ON_SELECT = True


# --- MEET TAB ---
class MeetTab(Vertical):
    def compose(self):
        yield Horizontal(
            Label(" Google Meet ", classes="view-header"),
            classes="view-header-row"
        )
        yield Vertical(
            Label("Instantly generate a Google Meet video conference space.", classes="description"),
            Button("Create Meeting Space", variant="success", id="meet-create-btn"),
            RichLog(id="meet-output", highlight=True, markup=True, wrap=True, min_width=0),
            id="meet-container"
        )

    async def on_button_pressed(self, event: Button.Pressed):
        if event.button.id == "meet-create-btn":
            self.post_message(StatusNotification("Creating Meet space..."))
            success, link = await GogAPI.meet_create()
            log = self.query_one("#meet-output")
            log.clear()
            if success:
                self.app.copy_to_clipboard(link)
                log.write("[bold green]Meeting created.[/bold green]\n")
                log.write(f"[bold][link={link}]{link}[/link][/bold]\n")
                log.write("[dim]Copied to clipboard.[/dim]")
                self.post_message(StatusNotification("Meet link copied to clipboard."))
            else:
                log.write(f"[red]Failed to create Meet space: {rich_escape(link)}[/red]")
                self.post_message(StatusNotification("Meet creation failed.", is_error=True))


# --- ZOOM TAB ---
class ZoomTab(Vertical):
    def compose(self):
        yield Horizontal(
            Label(" Zoom Auth & Connection ", classes="view-header"),
            classes="view-header-row"
        )
        yield Vertical(
            Label("Create an instant Zoom meeting, or validate your Server-to-Server OAuth credentials.", classes="description"),
            Horizontal(
                Button("Create Meeting", variant="success", id="zoom-create-btn"),
                Button("Run Zoom Doctor", id="zoom-doctor-btn"),
                classes="btn-row"
            ),
            RichLog(id="zoom-output", highlight=True, markup=True, wrap=True, min_width=0),
            id="zoom-container"
        )

    async def on_button_pressed(self, event: Button.Pressed):
        if event.button.id == "zoom-doctor-btn":
            self.post_message(StatusNotification("Validating Zoom configuration..."))
            log = self.query_one("#zoom-output")
            log.clear()
            log.write("[dim]Validating Zoom configuration…[/dim]")
            res = await GogAPI.zoom_doctor()
            log.clear()
            log.write(rich_escape(res or "(no output)"))
            self.post_message(StatusNotification("Zoom Doctor completed."))
        elif event.button.id == "zoom-create-btn":
            self.post_message(StatusNotification("Creating Zoom meeting..."))
            log = self.query_one("#zoom-output")
            log.clear()
            log.write("[dim]Creating Zoom meeting…[/dim]")
            success, data = await ZoomAPI.create_meeting()
            log.clear()
            if not success:
                log.write(f"[red]Failed to create meeting: {rich_escape(str(data))}[/red]")
                self.post_message(StatusNotification("Zoom meeting creation failed.", is_error=True))
                return
            join_url = data.get("join_url", "")
            start_url = data.get("start_url", "")
            self.app.copy_to_clipboard(join_url)
            log.write("[bold green]Zoom meeting created.[/bold green]\n")
            if join_url:
                log.write(f"[bold]Join:[/bold] [link={join_url}]{rich_escape(join_url)}[/link]")
            passcode = data.get("password")
            if passcode:
                log.write(f"[bold]Passcode:[/bold] {rich_escape(str(passcode))}")
            log.write("\n[dim]Join link copied to clipboard. Opening your Zoom client…[/dim]")
            # start_url launches the host's desktop Zoom client (or web) to begin
            # the meeting; join_url is the one to share with participants.
            try:
                if start_url:
                    webbrowser.open(start_url)
            except Exception as e:
                self.post_message(StatusNotification(f"Could not open Zoom client: {e}", is_error=True))
            self.post_message(StatusNotification("Zoom meeting created — opening client."))


# --- CONTACTS & PEOPLE TAB ---
class ContactsTab(Vertical):
    def compose(self):
        yield Horizontal(
            Label(" Google Contacts & Directory ", classes="view-header"),
            Input(placeholder="Search contacts by name, email...", id="contacts-search-input"),
            id="contacts-header-row"
        )
        yield Horizontal(
            Button("Email Contact", variant="success", id="contacts-email-btn"),
            Button("New Contact", variant="primary", id="contacts-new-btn"),
            Button("Edit Contact", id="contacts-edit-btn"),
            Button("Delete Contact", variant="error", id="contacts-del-btn"),
            Button("Refresh", id="contacts-ref-btn"),
            classes="btn-row"
        )
        yield Horizontal(
            DataTable(id="contacts-table"),
            RichLog(id="contact-detail", highlight=True, markup=True, wrap=True, min_width=0),
            id="contacts-content-row"
        )

    def on_mount(self):
        table = self.query_one("#contacts-table")
        table.cursor_type = "row"
        table.add_columns("Name", "Email", "Phone")

    async def refresh_contacts(self, query: str = None):
        self.post_message(StatusNotification("Loading contacts..."))
        table = self.query_one("#contacts-table")
        table.clear()
        
        if query:
            contacts = await GogAPI.contacts_search(query)
        else:
            contacts = await GogAPI.contacts_list()
        self.contacts_data = contacts
        
        for c in contacts:
            name = c.get("name") or c.get("names", [{}])[0].get("displayName", "(No Name)")
            email = c.get("email") or c.get("emailAddresses", [{}])[0].get("value", "")
            phone = c.get("phone") or c.get("phoneNumbers", [{}])[0].get("value", "")
            resource = c.get("resource") or c.get("resourceName") or ""
            table.add_row(name, email, phone, key=resource)
            
        self.post_message(StatusNotification(f"Loaded {len(contacts)} contacts."))

    async def on_input_submitted(self, event: Input.Submitted):
        if event.input.id == "contacts-search-input":
            await self.refresh_contacts(event.value)

    def _selected_contact(self) -> dict:
        table = self.query_one("#contacts-table")
        if table.cursor_row is None or not getattr(self, "contacts_data", None):
            return {}
        res = table.ordered_rows[table.cursor_row].key.value
        return next(
            (c for c in self.contacts_data if (c.get("resource") or c.get("resourceName")) == res),
            {},
        )

    @staticmethod
    def _contact_phone(contact: dict) -> str:
        """Primary phone for a contact dict (handles flat and nested shapes)."""
        if contact.get("phone"):
            return contact["phone"]
        for p in contact.get("phoneNumbers", []):
            if p.get("value"):
                return p["value"]
        return ""

    async def on_button_pressed(self, event: Button.Pressed):
        if event.button.id == "contacts-ref-btn":
            await self.refresh_contacts()
            return
        if event.button.id == "contacts-new-btn":
            self.app.open_contact_create_dialog()
            return
        if event.button.id == "contacts-email-btn":
            contact = self._selected_contact()
            if not contact:
                self.post_message(StatusNotification("Select a contact first.", is_error=True))
                return
            email = GogAPI.contact_email(contact)
            if not email:
                self.post_message(StatusNotification("That contact has no email address.", is_error=True))
                return
            name = GogAPI.contact_name(contact)
            self.app.open_compose_dialog(to=f"{name} <{email}>" if name else email)
            return
        if event.button.id == "contacts-edit-btn":
            contact = self._selected_contact()
            if not contact:
                self.post_message(StatusNotification("Select a contact first.", is_error=True))
                return
            self.app.open_contact_edit_dialog(contact)
            return
        if event.button.id == "contacts-del-btn":
            contact = self._selected_contact()
            if not contact:
                self.post_message(StatusNotification("Select a contact first.", is_error=True))
                return
            resource = contact.get("resource") or contact.get("resourceName") or ""
            name = GogAPI.contact_name(contact) or "(No Name)"

            async def do_delete():
                await GogAPI.contacts_delete(resource)
                self.post_message(StatusNotification("Contact deleted."))
                await self.refresh_contacts()
            self.app.confirm(f"Delete contact “{name}”?", do_delete)
            return

    async def on_data_table_row_selected(self, event: DataTable.RowSelected):
        res_name = event.row_key.value
        log = self.query_one("#contact-detail")
        log.clear()
        
        for c in self.contacts_data:
            c_res = c.get("resource") or c.get("resourceName") or ""
            if c_res == res_name:
                name = c.get("name") or c.get("names", [{}])[0].get("displayName", "")
                log.write(f"[bold magenta]Name:[/bold magenta] {name}")
                
                emails = []
                if "email" in c:
                    if c["email"]:
                        emails.append(c["email"])
                else:
                    emails = [e.get("value") for e in c.get("emailAddresses", []) if e.get("value")]
                log.write(f"[bold magenta]Emails:[/bold magenta] {', '.join(emails)}")
                
                phones = []
                if "phone" in c:
                    if c["phone"]:
                        phones.append(c["phone"])
                else:
                    phones = [p.get("value") for p in c.get("phoneNumbers", []) if p.get("value")]
                log.write(f"[bold magenta]Phones:[/bold magenta] {', '.join(phones)}")
                
                bday = c.get("birthday")
                if bday:
                    log.write(f"[bold magenta]Birthday:[/bold magenta] {bday}")
                
                orgs = []
                if "organization" in c and c["organization"]:
                    orgs.append(c["organization"])
                else:
                    orgs = [o.get("name", "") + (" - " + o.get("title", "") if o.get("title") else "") 
                            for o in c.get("organizations", []) if o.get("name")]
                if orgs:
                    log.write(f"[bold magenta]Organization:[/bold magenta] {', '.join(orgs)}")
                break


# --- TASKS TAB ---
class TasksTab(Vertical):
    def compose(self):
        yield Horizontal(
            Label(" Google Tasks ", classes="view-header"),
            Horizontal(
                Button("New Task List", id="tasklist-add-btn"),
                Button("New Task", variant="success", id="task-add-btn"),
                Button("Edit Task", id="task-edit-btn"),
                Button("Delete Task", variant="error", id="task-del-btn"),
                Button("Clear Completed", variant="warning", id="task-clear-btn"),
                classes="header-buttons"
            ),
            classes="view-header-row"
        )
        yield Horizontal(
            DataTable(id="tasklists-table"),
            DataTable(id="tasks-table"),
            id="tasks-content-split"
        )

    def on_mount(self):
        tl_table = self.query_one("#tasklists-table")
        tl_table.cursor_type = "row"
        tl_table.add_columns("Task Lists")
        
        t_table = self.query_one("#tasks-table")
        t_table.cursor_type = "row"
        t_table.add_columns("Completed", "Task Title", "Due", "Notes")
        
        self.selected_list_id = None

    async def refresh_tasklists(self):
        self.post_message(StatusNotification("Fetching task lists..."))
        tl_table = self.query_one("#tasklists-table")
        tl_table.clear()
        
        lists = await GogAPI.tasks_lists()
        self.tasklists_data = lists
        
        for l in lists:
            tl_table.add_row(l.get("title", ""), key=l.get("id"))
            
        if lists and not self.selected_list_id:
            self.selected_list_id = lists[0].get("id")
            self.run_worker(self.refresh_tasks())
            
        self.post_message(StatusNotification("Task lists loaded."))

    async def refresh_tasks(self):
        if not self.selected_list_id:
            return
            
        self.post_message(StatusNotification("Loading tasks..."))
        t_table = self.query_one("#tasks-table")
        t_table.clear()
        
        tasks = await GogAPI.tasks_list(self.selected_list_id)
        self.tasks_data = tasks
        
        for t in tasks:
            status = "[x] Yes" if t.get("status") == "completed" else "[ ] No"
            # Google Tasks returns `due` as an RFC3339 timestamp; show just the date.
            due = (t.get("due") or "")[:10]
            t_table.add_row(status, t.get("title", ""), due, t.get("notes", ""), key=t.get("id"))
            
        self.post_message(StatusNotification(f"Loaded {len(tasks)} tasks."))

    async def on_data_table_row_selected(self, event: DataTable.RowSelected):
        if event.data_table.id == "tasklists-table":
            self.selected_list_id = event.row_key.value
            await self.refresh_tasks()
        elif event.data_table.id == "tasks-table" and hasattr(self, "tasks_data"):
            # Toggle task status (complete/incomplete) when double-clicked or selected
            task_id = event.row_key.value
            selected_task = None
            for t in self.tasks_data:
                if t.get("id") == task_id:
                    selected_task = t
                    break
            if selected_task:
                if selected_task.get("status") == "completed":
                    await GogAPI.tasks_undo(self.selected_list_id, task_id)
                    self.post_message(StatusNotification("Task marked as active."))
                else:
                    await GogAPI.tasks_done(self.selected_list_id, task_id)
                    self.post_message(StatusNotification("Task marked as completed."))
                await self.refresh_tasks()

    def _selected_task(self) -> dict | None:
        """Return the task dict under the tasks-table cursor, or None."""
        t_table = self.query_one("#tasks-table")
        if t_table.cursor_row is None:
            return None
        task_id = t_table.ordered_rows[t_table.cursor_row].key.value
        return next((t for t in getattr(self, "tasks_data", []) if t.get("id") == task_id), None)

    async def on_button_pressed(self, event: Button.Pressed):
        if event.button.id == "tasklist-add-btn":
            self.app.open_tasklist_create_dialog()
        elif event.button.id == "task-add-btn" and self.selected_list_id:
            self.app.open_task_create_dialog(self.selected_list_id)
        elif event.button.id == "task-edit-btn" and self.selected_list_id:
            task = self._selected_task()
            if task:
                self.app.open_task_edit_dialog(self.selected_list_id, task)
        elif event.button.id == "task-clear-btn" and self.selected_list_id:
            async def do_clear():
                await GogAPI.tasks_clear_completed(self.selected_list_id)
                self.post_message(StatusNotification("Completed tasks cleared."))
                await self.refresh_tasks()
            self.app.confirm("Clear all completed tasks from this list?", do_clear, "Clear")
        elif event.button.id == "task-del-btn" and self.selected_list_id:
            task = self._selected_task()
            if task:
                task_id = task.get("id")
                title = task.get("title", "")

                async def do_delete():
                    await GogAPI.tasks_delete(self.selected_list_id, task_id)
                    self.post_message(StatusNotification("Task deleted."))
                    await self.refresh_tasks()
                self.app.confirm(f"Delete task “{title}”?", do_delete)


# --- CHAT TAB ---
class ChatTab(Vertical):
    def compose(self):
        yield Horizontal(
            Label(" Google Chat Spaces ", classes="view-header"),
            classes="view-header-row"
        )
        yield Horizontal(
            DataTable(id="chat-spaces-table"),
            Vertical(
                RichLog(id="chat-history", highlight=True, markup=True, wrap=True, min_width=0),
                Input(placeholder="Type your message and press Enter...", id="chat-input"),
                id="chat-message-pane"
            ),
            id="chat-split"
        )

    def on_mount(self):
        table = self.query_one("#chat-spaces-table")
        table.cursor_type = "row"
        table.add_columns("Chat Space")
        
        self.selected_space_id = None

    async def refresh_spaces(self):
        self.post_message(StatusNotification("Loading chat spaces..."))
        table = self.query_one("#chat-spaces-table")
        table.clear()
        
        spaces = await GogAPI.chat_spaces()
        self.spaces_data = spaces

        async def label_for(s) -> str:
            # gog returns the space id as `resource` (spaces/AAA); DMs have no
            # displayName, so resolve the other participant's real name from
            # the DM's recent senders (People API, cached).
            space_id = s.get("name") or s.get("resource") or ""
            if s.get("displayName"):
                return s["displayName"]
            if s.get("type") == "DIRECT_MESSAGE":
                name = await GogAPI.chat_dm_label(space_id)
                if name:
                    return name
                return f"DM {space_id.split('/')[-1]}"
            return space_id.split("/")[-1]

        labels = await asyncio.gather(*(label_for(s) for s in spaces))
        for s, label in zip(spaces, labels):
            table.add_row(label, key=s.get("name") or s.get("resource") or "")
            
        self.post_message(StatusNotification("Spaces loaded."))

    async def refresh_messages(self):
        if not self.selected_space_id:
            return
        log = self.query_one("#chat-history")
        log.clear()
        
        messages = await GogAPI.chat_messages(self.selected_space_id)
        for m in messages:
            # gog returns `sender` as a bare "users/<id>" string; resolve it to
            # a real name (cached). Escape everything written to the log.
            raw = m.get("sender")
            if isinstance(raw, dict):
                sender = raw.get("displayName") or raw.get("name", "System")
            else:
                sender = raw or "System"
            if sender.startswith("users/"):
                name, _ = await GogAPI.person_info(sender)
                sender = name or sender.split("/")[-1]
            sender = rich_escape(sender)
            text = rich_escape(m.get("text", ""))
            time_str = m.get("createTime", "")[:16].replace("T", " ")
            log.write(f"[dim]{time_str}[/dim] [bold cyan]{sender}:[/bold cyan] {text}")

    async def on_data_table_row_selected(self, event: DataTable.RowSelected):
        self.selected_space_id = event.row_key.value
        await self.refresh_messages()

    async def on_input_submitted(self, event: Input.Submitted):
        if event.input.id == "chat-input" and self.selected_space_id:
            msg_text = event.value
            if not msg_text:
                return
            success = await GogAPI.chat_send_message(self.selected_space_id, msg_text)
            if success:
                event.input.value = ""
                await self.refresh_messages()
            else:
                self.post_message(StatusNotification("Failed to send message."))


# --- KEEP TAB ---
class KeepTab(Vertical):
    """Google Keep notes: a list of notes (title + snippet) with a detail pane."""
    def compose(self):
        yield Horizontal(
            Label(" Google Keep ", classes="view-header"),
            Horizontal(
                Button("New Note", variant="success", id="keep-new-btn"),
                Button("Delete Note", variant="error", id="keep-del-btn"),
                Button("Refresh", id="keep-ref-btn"),
                classes="header-buttons"
            ),
            classes="view-header-row"
        )
        yield Horizontal(
            DataTable(id="keep-table"),
            RichLog(id="keep-detail", highlight=True, markup=True, wrap=True, min_width=0),
            id="keep-content-row"
        )

    def on_mount(self):
        table = self.query_one("#keep-table")
        table.cursor_type = "row"
        table.add_columns("Title", "Snippet")

    @staticmethod
    def _note_text(note: dict) -> str:
        """Body text of a note (handles flat `text`/`body` and nested shapes)."""
        return note.get("text") or note.get("body") or note.get("textContent") or ""

    async def refresh_notes(self):
        self.post_message(StatusNotification("Loading notes..."))
        table = self.query_one("#keep-table")
        table.clear()

        notes = await GogAPI.keep_list()
        self.notes_data = notes

        for n in notes:
            note_id = n.get("id") or n.get("name") or ""
            title = n.get("title") or "(Untitled)"
            snippet = self._note_text(n).replace("\n", " ")
            if len(snippet) > 60:
                snippet = snippet[:57] + "..."
            table.add_row(title, snippet, key=note_id)

        self.post_message(StatusNotification(f"Loaded {len(notes)} notes."))

    def _selected_note(self) -> dict:
        table = self.query_one("#keep-table")
        if table.cursor_row is None or not getattr(self, "notes_data", None):
            return {}
        note_id = table.ordered_rows[table.cursor_row].key.value
        return next(
            (n for n in self.notes_data if (n.get("id") or n.get("name")) == note_id),
            {},
        )

    async def on_button_pressed(self, event: Button.Pressed):
        if event.button.id == "keep-ref-btn":
            await self.refresh_notes()
        elif event.button.id == "keep-new-btn":
            self.app.open_keep_create_dialog()
        elif event.button.id == "keep-del-btn":
            note = self._selected_note()
            if not note:
                self.post_message(StatusNotification("Select a note first.", is_error=True))
                return
            note_id = note.get("id") or note.get("name") or ""
            title = note.get("title") or "(Untitled)"

            async def do_delete():
                await GogAPI.keep_delete(note_id)
                self.post_message(StatusNotification("Note deleted."))
                await self.refresh_notes()
            self.app.confirm(f"Delete note “{title}”?", do_delete)

    async def on_data_table_row_selected(self, event: DataTable.RowSelected):
        note = self._selected_note()
        log = self.query_one("#keep-detail")
        log.clear()
        if not note:
            return
        title = rich_escape(note.get("title") or "(Untitled)")
        log.write(f"[bold magenta]{title}[/bold magenta]\n")
        body = self._note_text(note)
        if body:
            log.write(rich_escape(body))
        for item in note.get("items") or note.get("listItems") or []:
            if isinstance(item, dict):
                checked = item.get("checked")
                mark = "[x]" if checked else "[ ]"
                text = rich_escape(item.get("text", ""))
                log.write(f"{mark} {text}")
        note_id = note.get("id") or note.get("name") or ""
        if note_id:
            log.write(f"\n[dim]ID: {rich_escape(note_id)}[/dim]")
class GroupsTab(Vertical):
    """Google Groups: groups on the left, the selected group's members on the
    right. Groups is an Admin API and may require domain-wide delegation; any
    permission error surfaces via the error sink."""

    def compose(self):
        yield Horizontal(
            Label(" Google Groups ", classes="view-header"),
            classes="view-header-row"
        )
        yield Horizontal(
            DataTable(id="groups-table"),
            Vertical(
                RichLog(id="groups-members", highlight=True, markup=True, wrap=True, min_width=0),
                id="groups-members-pane"
            ),
            id="groups-split"
        )

    def on_mount(self):
        table = self.query_one("#groups-table")
        table.cursor_type = "row"
        table.add_columns("Group", "Email")
        self.selected_group_email = None

    async def refresh_groups(self):
        self.post_message(StatusNotification("Loading groups..."))
        table = self.query_one("#groups-table")
        table.clear()
        self.query_one("#groups-members").clear()
        self.selected_group_email = None

        groups = await GogAPI.groups_list()
        self.groups_data = groups
        for g in groups:
            email = g.get("email") or g.get("groupKey", {}).get("id", "")
            name = g.get("name") or g.get("displayName") or email
            table.add_row(name, email, key=email)

        self.post_message(StatusNotification("Groups loaded."))

    async def refresh_members(self):
        if not self.selected_group_email:
            return
        log = self.query_one("#groups-members")
        log.clear()
        # Escape every API string written to the RichLog (markup is enabled).
        log.write(f"[bold]Members of {rich_escape(self.selected_group_email)}[/bold]\n")

        members = await GogAPI.group_members(self.selected_group_email)
        if not members:
            log.write("[dim]No members to show.[/dim]")
            return
        for m in members:
            email = rich_escape(m.get("email") or m.get("memberKey", {}).get("id", ""))
            role = rich_escape(m.get("role", ""))
            log.write(f"[cyan]{email}[/cyan] [dim]{role}[/dim]")

    async def on_data_table_row_selected(self, event: DataTable.RowSelected):
        self.selected_group_email = event.row_key.value
        await self.refresh_members()


# --- NICHE SERVICE TABS (read-only) ---
# Photos / YouTube / Classroom / Sites are niche Workspace APIs that frequently
# need extra OAuth scopes (or the API enabled in the project). Any permission
# error surfaces via the gog error sink, so these tabs are correct wiring even
# when the account can't read the service. Each is a read-only list with a
# detail pane, modelled on GroupsTab. All API strings written to the RichLog are
# escaped with rich_escape (markup is enabled).
class PhotosTab(Vertical):
    """Google Photos: app-created media items on the left, item detail on the
    right. The Photos Library API only exposes media this app created."""

    def compose(self):
        yield Horizontal(
            Label(" Google Photos ", classes="view-header"),
            Horizontal(
                Button("Refresh", id="photos-ref-btn"),
                classes="header-buttons"
            ),
            classes="view-header-row"
        )
        yield Horizontal(
            DataTable(id="photos-table"),
            RichLog(id="photos-detail", highlight=True, markup=True, wrap=True, min_width=0),
            id="photos-split"
        )

    def on_mount(self):
        table = self.query_one("#photos-table")
        table.cursor_type = "row"
        table.add_columns("Filename", "Type", "Created")

    async def refresh_photos(self):
        self.post_message(StatusNotification("Loading photos..."))
        table = self.query_one("#photos-table")
        table.clear()
        self.query_one("#photos-detail").clear()

        items = await GogAPI.photos_list()
        self.photos_data = items
        for it in items:
            item_id = it.get("id") or ""
            filename = it.get("filename") or "(unnamed)"
            mime = it.get("mimeType") or ""
            created = (it.get("mediaMetadata") or {}).get("creationTime") or ""
            table.add_row(filename, mime, created, key=item_id)

        self.post_message(StatusNotification(f"Loaded {len(items)} photos."))

    def _selected_item(self) -> dict:
        table = self.query_one("#photos-table")
        if table.cursor_row is None or not getattr(self, "photos_data", None):
            return {}
        item_id = table.ordered_rows[table.cursor_row].key.value
        return next((it for it in self.photos_data if it.get("id") == item_id), {})

    async def on_button_pressed(self, event: Button.Pressed):
        if event.button.id == "photos-ref-btn":
            await self.refresh_photos()

    async def on_data_table_row_selected(self, event: DataTable.RowSelected):
        item = self._selected_item()
        log = self.query_one("#photos-detail")
        log.clear()
        if not item:
            return
        log.write(f"[bold magenta]{rich_escape(item.get('filename') or '(unnamed)')}[/bold magenta]\n")
        meta = item.get("mediaMetadata") or {}
        for label, value in (
            ("Type", item.get("mimeType")),
            ("Created", meta.get("creationTime")),
            ("Dimensions", f"{meta.get('width')}x{meta.get('height')}"
                if meta.get("width") and meta.get("height") else None),
            ("Description", item.get("description")),
        ):
            if value:
                log.write(f"[cyan]{label}:[/cyan] {rich_escape(str(value))}")
        url = item.get("productUrl") or item.get("baseUrl")
        if url:
            log.write(f"\n[blue underline]{rich_escape(url)}[/blue underline]")
        item_id = item.get("id") or ""
        if item_id:
            log.write(f"\n[dim]ID: {rich_escape(item_id)}[/dim]")


class YouTubeTab(Vertical):
    """YouTube: the authenticated user's playlists on the left, playlist detail
    on the right (via the YouTube Data API)."""

    def compose(self):
        yield Horizontal(
            Label(" YouTube ", classes="view-header"),
            Horizontal(
                Button("Refresh", id="youtube-ref-btn"),
                classes="header-buttons"
            ),
            classes="view-header-row"
        )
        yield Horizontal(
            DataTable(id="youtube-table"),
            RichLog(id="youtube-detail", highlight=True, markup=True, wrap=True, min_width=0),
            id="youtube-split"
        )

    def on_mount(self):
        table = self.query_one("#youtube-table")
        table.cursor_type = "row"
        table.add_columns("Playlist", "Videos", "Privacy")

    async def refresh_youtube(self):
        self.post_message(StatusNotification("Loading playlists..."))
        table = self.query_one("#youtube-table")
        table.clear()
        self.query_one("#youtube-detail").clear()

        items = await GogAPI.youtube_list()
        self.youtube_data = items
        for it in items:
            pl_id = it.get("id") or ""
            snippet = it.get("snippet") or {}
            title = snippet.get("title") or "(untitled)"
            count = str((it.get("contentDetails") or {}).get("itemCount", ""))
            privacy = (it.get("status") or {}).get("privacyStatus") or ""
            table.add_row(title, count, privacy, key=pl_id)

        self.post_message(StatusNotification(f"Loaded {len(items)} playlists."))

    def _selected_item(self) -> dict:
        table = self.query_one("#youtube-table")
        if table.cursor_row is None or not getattr(self, "youtube_data", None):
            return {}
        pl_id = table.ordered_rows[table.cursor_row].key.value
        return next((it for it in self.youtube_data if it.get("id") == pl_id), {})

    async def on_button_pressed(self, event: Button.Pressed):
        if event.button.id == "youtube-ref-btn":
            await self.refresh_youtube()

    async def on_data_table_row_selected(self, event: DataTable.RowSelected):
        item = self._selected_item()
        log = self.query_one("#youtube-detail")
        log.clear()
        if not item:
            return
        snippet = item.get("snippet") or {}
        log.write(f"[bold magenta]{rich_escape(snippet.get('title') or '(untitled)')}[/bold magenta]\n")
        for label, value in (
            ("Videos", (item.get("contentDetails") or {}).get("itemCount")),
            ("Privacy", (item.get("status") or {}).get("privacyStatus")),
            ("Published", snippet.get("publishedAt")),
            ("Channel", snippet.get("channelTitle")),
            ("Description", snippet.get("description")),
        ):
            if value not in (None, ""):
                log.write(f"[cyan]{label}:[/cyan] {rich_escape(str(value))}")
        pl_id = item.get("id") or ""
        if pl_id:
            log.write(f"\n[dim]ID: {rich_escape(pl_id)}[/dim]")


class ClassroomTab(Vertical):
    """Google Classroom: courses on the left, course detail on the right."""

    def compose(self):
        yield Horizontal(
            Label(" Google Classroom ", classes="view-header"),
            Horizontal(
                Button("Refresh", id="classroom-ref-btn"),
                classes="header-buttons"
            ),
            classes="view-header-row"
        )
        yield Horizontal(
            DataTable(id="classroom-table"),
            RichLog(id="classroom-detail", highlight=True, markup=True, wrap=True, min_width=0),
            id="classroom-split"
        )

    def on_mount(self):
        table = self.query_one("#classroom-table")
        table.cursor_type = "row"
        table.add_columns("Course", "Section", "State")

    async def refresh_classroom(self):
        self.post_message(StatusNotification("Loading courses..."))
        table = self.query_one("#classroom-table")
        table.clear()
        self.query_one("#classroom-detail").clear()

        courses = await GogAPI.classroom_list()
        self.classroom_data = courses
        for c in courses:
            course_id = c.get("id") or ""
            name = c.get("name") or "(unnamed)"
            section = c.get("section") or ""
            state = c.get("courseState") or ""
            table.add_row(name, section, state, key=course_id)

        self.post_message(StatusNotification(f"Loaded {len(courses)} courses."))

    def _selected_course(self) -> dict:
        table = self.query_one("#classroom-table")
        if table.cursor_row is None or not getattr(self, "classroom_data", None):
            return {}
        course_id = table.ordered_rows[table.cursor_row].key.value
        return next((c for c in self.classroom_data if c.get("id") == course_id), {})

    async def on_button_pressed(self, event: Button.Pressed):
        if event.button.id == "classroom-ref-btn":
            await self.refresh_classroom()

    async def on_data_table_row_selected(self, event: DataTable.RowSelected):
        course = self._selected_course()
        log = self.query_one("#classroom-detail")
        log.clear()
        if not course:
            return
        log.write(f"[bold magenta]{rich_escape(course.get('name') or '(unnamed)')}[/bold magenta]\n")
        for label, value in (
            ("Section", course.get("section")),
            ("Room", course.get("room")),
            ("State", course.get("courseState")),
            ("Owner", course.get("ownerId")),
            ("Description", course.get("descriptionHeading") or course.get("description")),
        ):
            if value:
                log.write(f"[cyan]{label}:[/cyan] {rich_escape(str(value))}")
        url = course.get("alternateLink")
        if url:
            log.write(f"\n[blue underline]{rich_escape(url)}[/blue underline]")
        course_id = course.get("id") or ""
        if course_id:
            log.write(f"\n[dim]ID: {rich_escape(course_id)}[/dim]")


class SitesTab(Vertical):
    """Google Sites (Drive-backed): a full-width list of the sites visible in
    Drive."""

    def compose(self):
        yield Horizontal(
            Label(" Google Sites ", classes="view-header"),
            Horizontal(
                Button("Refresh", id="sites-ref-btn"),
                classes="header-buttons"
            ),
            classes="view-header-row"
        )
        yield DataTable(id="sites-table")

    def on_mount(self):
        table = self.query_one("#sites-table")
        table.cursor_type = "row"
        table.add_columns("Name", "Modified", "Link")

    async def refresh_sites(self):
        self.post_message(StatusNotification("Loading sites..."))
        table = self.query_one("#sites-table")
        table.clear()

        sites = await GogAPI.sites_list()
        self.sites_data = sites
        for s in sites:
            site_id = s.get("id") or ""
            name = s.get("name") or "(unnamed)"
            modified = s.get("modifiedTime") or ""
            link = s.get("webViewLink") or ""
            table.add_row(name, modified, link, key=site_id)

        self.post_message(StatusNotification(f"Loaded {len(sites)} sites."))
