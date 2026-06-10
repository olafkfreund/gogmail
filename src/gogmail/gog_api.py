import asyncio
import json
import logging
import os

logging.basicConfig(level=logging.INFO, filename="gogmail.log", filemode="a",
                    format="%(asctime)s - %(levelname)s - %(message)s")

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


async def run_gog(args: list[str], parse_json: bool = True, quiet: bool = False) -> tuple[bool, any]:
    """Runs the local gog command asynchronously and returns (success, result).

    On failure the error is logged and (unless quiet) pushed to the registered
    error sink so it reaches the user, then returned as (False, message). Use
    quiet=True for speculative calls whose failure is handled by the caller.
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
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=os.environ
        )
        stdout, stderr = await proc.communicate()

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
        # Temporarily silence the error sink: a failed preflight is expected and
        # is reported through the returned message, not as a scary popup.
        global _error_sink
        saved, _error_sink = _error_sink, None
        try:
            success, res = await run_gog(["status"])
        finally:
            _error_sink = saved

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
        success, res = await run_gog(["gmail", "search", query])
        return _extract_list(success, res, "threads")

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
    async def gmail_send(to: str, subject: str, body: str, thread_id: str = None, reply_to_message_id: str = None) -> tuple[bool, str]:
        args = ["gmail", "send", "--to", to, "--subject", subject, "--body", body]
        if thread_id:
            args.extend(["--thread-id", thread_id])
        if reply_to_message_id:
            args.extend(["--reply-to-message-id", reply_to_message_id, "--quote"])
        success, res = await run_gog(args)
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

    # --- Calendar ---
    @staticmethod
    async def calendar_list() -> list:
        success, res = await run_gog(["calendar", "calendars"])
        return _extract_list(success, res, "calendars")

    @staticmethod
    async def calendar_events(calendar_id: str = "primary") -> list:
        success, res = await run_gog(["calendar", "events", calendar_id])
        return _extract_list(success, res, "events")

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
        # values format: nested JSON values
        values_str = json.dumps(values)
        success, _ = await run_gog(["sheets", "update", spreadsheet_id, range_name, values_str])
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
        success, res = await run_gog(["meet", "create"])
        # Extract meeting code/link from response
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
    async def tasks_add(tasklist_id: str, title: str, notes: str = "") -> bool:
        args = ["tasks", "add", tasklist_id, "--title", title]
        if notes:
            args.extend(["--notes", notes])
        success, _ = await run_gog(args)
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
    async def chat_messages(space_id: str) -> list:
        success, res = await run_gog(["chat", "messages", "list", space_id])
        return _extract_list(success, res, "messages")

    @staticmethod
    async def chat_send_message(space_id: str, text: str) -> bool:
        success, _ = await run_gog(["chat", "messages", "send", space_id, "--text", text])
        return success
