# GogMail TUI

A modern, premium, feature-rich Terminal User Interface (TUI) client for Linux that brings Google Workspace and Zoom services directly into your terminal.

## Key Features

- **Integrated Services**: Contains specialized dashboards for:
  - **Gmail**: Read, search, archive, trash, compose, and reply.
  - **Calendar**: Agenda view, details panel, RSVP responses, create and delete events.
  - **Drive**: Browse, search, upload, download, create folders, and delete.
  - **Docs, Sheets, Slides, Forms**: Interactive lists, read/cat Docs, render Sheets inside a grid/table, create new documents/spreadsheets/presentations/forms.
  - **Meet**: Instantly create video conference spaces and copy links.
  - **Zoom**: Validate Server-to-Server OAuth configuration.
  - **Contacts & People**: Fast search and details card for connections.
  - **Tasks**: Checklists supporting multiple task lists, marking tasks complete/incomplete, adding and deleting.
  - **Chat**: Real-time spaces listing, messages history, and sending text chats.
- **AI Integration (Gemini)**:
  - Toggleable side panel drawer that acts as a context-aware chat partner.
  - Automatically receives context (selected email body, document text, active tasks list) depending on your current active view.
  - Email composing helper that writes replies/drafts directly based on your instructions.
- **Visual Aesthetics**: Clean, responsive layout utilizing a customized catppuccin-themed dark palette with vibrant accents.

---

## Prerequisites

1. **gog CLI**: The local CLI tool `gog` must be installed and authenticated. Check status by running `gog status` or list authenticated accounts via `gog auth list`.
2. **Gemini API Key**: Make sure the `GEMINI_API_KEY` environment variable is exported.
3. **Devenv & Nix**: A local `devenv` installation to manage Python and TUI libraries.

---

## Getting Started

### Using Devenv & Just (Recommended)

Start the TUI with a single command:
```bash
just run
```

### Alternatively, Enter Shell and Run Manually

```bash
devenv shell
python -m gogmail.app
```

---

## Key Bindings

- `q`: Quit the application.
- `F2` (or `ctrl + b`): Toggle left sidebar (Workspace Navigator).
- `F3` (or `alt + a`): Toggle right AI assistant panel.
- `alt + ←` / `alt + →` (or `alt + h` / `alt + l`): Resize the AI panel.
- `tab` / `shift + tab`: Move focus between widgets.
- `↑ / ↓`: Navigate lists, tables, and the sidebar tree.
- `enter`: Select items (in lists, email tables, etc.) or submit forms/chats.

Everything is fully **keyboard-driven** (Textual handles tab/arrow/enter focus
and navigation) **and mouse-driven** (click the sidebar, rows, and buttons;
drag the divider between the content and the AI panel to resize).

## Running in tmux

GogMail works in tmux. Two notes:

- **Mouse:** enable it in your tmux config so clicks/scroll/drag reach the app:
  ```tmux
  set -g mouse on
  ```
- **Sidebar key:** `ctrl+b` is the default tmux prefix and is swallowed by tmux,
  so use **`F2`** to toggle the sidebar (and `F3` for the AI panel) inside tmux.
- **Colors:** use a truecolor terminal; if colors look off, add
  `set -ga terminal-overrides ",*:Tc"` to your tmux config.

---

## Configuration

Settings persist to `~/.config/gogmail/settings.json` (theme, AI-panel width, last account).
The active Google account is detected from `gog status` at launch; set `GOG_ACCOUNT` to pin one.

On startup GogMail runs a preflight check — if the `gog` CLI is missing or not
authenticated, you'll get a clear, actionable message instead of silently empty
views. Any `gog` command failure during use is surfaced as an error toast rather
than looking like "no data".

---

## Development

```bash
just run     # launch the TUI
just test    # run the unit-test suite (unittest)
just lint    # syntax check (py_compile)
```

---

<p align="center"><sub>✨ Created using <a href="https://claude.com/claude-code">Claude Code</a></sub></p>
