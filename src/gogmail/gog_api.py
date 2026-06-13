import asyncio
import json
import logging
import os


def _configure_logging() -> None:
    """Log to the XDG state dir (never the cwd). Must never crash on import:
    a read-only HOME (Nix build sandbox, locked-down accounts) would otherwise
    take down the whole module, so fall back to a temp file and finally to no
    file logging at all."""
    fmt = "%(asctime)s - %(levelname)s - %(message)s"
    base = os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")
    candidates = [os.path.join(base, "gogmail")]
    import tempfile
    candidates.append(os.path.join(tempfile.gettempdir(), "gogmail"))
    for d in candidates:
        try:
            os.makedirs(d, exist_ok=True)
            logging.basicConfig(level=logging.INFO, filename=os.path.join(d, "gogmail.log"),
                                filemode="a", format=fmt)
            return
        except OSError:
            continue
    # Last resort: in-memory/stderr handler so logging calls still work.
    logging.basicConfig(level=logging.INFO, format=fmt, handlers=[logging.NullHandler()])


_configure_logging()

# --- Error surfacing -------------------------------------------------------
# Reads return empty values on failure so call sites stay simple, but a failed
# `gog` call must never look like "no data". Any failure is pushed through this
# sink so the UI can show a real error instead of a silently empty view.
_error_sink = None

# Active Google account. When set, it is passed to every API command as
# `-a <email>` so the app can switch between multiple authenticated accounts at
# runtime. `auth` commands are never account-scoped (we want all accounts).
_active_account = None


class GogError(Exception):
    """Raised by the *_checked helpers when a gog command fails."""


def set_error_sink(fn) -> None:
    """Register a callback invoked with a human-readable message on any gog failure."""
    global _error_sink
    _error_sink = fn


def set_account(email) -> None:
    """Set the active account applied to subsequent gog API commands."""
    global _active_account
    _active_account = email or None


def get_account():
    return _active_account


def _report_error(cmd: list[str], err_msg: str) -> None:
    logging.error(f"gog command failed: {' '.join(cmd)} - Error: {err_msg}")
    if _error_sink is not None:
        # Show the failing subcommand (not the noisy full arg list) plus the error.
        subcommand = " ".join(a for a in cmd[1:] if not a.startswith("-"))
        try:
            _error_sink(f"gog {subcommand}: {err_msg or 'command failed'}")
        except Exception as e:  # never let the sink break a command
            logging.error(f"Error sink raised: {e}")


async def run_gog(args: list[str], parse_json: bool = True, quiet: bool = False,
                  stdin_data: str = None) -> tuple[bool, any]:
    """Runs the local gog command asynchronously and returns (success, result).

    On failure the error is logged and (unless quiet) pushed to the registered
    error sink so it reaches the user, then returned as (False, message). Use
    quiet=True for speculative calls whose failure is handled by the caller.
    stdin_data is piped to the process (for `--body-file -` style flags, so
    bodies never appear in /proc/<pid>/cmdline).
    """
    cmd = ["gog"]
    if parse_json:
        cmd.append("--json")
    # Scope API commands to the active account (but never `auth`, which lists/
    # manages all accounts).
    if _active_account and args and args[0] != "auth":
        cmd += ["-a", _active_account]
    cmd.extend(args)

    def report(msg):
        if not quiet:
            _report_error(cmd, msg)
        else:
            logging.error(f"gog command failed (quiet): {' '.join(cmd)} - {msg}")

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE if stdin_data is not None else None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=os.environ
        )
        stdout, stderr = await proc.communicate(
            input=stdin_data.encode() if stdin_data is not None else None)

        if proc.returncode != 0:
            err_msg = stderr.decode().strip()
            report(err_msg)
            return False, err_msg

        output = stdout.decode().strip()
        if parse_json:
            if not output:
                return True, {}
            try:
                return True, json.loads(output)
            except json.JSONDecodeError as e:
                report(f"JSON parse error: {e}")
                return False, f"JSON parse error: {output}"
        else:
            return True, output
    except FileNotFoundError:
        report("the `gog` CLI was not found on PATH")
        return False, "gog-not-found"
    except Exception as e:
        report(str(e))
        return False, str(e)

def _str_result(res: any) -> str:
    """Coerce a run_gog result to a string for tuple[bool, str] returns."""
    return res if isinstance(res, str) else json.dumps(res)

