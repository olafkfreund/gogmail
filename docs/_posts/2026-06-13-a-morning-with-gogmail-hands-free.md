---
title: "A morning with GogMail, hands-free: voice, an agentic assistant, and a Zoom call without leaving the terminal"
date: 2026-06-13
author: olafkfreund
description: A showcase scenario — clearing the inbox, planning the day, and starting a meeting using GogMail's push-to-talk voice control, its read-and-act Gemini assistant, and one-keystroke Zoom, all inside tmux.
---

The first version of GogMail could *do* things — send mail, create events, add
tasks. What it couldn't do was *show* you anything through the assistant, and
talking to it meant typing. This release changes both. Here's a concrete morning
to show how it fits together.

## 08:55 — "What's waiting for me?"

I open GogMail in a tmux pane, click the **🎙️ Talk** button in the Gemini drawer,
and say:

> "Show me my latest emails."

The clip is captured by the mic, sent to Gemini to transcribe, and the transcript
flows into the same assistant loop as if I'd typed it. A second later the
**Gmail view opens, populated** — and the assistant adds a one-line summary in the
panel instead of dumping the whole list:

> *Showing your inbox — 6 unread. Two look time-sensitive: a contract from Legal
> and a reschedule request from Jordan.*

That's the important change: the assistant now has **read tools**. "Show me my
latest emails", "what's on my calendar this week", "what are my open tasks",
"find the doc about the Q3 roadmap" — each fetches real data through the `gog`
CLI, summarizes it, **and opens the matching client tab** so the data lives where
you'd expect it, not buried in a chat log.

## 09:01 — Planning the day by voice

> "What's on my calendar this week?"

The Calendar tab opens on the week and the assistant calls `gog calendar events
--week` under the hood — relative dates like "this week" just work, because the
assistant is told the current time and resolves them itself. Then:

> "What are my open tasks?"

The Tasks tab fills in. If spoken replies are enabled in **Settings**, the
assistant answers out loud — and not in the old robotic monotone. It now uses
Google's **Gemini TTS** (the `gemini-3.1-flash-tts-preview` voice), played back
through your system audio, with `espeak` kept only as an offline fallback.

## 09:08 — Triage, the docked way

Jordan wants to reschedule. I hit **Reply** and the compose window slides in as a
**right-docked panel** — Gmail-web style — so I can still see the thread behind
it. Need more room? The **⛶ Fullscreen** toggle expands it to the whole client and
back. I let Gemini draft the reply ("propose Thursday 2pm, keep it warm"), tweak a
line, and send.

The contract from Legal goes into a folder: the **Label** button opens a picker of
my existing labels with a "Move here (also remove from Inbox)" option — or I type a
new label name and it's created on the spot. Moving it drops me straight back to
the inbox list.

## 09:15 — "Start a meeting"

The standup is now. Instead of hunting for a link, I open the **Zoom** tab and
click **Create Meeting**. GogMail mints a Server-to-Server OAuth token, creates an
instant meeting via the Zoom REST API, copies the join link to my clipboard for
the team, and opens the host URL so my desktop Zoom client launches straight into
the call. (Prefer Google Meet? One click in the Meet tab does the same and hands
back a clean link — no JSON blob.)

All of that happened in one terminal pane, most of it without touching the
keyboard.

## What made it possible

None of this required a new architecture — it reused the one the project started
with:

- **The assistant was already agentic.** A single `TOOLS` registry drives both the
  prompt the model sees and the dispatch, and the loop already fed a tool's output
  back to the model. Adding "show me X" was mostly adding *read* tools whose
  handlers return capped, formatted data — and a side effect that navigates to the
  right view.
- **Voice is just a front-end.** Recording shells out to `arecord`/`ffmpeg`/`sox`
  (no native audio dependency); transcription and the natural voice both go through
  the Gemini key you already have. Speech becomes the text that drives the existing
  tools, so every capability is voice-addressable for free.
- **Everything stays honest and tested.** Read handlers cap their output so they
  can't blow the context window; tool calls are only dispatched from fenced or
  whole-message JSON so a crafted email subject can't trick the assistant into a
  destructive action; and the whole thing is covered by a `unittest` suite wired
  into `nix flake check`, including a NixOS VM test for the module.

GogMail still doesn't talk to Google directly — `gog` owns auth, Gemini handles AI
and now voice, and the Zoom REST API handles meetings. The terminal just got a lot
more conversational.
