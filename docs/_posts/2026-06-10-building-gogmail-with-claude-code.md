---
title: "Building GogMail: a terminal Google Workspace client, with Claude Code"
date: 2026-06-10
author: olafkfreund
description: Why a terminal-native power user built a TUI for Gmail, Calendar, Drive and the rest — and how the build went with Claude Code in a plan/act/reflect loop.
---

I live in the terminal. Editor, shell, git, infrastructure — all of it inside
tmux. The one thing that kept dragging me back to a heavy browser tab was Google
Workspace: email, calendar, the occasional Drive file. **GogMail** is the fix —
the whole Workspace in one fast, themeable, keyboard-driven TUI.

## The idea

There's a great little CLI called [`gog`](https://github.com/steino/gogcli)
(`gogcli`) that already handles Google OAuth and exposes Gmail, Calendar, Drive,
Docs, Sheets, Slides, Forms, Tasks, Contacts, Chat and Meet as subcommands with
clean `--json` output. That's the hard part — auth and API plumbing — already
solved.

So GogMail doesn't talk to Google at all. It's a **thin, fast UI** over `gog`,
plus a Gemini side panel for summaries and drafting. Three layers:

1. **`GogAPI`** — async wrappers that shell out to `gog --json …` through a single
   `run_gog` choke point.
2. **Textual widgets** — one tab per service, each with a `refresh_*()` method.
3. **The app shell** — a tree sidebar, a content switcher, and the AI drawer.

Because *everything* funnels through one function, the whole API layer is trivial
to test by mocking `run_gog` — which mattered a lot later.

## How it was built

The entire thing was designed, debugged and hardened together with
[Claude Code](https://claude.com/claude-code), working in a disciplined
**plan → act → reflect** loop. Not "generate a big blob and hope" — small, verified
steps, each one compiled and tested before the next.

A few principles that shaped it:

- **Reliability over features.** The biggest early problem wasn't missing features
  — it was *silent* failures. A reworked error model now surfaces every `gog`
  failure as a toast, runs a preflight auth check at startup, and de-dupes noisy
  errors. (More on that in the [next post]({{ '/blog/reliability-and-bug-hunting/' | relative_url }}).)
- **Verify against reality.** When emails rendered blank, we didn't guess — we
  rendered the real UI headlessly to PNGs and replayed actual mailbox data until
  the root cause was obvious.
- **Test the seam.** A stdlib `unittest` suite mocks `run_gog` and the Gemini call,
  covering the API layer and the AI tool registry. It's wired into
  `nix flake check`.

## What it can do

- **Gmail** — read, search, compose, reply, archive, trash; with recipient
  autocomplete from your contacts.
- **Calendar** — month/week/day, create/delete/RSVP, tasks alongside.
- **Drive / Docs / Sheets / Slides / Forms** — browse, open in browser, create.
- **Tasks, Contacts, Chat, Meet, Zoom.**
- **Gemini** — context-aware assistant with tool-calling (send, search, create).
- **Multiple Google accounts**, switchable from the sidebar.
- **Six themes**, live-switchable. **Keyboard + mouse**, tmux-friendly.
- **Nix-native** — `nix run`, a dev shell, flake checks, and NixOS / Home Manager
  modules with secret-safe API keys.

## Try it

```bash
gog auth add you@example.com --services gmail,calendar,contacts,tasks,drive,chat
export GEMINI_API_KEY=AIza...
nix run github:olafkfreund/gogmail
```

It's MIT-licensed and on [GitHub]({{ site.repo_url }}). The second post digs into
the reliability work and the bugs we hunted along the way.