def _extract_list(success: bool, res: any, key: str, fallback_key: str = None) -> list:
    """Pull a list out of a run_gog dict result, with an optional fallback key."""
    if success and isinstance(res, dict):
        if fallback_key:
            return res.get(key, []) or res.get(fallback_key, [])
        return res.get(key, [])
    return []

class GogAPI:
    # --- Auth Status ---
    @staticmethod
    async def get_status() -> dict:
        success, res = await run_gog(["status"])
        return res if success else {}

    @staticmethod
    async def preflight() -> tuple[bool, str]:
        """Check that `gog` is installed and authenticated before the app boots.

        Returns (ok, message). `message` is an actionable hint when not ok.
        """
        # quiet=True: a failed preflight is expected and is reported through the
        # returned message, not the error sink. (Nulling the global sink here
        # would race with concurrent run_gog calls and swallow their errors.)
        success, res = await run_gog(["status"], quiet=True)

        if not success:
            if res == "gog-not-found":
                return False, "The `gog` CLI was not found on PATH. Install it and run `gog auth login`."
            return False, f"`gog status` failed: {res}"
        if not res:
            return False, "`gog` is installed but not authenticated. Run `gog auth login`."
        return True, GogAPI._account_from_status(res)

    @staticmethod
    async def list_accounts() -> list:
        """Return the emails of all authenticated gog accounts."""
        ok, res = await run_gog(["auth", "list"])
        if ok and isinstance(res, dict):
            return [a.get("email") for a in res.get("accounts", []) if a.get("email")]
        return []

    @staticmethod
    def _account_from_status(status: dict) -> str:
        """Best-effort extraction of the active account email from `gog status`."""
        if isinstance(status, dict):
            for key in ("account", "email", "user", "active_account", "activeAccount"):
                val = status.get(key)
                if isinstance(val, str) and "@" in val:
                    return val
                if isinstance(val, dict):
                    for sub in ("email", "address"):
                        if isinstance(val.get(sub), str) and "@" in val[sub]:
                            return val[sub]
        return os.environ.get("GOG_ACCOUNT", "")

    # --- Gmail ---
    @staticmethod
    async def gmail_search(query: str = "is:unread") -> list:
        threads, _ = await GogAPI.gmail_search_page(query)
        return threads

    @staticmethod
    async def gmail_search_page(query: str = "is:unread", max_results: int = 25,
                                page_token: str = None) -> tuple[list, str]:
        """Search one page of threads, returning (threads, next_page_token).

        Passes `--max` and (when paging) `--page`. Reads the `nextPageToken`
        from the JSON envelope so callers can fetch the following page; an empty
        string means there are no more results. Reads return ([], "") on failure
        (surfaced via the error sink). NB: do NOT pass `--results-only` here, as
        it drops the envelope (and thus the token)."""
        args = ["gmail", "search", query, "--max", str(max_results)]
        if page_token:
            args += ["--page", page_token]
        success, res = await run_gog(args)
        threads = _extract_list(success, res, "threads")
        next_token = ""
        if success and isinstance(res, dict):
            next_token = res.get("nextPageToken") or ""
        return threads, next_token

    @staticmethod
    async def gmail_get_message(message_id: str) -> dict:
        # gmail search returns THREAD ids, which equal a message id only for
        # single-message threads. For multi-message threads (common in Sent)
        # `gmail get <thread_id>` 404s, so resolve the latest message in the
        # thread and fetch that. The first attempt is quiet so the expected
        # 404 doesn't raise an error toast.
        success, res = await run_gog(["gmail", "get", message_id], quiet=True)
        if success and isinstance(res, dict) and res:
            return res

        ok, tres = await run_gog(["gmail", "thread", "get", message_id], quiet=True)
        if ok and isinstance(tres, dict):
            messages = (tres.get("thread") or {}).get("messages") or []
            if messages:
                real_id = messages[-1].get("id")
                if real_id:
                    ok2, res2 = await run_gog(["gmail", "get", real_id])
                    if ok2 and isinstance(res2, dict):
                        return res2
        return {}

    @staticmethod
    async def gmail_thread_messages(thread_id: str) -> list:
        """All message stubs ({id, ...}) in a thread, oldest first."""
        ok, res = await run_gog(["gmail", "thread", "get", thread_id], quiet=True)
        if ok and isinstance(res, dict):
            return (res.get("thread") or {}).get("messages") or []
        return []

    @staticmethod
    async def gmail_send(to: str, subject: str, body: str, thread_id: str = None, reply_to_message_id: str = None) -> tuple[bool, str]:
        # Body goes via stdin (--body-file -): argv is world-readable in /proc.
        args = ["gmail", "send", "--to", to, "--subject", subject, "--body-file", "-"]
        if thread_id:
            args.extend(["--thread-id", thread_id])
        if reply_to_message_id:
            args.extend(["--reply-to-message-id", reply_to_message_id, "--quote"])
        success, res = await run_gog(args, stdin_data=body)
        return success, _str_result(res)

    @staticmethod
    async def gmail_labels_list() -> list:
        success, res = await run_gog(["gmail", "labels", "list"])
        return _extract_list(success, res, "labels")

    @staticmethod
    async def gmail_labels_create(name: str) -> tuple[bool, str]:
        success, res = await run_gog(["gmail", "labels", "create", name])
        return success, _str_result(res)

    @staticmethod
    async def gmail_modify_labels(thread_id: str, add: str = "", remove: str = "") -> bool:
        """Add/remove labels (comma-separated names or IDs) on a thread."""
        args = ["gmail", "labels", "modify", thread_id]
        if add:
            args += ["--add", add]
        if remove:
            args += ["--remove", remove]
        success, _ = await run_gog(args)
        return success

    @staticmethod
    async def gmail_create_draft(to: str, subject: str, body: str, cc: str = "", bcc: str = "") -> tuple[bool, str]:
        args = ["gmail", "drafts", "create", "--to", to, "--subject", subject, "--body-file", "-"]
        if cc:
            args += ["--cc", cc]
        if bcc:
            args += ["--bcc", bcc]
        success, res = await run_gog(args, stdin_data=body)
        return success, _str_result(res)

    @staticmethod
    async def gmail_archive(message_id: str) -> bool:
        success, _ = await run_gog(["gmail", "archive", message_id])
        return success

    @staticmethod
    async def gmail_trash(message_id: str) -> bool:
        success, _ = await run_gog(["gmail", "trash", message_id])
        return success

    @staticmethod
    async def gmail_mark_read(message_id: str) -> bool:
        success, _ = await run_gog(["gmail", "mark-read", message_id])
        return success

    @staticmethod
    async def gmail_mark_unread(message_id: str) -> bool:
        success, _ = await run_gog(["gmail", "unread", message_id])
        return success

    @staticmethod
    async def gmail_list_attachments(thread_id: str) -> list:
        """List attachments across all messages in a thread.

        Each entry is a dict: filename, mimeType, size, sizeHuman,
        attachmentId, messageId. Returns [] on failure (surfaced via the sink).
        """
        success, res = await run_gog(["gmail", "thread", "attachments", thread_id])
        return _extract_list(success, res, "attachments")

    @staticmethod
    async def gmail_download_attachment(message_id: str, attachment_id: str, destination: str) -> tuple[bool, str]:
        """Download a single attachment to a file path (mirrors drive_download)."""
        success, res = await run_gog(
            ["gmail", "attachment", message_id, attachment_id, "--out", destination])
        return success, _str_result(res)

    # --- Calendar ---
    @staticmethod
    async def calendar_list() -> list:
        success, res = await run_gog(["calendar", "calendars"])
        return _extract_list(success, res, "calendars")

    @staticmethod
    async def calendar_events(calendar_id: str = "primary", time_range: str = None,
                              time_from: str = None, time_to: str = None,
                              max_results: int = None) -> list:
        """List events. With no range args this is the original call (used by the
        Calendar tab); the assistant passes a range for "this week"/"today".
        time_range: 'today' | 'tomorrow' | 'week' | 'days:<N>'. Explicit
        time_from/time_to (RFC3339, a date, or words like 'monday') win."""
        args = ["calendar", "events", calendar_id]
        if time_from or time_to:
            if time_from:
                args += ["--from", time_from]
            if time_to:
                args += ["--to", time_to]
        elif time_range in ("today", "tomorrow", "week"):
            args.append(f"--{time_range}")
        elif time_range and time_range.startswith("days:"):
            args += ["--days", time_range.split(":", 1)[1]]
        if max_results:
            args += ["--max", str(max_results)]
        success, res = await run_gog(args)
        return _extract_list(success, res, "events")

    @staticmethod
    async def calendar_freebusy(calendar_id: str, time_from: str, time_to: str) -> list:
        """Query free/busy for a calendar (or email) over [time_from, time_to].

        Wraps `gog calendar freebusy --cal <id> --from <rfc3339> --to <rfc3339>`.
        Returns the list of busy intervals (each {"start", "end"}) for the
        calendar, [] on failure or if it has no busy blocks. gog returns
        `{"calendars": {"<id>": {"busy": [...]}}}`; the key may be the resolved
        id rather than the requested one, so fall back to the single entry."""
        success, res = await run_gog(
            ["calendar", "freebusy", "--cal", calendar_id, "--from", time_from, "--to", time_to])
        if not (success and isinstance(res, dict)):
            return []
        calendars = res.get("calendars") or {}
        entry = calendars.get(calendar_id)
        if entry is None and len(calendars) == 1:
            entry = next(iter(calendars.values()))
        return (entry or {}).get("busy", []) if isinstance(entry, dict) else []

    @staticmethod
    async def calendar_create_event(calendar_id: str, summary: str, start_time: str, end_time: str, description: str = "", location: str = "") -> tuple[bool, str]:
        args = ["calendar", "create", calendar_id, "--summary", summary, "--from", start_time, "--to", end_time]
        if description:
            args.extend(["--description", description])
        if location:
            args.extend(["--location", location])
        success, res = await run_gog(args)
        return success, _str_result(res)

    @staticmethod
    async def calendar_update_event(calendar_id: str, event_id: str, summary: str = None,
                                    start_time: str = None, end_time: str = None,
                                    description: str = None, location: str = None) -> tuple[bool, str]:
        """Update an event. Only non-None fields are changed."""
        args = ["calendar", "update", calendar_id, event_id]
        for flag, value in (("--summary", summary), ("--from", start_time), ("--to", end_time),
                            ("--description", description), ("--location", location)):
            if value is not None:
                args += [flag, value]
        success, res = await run_gog(args)
        return success, _str_result(res)

    @staticmethod
    async def calendar_delete_event(calendar_id: str, event_id: str) -> bool:
        success, _ = await run_gog(["calendar", "delete", calendar_id, event_id])
        return success

    @staticmethod
    async def calendar_respond_event(calendar_id: str, event_id: str, response: str) -> bool:
        # response: yes, no, maybe (equivalent to accepted, declined, tentative)
        success, _ = await run_gog(["calendar", "respond", calendar_id, event_id, "--response", response])
        return success

    # --- Drive ---
    @staticmethod
    async def drive_list(folder_id: str = "root") -> list:
        args = ["drive", "ls"]
        if folder_id != "root":
            args.extend(["--folder", folder_id])
        success, res = await run_gog(args)
        return _extract_list(success, res, "files")

    @staticmethod
    async def drive_search(query: str) -> list:
        success, res = await run_gog(["drive", "search", query])
        return _extract_list(success, res, "files")

    @staticmethod
    async def drive_download(file_id: str, destination: str) -> tuple[bool, str]:
        success, res = await run_gog(["drive", "download", file_id, "--out", destination])
        return success, _str_result(res)

    @staticmethod
    async def drive_upload(local_path: str) -> tuple[bool, str]:
        success, res = await run_gog(["drive", "upload", local_path])
        return success, _str_result(res)

    @staticmethod
    async def drive_delete(file_id: str) -> bool:
        success, _ = await run_gog(["drive", "delete", file_id])
        return success

    @staticmethod
    async def drive_mkdir(name: str) -> tuple[bool, str]:
        success, res = await run_gog(["drive", "mkdir", name])
        return success, _str_result(res)

    @staticmethod
    async def drive_share(file_id: str, email: str, role: str = "reader", notify: bool = False) -> tuple[bool, str]:
        args = ["drive", "share", file_id, "--to", "user", "--email", email, "--role", role]
        if notify:
            args.append("--notify")
        success, res = await run_gog(args)
        return success, _str_result(res)

    @staticmethod
    async def drive_rename(file_id: str, new_name: str) -> tuple[bool, str]:
        success, res = await run_gog(["drive", "rename", file_id, new_name])
        return success, _str_result(res)

    @staticmethod
    async def drive_move(file_id: str, parent_id: str) -> tuple[bool, str]:
        success, res = await run_gog(["drive", "move", file_id, "--parent", parent_id])
        return success, _str_result(res)

    # --- Docs ---
    @staticmethod
    async def docs_cat(doc_id: str) -> str:
        success, res = await run_gog(["docs", "cat", doc_id], parse_json=False)
        return res if success else ""

    @staticmethod
    async def docs_create(title: str) -> tuple[bool, str]:
        success, res = await run_gog(["docs", "create", title])
        return success, _str_result(res)

    @staticmethod
    async def docs_write(doc_id: str, content: str) -> bool:
        # gog docs write is documented, writes content to a doc
        success, _ = await run_gog(["docs", "write", doc_id, "--content", content])
        return success

    # --- Sheets ---
    @staticmethod
    async def sheets_metadata(spreadsheet_id: str) -> dict:
        success, res = await run_gog(["sheets", "metadata", spreadsheet_id])
        return res if success else {}

    @staticmethod
    async def sheets_get(spreadsheet_id: str, range_name: str) -> dict:
        success, res = await run_gog(["sheets", "get", spreadsheet_id, range_name])
        return res if success else {}

    @staticmethod
    async def sheets_update(spreadsheet_id: str, range_name: str, values: list[list[str]]) -> bool:
        # `gog sheets update` takes the 2D values via --values-json (positional
        # values are the comma/pipe text form, which can't hold arbitrary cells).
        success, _ = await run_gog(
            ["sheets", "update", spreadsheet_id, range_name, "--values-json", json.dumps(values)])
        return success

    @staticmethod
    async def sheets_set_cell(spreadsheet_id: str, cell: str, value: str) -> bool:
        # Single A1 cell update (e.g. cell="B3"). One row, one cell.
        return await GogAPI.sheets_update(spreadsheet_id, cell, [[value]])

    @staticmethod
    async def sheets_append_row(spreadsheet_id: str, values: list[str]) -> bool:
        # `gog sheets append <id> <range>` adds a new row after the existing
        # data; range "A1" anchors it to the first table. One row of cells.
        success, _ = await run_gog(
            ["sheets", "append", spreadsheet_id, "A1",
             "--values-json", json.dumps([list(values)])])
        return success

    @staticmethod
    async def sheets_create(title: str) -> tuple[bool, str]:
        success, res = await run_gog(["sheets", "create", title])
        return success, _str_result(res)

    # --- Slides ---
    @staticmethod
    async def slides_info(presentation_id: str) -> dict:
        success, res = await run_gog(["slides", "info", presentation_id])
        return res if success else {}

    @staticmethod
    async def slides_create(title: str) -> tuple[bool, str]:
        success, res = await run_gog(["slides", "create", title])
        return success, _str_result(res)

    # --- Forms ---
    @staticmethod
    async def forms_get(form_id: str) -> dict:
        success, res = await run_gog(["forms", "get", form_id])
        return res if success else {}

    @staticmethod
    async def forms_create(title: str) -> tuple[bool, str]:
        success, res = await run_gog(["forms", "create", "--title", title])
        return success, _str_result(res)

    # --- Meet ---
    @staticmethod
    async def meet_create() -> tuple[bool, str]:
        """Create a Meet space; on success returns just the meeting URL."""
        success, res = await run_gog(["meet", "create"])
        if success and isinstance(res, dict):
            url = res.get("meeting_uri") or res.get("meetingUri") or ""
            if url:
                return True, url
        return success, _str_result(res)

    # --- Zoom ---
    @staticmethod
    async def zoom_doctor() -> str:
        success, res = await run_gog(["zoom", "auth", "doctor"], parse_json=False)
        return res

    # --- Contacts & People ---
    @staticmethod
    async def contacts_search(query: str) -> list:
        success, res = await run_gog(["contacts", "search", query])
        return _extract_list(success, res, "contacts", "connections")

    @staticmethod
    async def contacts_list() -> list:
        success, res = await run_gog(["contacts", "list"])
        return _extract_list(success, res, "contacts", "connections")

    @staticmethod
    def contact_email(contact: dict) -> str:
        """Primary email for a contact dict (handles flat and nested shapes)."""
        if contact.get("email"):
            return contact["email"]
        for e in contact.get("emailAddresses", []):
            if e.get("value"):
                return e["value"]
        return ""

    @staticmethod
    def contact_name(contact: dict) -> str:
        if contact.get("name"):
            return contact["name"]
        for n in contact.get("names", []):
            if n.get("displayName"):
                return n["displayName"]
        return ""

    @classmethod
    async def contact_suggestions(cls) -> list:
        """Recipient autocomplete entries: 'Name <email>' plus bare emails."""
        suggestions, seen = [], set()
        for c in await cls.contacts_list():
            email = cls.contact_email(c)
            if not email or email in seen:
                continue
            seen.add(email)
            name = cls.contact_name(c)
            suggestions.append(f"{name} <{email}>" if name else email)
            suggestions.append(email)
        return suggestions

    @staticmethod
    def _split_name(name: str) -> tuple[str, str]:
        """Split a display name into (given, family) for the People API.

        `gog contacts create/update` take separate --given/--family flags, but
        the TUI works with a single display name. Everything before the last
        space is the given name; the last token is the family name.
        """
        parts = (name or "").strip().split()
        if not parts:
            return "", ""
        if len(parts) == 1:
            return parts[0], ""
        return " ".join(parts[:-1]), parts[-1]

    @classmethod
    async def contacts_create(cls, name: str, email: str = None, phone: str = None) -> tuple[bool, str]:
        given, family = cls._split_name(name)
        args = ["contacts", "create", "--given", given]
        if family:
            args += ["--family", family]
        if email:
            args += ["--email", email]
        if phone:
            args += ["--phone", phone]
        success, res = await run_gog(args)
        return success, _str_result(res)

    @classmethod
    async def contacts_update(cls, resource_name: str, name: str = None,
                              email: str = None, phone: str = None) -> tuple[bool, str]:
        """Update a contact. Only provided fields are changed."""
        args = ["contacts", "update", resource_name]
        if name is not None:
            given, family = cls._split_name(name)
            args += ["--given", given, "--family", family]
        if email is not None:
            args += ["--email", email]
        if phone is not None:
            args += ["--phone", phone]
        success, res = await run_gog(args)
        return success, _str_result(res)

    @staticmethod
    async def contacts_delete(resource_name: str) -> bool:
        # --force: the TUI confirms destructive ops itself, so skip gog's prompt.
        success, _ = await run_gog(["contacts", "delete", resource_name, "--force"])
        return success

    @staticmethod
    async def people_me() -> dict:
        success, res = await run_gog(["people", "me"])
        return res if success else {}

    # --- Tasks ---
    @staticmethod
    async def tasks_lists() -> list:
        success, res = await run_gog(["tasks", "lists", "list"])
        return _extract_list(success, res, "tasklists")

    @staticmethod
    async def tasks_lists_create(title: str) -> bool:
        success, _ = await run_gog(["tasks", "lists", "create", title])
        return success

    @staticmethod
    async def tasks_list(tasklist_id: str) -> list:
        success, res = await run_gog(["tasks", "list", tasklist_id])
        return _extract_list(success, res, "tasks")

    @staticmethod
    async def tasks_add(tasklist_id: str, title: str, notes: str = "", due: str = "") -> bool:
        args = ["tasks", "add", tasklist_id, "--title", title]
        if notes:
            args.extend(["--notes", notes])
        if due:
            args.extend(["--due", due])
        success, _ = await run_gog(args)
        return success

    @staticmethod
    async def tasks_edit(tasklist_id: str, task_id: str, title: str = None,
                         notes: str = None, due: str = None) -> tuple[bool, str]:
        """Update a task. Only non-None fields are changed (pass "" to clear)."""
        args = ["tasks", "update", tasklist_id, task_id]
        for flag, value in (("--title", title), ("--notes", notes), ("--due", due)):
            if value is not None:
                args += [flag, value]
        success, res = await run_gog(args)
        return success, _str_result(res)

    @staticmethod
    async def tasks_clear_completed(tasklist_id: str) -> bool:
        """Clear all completed tasks from a list."""
        success, _ = await run_gog(["tasks", "clear", tasklist_id])
        return success

    @staticmethod
    async def tasks_done(tasklist_id: str, task_id: str) -> bool:
        success, _ = await run_gog(["tasks", "done", tasklist_id, task_id])
        return success

    @staticmethod
    async def tasks_undo(tasklist_id: str, task_id: str) -> bool:
        success, _ = await run_gog(["tasks", "undo", tasklist_id, task_id])
        return success

    @staticmethod
    async def tasks_delete(tasklist_id: str, task_id: str) -> bool:
        success, _ = await run_gog(["tasks", "delete", tasklist_id, task_id])
        return success

    # --- Chat ---
    @staticmethod
    async def chat_spaces() -> list:
        success, res = await run_gog(["chat", "spaces", "list"])
        return _extract_list(success, res, "spaces")

    @staticmethod
    async def chat_messages(space_id: str, max_results: int = 50) -> list:
        success, res = await run_gog(["chat", "messages", "list", space_id,
                                      "--max", str(max_results)])
        return _extract_list(success, res, "messages")

    # Chat messages reference senders as bare "users/<id>" strings; resolve them
    # to (display name, emails) via the People API, cached for the session.
    _person_cache: dict = {}

    @classmethod
    async def person_info(cls, user_id: str) -> tuple[str, set]:
        pid = "people/" + user_id.split("/")[-1]
        if pid in cls._person_cache:
            return cls._person_cache[pid]
        ok, res = await run_gog(["people", "get", pid], quiet=True)
        name, emails = "", set()
        if ok and isinstance(res, dict):
            person = res.get("person") or res
            for n in person.get("names", []):
                if n.get("displayName"):
                    name = n["displayName"]
                    break
            emails = {e.get("value", "").lower() for e in person.get("emailAddresses", []) if e.get("value")}
        cls._person_cache[pid] = (name, emails)
        return name, emails

    @classmethod
    async def chat_dm_label(cls, space_id: str) -> str:
        """Best-effort human label for a DM: the other participant's name."""
        own = (get_account() or "").lower()
        seen = []
        for m in await cls.chat_messages(space_id, max_results=10):
            sender = m.get("sender")
            sid = sender if isinstance(sender, str) else (sender or {}).get("name", "")
            if sid and sid not in seen:
                seen.append(sid)
        for sid in seen:
            name, emails = await cls.person_info(sid)
            if name and (not own or own not in emails):
                return name
        # Fall back to any resolvable sender (e.g. a DM with only own messages).
        for sid in seen:
            name, _ = await cls.person_info(sid)
            if name:
                return name
        return ""

    @staticmethod
    async def chat_send_message(space_id: str, text: str) -> bool:
        success, _ = await run_gog(["chat", "messages", "send", space_id, "--text", text])
        return success

    # --- Keep (Notes) ---
    @staticmethod
    async def keep_list() -> list:
        success, res = await run_gog(["keep", "list"])
        return _extract_list(success, res, "notes")

    @staticmethod
    async def keep_create(title: str, text: str = "") -> tuple[bool, str]:
        args = ["keep", "create"]
        if title:
            args += ["--title", title]
        if text:
            args += ["--text", text]
        success, res = await run_gog(args)
        return success, _str_result(res)

    @staticmethod
    async def keep_delete(note_id: str) -> bool:
        # --force: the TUI confirms destructive ops itself, so skip gog's prompt.
        success, _ = await run_gog(["keep", "delete", note_id, "--force"])
        return success
    # --- Groups (Cloud Identity / Workspace Admin) ---
    # These hit an Admin API and may require domain-wide delegation / extra
    # scopes; runtime permission errors surface via the error sink, so the
    # wiring is correct even when the account can't read groups.
    @staticmethod
    async def groups_list() -> list:
        """Groups the active account belongs to (each: email, name, description).

        Reads return [] on failure (surfaced via the error sink)."""
        success, res = await run_gog(["groups", "list"])
        return _extract_list(success, res, "groups")

    @staticmethod
    async def group_members(group_email: str) -> list:
        """Members of a group (each: email, role). Reads return [] on failure."""
        success, res = await run_gog(["groups", "members", group_email])
        return _extract_list(success, res, "members")
    # --- Backup ---
    @staticmethod
    async def backup(destination: str = None, services: str = None) -> tuple[bool, str]:
        """Export the account into an encrypted backup via `gog backup push`.

        This is a long-running mutation. `destination` is the local backup
        repository path (passed as `--repo`); `services` is a comma-separated
        list (e.g. "gmail,calendar,drive") passed as `--services`. `--no-input`
        keeps it non-interactive. Returns (success, message)."""
        args = ["backup", "push", "--no-input"]
        if destination:
            args += ["--repo", destination]
        if services:
            args += ["--services", services]
        success, res = await run_gog(args)
        return success, _str_result(res)
