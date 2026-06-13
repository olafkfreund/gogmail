# Contributing to GogMail TUI

Thanks for your interest in improving GogMail! This guide covers how to get a
development environment running, how to test and lint, the architecture you'll
be working within, and what we expect from a pull request.

## Getting set up

GogMail is developed inside a [Nix](https://nixos.org/) / [devenv](https://devenv.sh/)
environment so everyone gets the same toolchain (including the `gog` CLI and
clipboard/browser/image helpers wired onto `PATH`).

```bash
# Clone
git clone https://github.com/olafkfreund/gogmail
cd gogmail

# Enter the dev shell (devenv via direnv, or directly):
devenv shell                 # then: python -m gogmail.app
# …or with the flake:
nix develop                  # dev shell
```

Common tasks are exposed through [`just`](https://github.com/casey/just):

```bash
just run     # run the app   (devenv shell run-app → PYTHONPATH=src python -m gogmail.app)
just test    # run the test suite
just lint    # py_compile syntax check over src/
```

### Runtime prerequisites

The app is a **thin TUI over external services** and will not function without:

- The **`gog` CLI** installed and authenticated — check with `gog status` and
  `gog auth list`. The Nix package wraps `gogcli` onto `PATH`; a `gog` already on
  `PATH` wins. The TUI never holds Google credentials — `gog` owns auth.
- **`GEMINI_API_KEY`** exported (or `geminiApiKeyFile` via the Nix modules) for the
  AI assistant. `GEMINI_MODEL_DEFAULT` optionally overrides the model.

You can develop most of the codebase without live credentials because the test
suite mocks the external boundary (see below).

## Architecture in one minute

The code is **three strictly-separated layers** — keep changes within the right one:

1. **API layer** (`src/gogmail/gog_api.py`, `src/gogmail/gemini_api.py`)
   `GogAPI` is a class of `@staticmethod async` methods, one per Workspace
   operation, each delegating to the module-level `run_gog(args, parse_json=True)`,
   which spawns `gog --json …`. Mutations return `(success, result)`; reads return a
   plain `list`/`dict`/`str` (empty on failure). Failures auto-surface to the user
   via a global **error sink** so a failed call never looks like "no data".
   `GeminiAPI` wraps blocking `requests` in `asyncio.to_thread`.

2. **Widget / screen layer** (`src/gogmail/tui/`)
   One `*Tab(Vertical)` class per service in `widgets.py`, each with an
   `async refresh_*()` that calls `GogAPI` and repopulates its widgets — call it
   after any mutation. `ModalScreen` dialogs live in `screens.py`. All styling is
   in `styles.tcss`, keyed by theme-prefix class.

3. **App shell** (`src/gogmail/app.py`)
   `GogMailApp(App)` — sidebar `Tree` → `ContentSwitcher` of tabs → AI panel.
   Status is centralized in `notify_status(...)`; mutations run through
   `_run_mutation(...)` for consistent status + refresh.

The Gemini assistant is **agentic** via a prompt-driven JSON protocol. **To add a
new AI tool, append one entry to the `TOOLS` registry** (with an `async def _tool_*`
handler) in `app.py` — both the prompt schema and the dispatch derive from it, so
they can't drift.

See [`CLAUDE.md`](CLAUDE.md) for the full architecture notes.

## Testing

Tests use the **stdlib `unittest`** (no pytest dependency):

```bash
just test
# equivalent to: python -m unittest discover -s tests
```

The single mockable seam is **`gog_api.run_gog`** — patch it to feed canned
`(success, result)` tuples instead of shelling out to the real `gog` CLI. This is
how the suite exercises the API and widget layers without live Google credentials.

When you add a `GogAPI` method, match the existing contract: mutations return
`(success, result)`, reads return an empty `list`/`dict`/`str` on failure (failures
already surface through the error sink — don't swallow them).

> Python dependencies are declared in **three** places that must stay in sync:
> `pyproject.toml`, `devenv.nix`, and `flake.nix`. Update all three when adding a dep.

## Code style

- Follow the patterns already in the surrounding code; keep the three layers
  separate.
- `GogAPI` operations are `async`. After any mutating action, refresh the owning
  tab (`_run_mutation`'s `refresh` argument handles this for dialogs).
- Prefer small, focused, reversible changes. Read existing code before modifying it.
- Add a comment when the *why* isn't obvious; don't comment the obvious *what*.

## Submitting a pull request

Before opening a PR, make sure:

- [ ] `just test` is green (add tests for new behavior, using the `run_gog` seam).
- [ ] `just lint` passes.
- [ ] `nix flake check` passes if you touched packaging or have Nix available.
- [ ] Your change stays within the appropriate architectural layer.

For the PR itself:

- Use a clear, conventional-ish commit message — a short imperative summary,
  optionally prefixed with a scope (e.g. `Gmail: add label filtering`,
  `Calendar: fix week-view off-by-one`).
- Describe **what** changed and **why** in the PR body; link any related issue.
- Include screenshots for UI changes when practical.
- Keep PRs focused — one logical change per PR is easier to review.

By contributing you agree that your contributions are licensed under the project's
MIT license.
