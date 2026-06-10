---
title: "Reliability and bug-hunting: why GogMail never shows you a blank screen"
date: 2026-06-10
author: olafkfreund
description: The unglamorous work that makes a TUI trustworthy — surfacing errors, a startup preflight, and three real bugs behind blank emails (and how we found them headlessly).
---

A demo that looks good and an app you *trust* are different things. Most of the
work on GogMail after the first working version went into the second — making it
honest about failure and impossible to leave you staring at a blank pane. Here's
the interesting part.

## Silent failure was the real enemy

The original API layer swallowed errors: every read returned `[]`/`{}` on failure.
So an expired `gog` token, a network blip, or a bad query all looked **identical
to an empty inbox**. You couldn't tell "no mail" from "the backend just broke."

The fix was a small but high-leverage change: a global **error sink**. Every
`run_gog` failure (including a missing `gog` binary) is logged *and* pushed to the
UI as an error toast — de-duplicated so an optional, unconfigured service (like
Chat without the API enabled) reports once instead of nagging. On top of that, a
**startup preflight** verifies `gog` is installed and authenticated and resolves
your real account, so you get an actionable message instead of twelve silently
empty tabs.

## Three bugs behind "this email is blank"

Blank emails turned out to be **three** separate root causes — found by testing
against a real mailbox, not by reading code:

1. **The HTML parser leaked state.** `<meta>` and `<link>` are *void* tags (no
   closing tag), but the parser pushed an "ignore" depth on them and never popped
   it — so after the `<head>`, the entire body was skipped. Newsletter emails with
   several `<meta>` tags rendered to nothing.
2. **Thread IDs aren't message IDs.** Gmail search returns *thread* IDs; fetching
   one as a *message* works only for single-message threads. Multi-message threads
   (common in Sent) 404'd. Now we resolve the thread's latest message and fetch
   that.
3. **Rich markup in email content.** The detail pane interprets markup, so an email
   containing something like `[/x]` raised a `MarkupError` and aborted the write —
   blank body. Everything user-supplied is now escaped; intended styling is kept.

Layered on top: `best_email_text()` tries rendered HTML → plain-text body → a
brute-force tag-strip, so **no email can render blank**, and a loading spinner
shows while a slow message fetches.

## Verifying by *looking*, headlessly

The trick that made this fast: Textual can run an app headlessly and export an
**SVG screenshot**. So instead of guessing whether a fix worked, we rendered the
real detail pane to a PNG and looked at it — confirming the previously-blank
Evri newsletter and a 9-message Sent thread both rendered fully. The same
technique caught a UI bug where two header bars had "black notches" (Label widgets
without `width: 100%`).

> Lesson: when a UI bug is visual, make the loop *see* the UI. A screenshot beats
> a thousand assertions about `widget.lines`.

## Quality passes

We also ran the codebase through reuse/simplification/efficiency cleanups —
collapsing duplicated Gemini calls into one, extracting `gog`-result helpers,
unifying the AI tools into a single registry that generates the prompt *and*
drives dispatch (so they can't drift), and giving the four Drive document tabs a
shared base class.

The result is a TUI that fails loudly when it should, recovers gracefully when it
can, and is covered by a test suite wired into `nix flake check`. Trustworthy
beats flashy.

— Built with [Claude Code]({{ "https://claude.com/claude-code" }}).
