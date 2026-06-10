# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

GogMail is a [Textual](https://textual.textualize.io/) TUI client for Google Workspace (Gmail, Calendar, Drive, Docs/Sheets/Slides/Forms, Meet, Zoom, Contacts, Tasks, Chat) plus an embedded Gemini AI assistant. It is a **thin TUI over two external dependencies** — it does not talk to Google APIs directly:

- **All Workspace data flows through the `gog` CLI** (a separately installed tool), invoked as `gog --json <subcommand>`. The TUI never holds Google credentials; `gog` owns auth.
- **AI flows through the Gemini REST API** over plain `requests`, keyed by `GEMINI_API_KEY`.

## Commands

```bash
just run            # Run the app (devenv shell run-app → PYTHONPATH=src python -m gogmail.app)
just lint           # "lint" == py_compile syntax check over src/ (no real linter configured)
just check-syntax   # same as lint

devenv shell        # Enter the dev env, then: python -m gogmail.app
```

There is **no test suite** and **no formatter/linter** beyond `py_compile`. Adding tests means setting up the harness from scratch.

Runtime prerequisites (the app will not function without these):
- `gog` CLI installed and authenticated (`gog status`, `gog auth list`).
- `GEMINI_API_KEY` exported. `GEMINI_MODEL_DEFAULT` optionally overrides the model (default `gemini-2.5-flash`).

Nix: `nix build` produces the `gogmail` package via `flake.nix`. `nixos-module.nix` exposes `programs.gogmail` (sets `GEMINI_API_KEY`, `GEMINI_MODEL_DEFAULT`, `GOG_ACCOUNT` as session vars). Dependencies are declared in **three** places that must stay in sync: `pyproject.toml`, `devenv.nix`, and `flake.nix`.

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
