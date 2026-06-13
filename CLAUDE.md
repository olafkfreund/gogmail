# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

GogMail is a [Textual](https://textual.textualize.io/) TUI client for Google Workspace (Gmail, Calendar, Drive, Docs/Sheets/Slides/Forms, Meet, Zoom, Contacts, Tasks, Chat) plus an embedded Gemini AI assistant. It is a **thin TUI over external services** — it does not talk to Google APIs directly:

- **All Workspace data flows through the `gog` CLI** (a separately installed tool), invoked as `gog --json <subcommand>`. The TUI never holds Google credentials; `gog` owns auth.
- **AI flows through the Gemini REST API** over plain `requests`, keyed by `GEMINI_API_KEY`.
- **Zoom meeting creation goes to the Zoom REST API** (`zoom_api.py`, `requests` in a thread, mirroring `gemini_api.py`) since gogcli only manages Zoom auth and has no meeting commands. Credentials come from `GOG_ZOOM_ACCOUNT_ID`/`GOG_ZOOM_CLIENT_ID`/`GOG_ZOOM_CLIENT_SECRET` (the same names gogcli accepts); the S2S app needs a `meeting:write` scope.

## Commands

```bash
just run            # Run the app (devenv shell run-app → PYTHONPATH=src python -m gogmail.app)
just test           # Run the unittest suite (devenv shell run-tests)
just lint           # py_compile syntax check over src/

devenv shell        # Enter the dev env, then: python -m gogmail.app

# Nix (inputs wire gog + clipboard/browser/image tools onto PATH):
nix run             # run the app          (apps.default)
nix develop         # dev shell            (devShells.default)
nix flake check     # build + run tests    (checks.default)
```

Tests are stdlib `unittest` (no pytest); the mockable seam is `gog_api.run_gog`.

Runtime prerequisites (the app will not function without these):
- `gog` CLI installed and authenticated (`gog status`, `gog auth list`). The Nix package wraps `gogcli` onto PATH; a `gog` already on PATH wins.
- `GEMINI_API_KEY` exported (or `geminiApiKeyFile` via the Nix modules). `GEMINI_MODEL_DEFAULT` optionally overrides the model.

Nix outputs (`flake.nix`): `packages`/`apps`/`devShells`/`checks` per system, plus `nixosModules.gogmail` and `homeManagerModules.gogmail`. Both modules expose `programs.gogmail` with `geminiApiKeyFile` (secret-safe, read at runtime via a wrapper) and a literal `geminiApiKey` (warns: world-readable store). Python deps are declared in **three** places that must stay in sync: `pyproject.toml`, `devenv.nix`, `flake.nix`.

**Multiple accounts:** `gog` is multi-account; `run_gog` scopes API calls to the active account via `-a <email>` (set with `set_account`, never applied to `auth` commands). The app lists accounts via `GogAPI.list_accounts()`, shows them under the sidebar **👤 Accounts** node, and `switch_account()` changes the active one (persisted to config, resets tab `_loaded` flags, reloads).

> Note: the real working clone is `/home/olafkfreund/Source/GitHub/gogmail`. The `/mnt/data/...` primary dir is empty.

## Architecture

Three layers, strictly separated:

1. **API layer** (`src/gogmail/gog_api.py`, `src/gogmail/gemini_api.py`)
   - `GogAPI` is a class of `@staticmethod async` methods, one per Workspace operation. Every method delegates to the module-level `run_gog(args, parse_json=True)`, which spawns `gog --json ...` via `asyncio.create_subprocess_exec`, returns `(success: bool, result)`, and logs failures to `gogmail.log`. Reads return `[]`/`{}` on failure (via the `_extract_list`/`_str_result` helpers); JSON commands are parsed, text commands (e.g. `docs cat`) pass `parse_json=False`.
   - **Error surfacing:** because reads return empty on failure, `run_gog` also pushes every failure through a global **error sink** (`set_error_sink`). The app registers a sink that shows an error toast, so a failed `gog` call never silently looks like "no data". `GogAPI.preflight()` checks install/auth at startup and resolves the active account via `_account_from_status`.
   - `GeminiAPI` wraps blocking `requests.post` in `asyncio.to_thread`. Both single-turn and multi-turn paths funnel through one `_call_sync(contents, system_instruction)`.

2. **Widget/screen layer** (`src/gogmail/tui/`)
   - `widgets.py`: one `*Tab(Vertical)` class per service. Each owns its `compose()` and an `async refresh_*()` that calls `GogAPI` and repopulates its widgets — call it after any mutation. The four Drive document tabs (`DocsTab`, `SheetsTab`, `SlidesTab`, `FormsTab`) share a **`DriveMimeTab` base** (set `MIME`/`LIST_TABLE_ID`/`NEW_DIALOG`/… class attrs); their reload method is `refresh_list()`.
   - `screens.py`: `ModalScreen` dialogs. `ThemeSelectScreen` is generated from the `THEMES` registry (the single source of truth for theme keys, imported by `app.py` as `VALID_THEMES`).
   - `styles.tcss`: all styling, by theme-prefix class (`.theme-gruvbox ...`). Theme switches via `App.classes = f"theme-{name}"`.

3. **App shell** (`src/gogmail/app.py`)
   - `GogMailApp(App)`: left `Tree` sidebar → `ContentSwitcher` of 12 tabs → draggable `AISplitter` → `AIAssistantPanel`. Navigation via `on_tree_node_selected` using the `TREE_VIEWS` table.
   - **Status is centralized** in `notify_status(message, error=False)` (updates subtitle prefixed with the resolved account; toasts on error). The `StatusNotification` message and the gog error sink both route here.
   - **Dialog dispatch is generalized:** simple create dialogs use `_open_prompt(...)`; all mutations run through `_run_mutation(working, coro, success, refresh)` for consistent status + refresh. Add a new simple dialog as a one-line `_open_prompt` call.
   - Config (`theme`, `ai_width`, `account`) persists to `~/.config/gogmail/settings.json` via `load_config`/`save_config`. Exported email temp files are tracked via `register_temp_file` and removed in `on_unmount`.
   - Clipboard: `App.clipboard` / `copy_to_clipboard` shell out to wl-copy/wl-paste → xclip → xsel, with an in-memory fallback.

### AI tool-calling loop (the non-obvious part)

The Gemini assistant is **agentic** via a prompt-driven JSON protocol, all in `app.py`:

- `AIAssistantPanel._gather_context()` pulls context from the active tab; `on_input_submitted` prepends it plus `SYSTEM_INSTRUCTION` (generated from the tool registry).
- `run_ai()` loops up to `max_steps = 5`: call `GeminiAPI.generate_chat`, regex-extract a ```json``` block, and if it parses to a dict with a `tool` key, dispatch via `execute_tool` and feed the result back; otherwise show the text and stop.
- **Tools live in one `TOOLS` registry** (`name`, `description`, `params` as `(name, required, example)`, `handler`). `_build_system_instruction(TOOLS)` generates the prompt schema section and `execute_tool` validates required params + dispatches against the same table.

**To add a new AI tool: append one entry to `TOOLS`** (with an `async def _tool_*` handler) — the prompt schema and dispatch both derive from it, so they can't drift.

## Conventions

- `GogAPI` ops are `async`: mutations return `(success, result)`, reads return a plain `list`/`dict`/`str` (empty on failure). Match this when adding methods; failures auto-surface via the error sink.
- After any mutating action, refresh the owning tab (`_run_mutation`'s `refresh` arg handles this for dialogs).
- Tests use stdlib `unittest` (no pytest dep): `just test` → `python -m unittest discover -s tests`. The mockable seam is `gog_api.run_gog`.
- Key bindings (`GogMailApp.BINDINGS`): `q` quit, `ctrl+b` sidebar, `alt+a` AI panel, `alt+left/right` (or `alt+h/l`) resize AI drawer.
