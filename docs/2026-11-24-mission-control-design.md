# Mission Control: design

Date: 2026-11-24. Status: draft for Anay's approval. Moves into the new repo at `docs/superpowers/specs/` once approved.

## Intent

Anay runs long, heavy build sessions with Claude Code, often several sessions and subagents at once. He wants a page he keeps open beside them that lets him watch the code get written. He wants it for four reasons, all of them his:

1. It's fun to watch.
2. He can catch Claude going the wrong way early.
3. He learns from the code as it lands.
4. He can show it to other people (a portfolio piece and demo video).

He wants two things on top of watching:

- **Talk-back:** he can nudge a session from the page without stopping it.
- **A robot companion** in the corner that explains in chat bubbles what Claude is doing, edit by edit.

## Goals (v1)

- One localhost page that shows every Claude Code session on this Mac, including subagents, with an indicator for the one that's working right now.
- Each Edit/Write is replayed as fast typing in an editor-style view with syntax highlighting. A toggle switches to a diff view.
- Above the code: Claude's own most recent note explaining the edit (the assistant text that came before the tool call).
- A file tree for the selected session. The file being touched pulses.
- A running session counter: +lines / −lines, edit count, elapsed time.
- A timeline of the session's edits. Click to jump to one, or play to replay them in order (rewind).
- A robot companion (design "A · Monitor-head", picked from the mockup): a screen-face robot with an antenna light that bobs when idle, talks in bubbles, and squints when a tool call errors (for example a failing test run).
- A nudge box. The text reaches the selected session before its next step, without blocking anything. The page shows queued / delivered.
- Secrets are masked before anything leaves the server: to the browser, to the robot's model, or into any future export.

## Non-goals (v1; v2 candidates)

- Timelapse video/GIF export (v2: this is the "show people" feature, worth building once the page looks good).
- Phone access (v2: `tailscale serve` in front of the localhost port, behind the same token).
- Editing files, approving permissions, or starting/stopping sessions from the page.
- Cloud sessions or other machines.
- True token-by-token streaming. Claude Code writes each tool call whole, so "live" means the edit shows up and types out within about a second of Claude calling the tool.

## Anti-goals (would be a failure even if it works)

- Slowing Claude sessions down. The hook must be fast and fail open: any error means it outputs nothing and exits 0.
- Leaking secrets on screen or to a model host.
- Letting anyone other than Anay inject text into a Claude session. The nudge path is a prompt-injection path, so it is locked down (see Security).
- A robot so chatty it's noise, or bubbles that just restate the filename.
- Looking like a generic AI dashboard. It should feel like a real editor with a character in it.

## Success

- Anay keeps it open on build days without being asked.
- At least one wrong turn gets caught and fixed through a nudge.
- A 60-to-90-second demo video of a real build is recorded with it (v2 export makes this easy).
- Measured numbers for the README:
  - Hook overhead p95 under 30 ms per tool call.
  - Edit-in-log to on-screen latency p95 under 1 s.
  - Robot bubble latency on the local model.

## Constraints and facts already checked

- **Data source.** Claude Code writes every session to `~/.claude/projects/<project-dir>/<session-id>.jsonl`.
  - Subagents get their own files at `<project-dir>/<session-id>/subagents/agent-<id>.jsonl`, with `isSidechain: true` and `agentId`.
  - Assistant entries hold `tool_use` blocks. Their full input is there: Write has `file_path` and `content`; Edit has `file_path`, `old_string`, `new_string`.
  - Verified on this machine on 2026-11-24.
- **Nudge mechanism.** Per the hooks docs (https://code.claude.com/docs/en/hooks):
  - A `PostToolUse` hook can return `{"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": "..."}}`. That text is added to Claude's context alongside the tool result, with no block.
  - The hook input includes `session_id`, `transcript_path`, and, inside subagents, `agent_id` / `agent_type`.
  - Hooks from settings files also fire inside subagents.
  - **Unverified:** whether a subagent's hook carries the parent's `session_id`, and whether its `additionalContext` lands in the subagent or the parent. Test this first.
- **Local models available:** `qwen3-coder:30b`, `qwen3.8:27b-q8_0`, `llama3.2:3b` via Ollama.
- **Lessons that apply:**
  - Run Python through `uv`.
  - Never hardcode absolute repo paths.
  - Ask before editing `~/.claude/settings.json`.
  - Keep "built by Claude" mentions out of the code, docs and UI.
  - Run axe, a no-sideways-scroll check and 200% text in the phase that adds the page.

## Approaches considered

1. **Own repo, `~/code/mission-control`: small Python server plus one localhost page. Chosen by Anay.** It's portfolio-ready, independent of JARVIS's health, and phone access later is one command.
2. **A panel inside JARVIS on :7777.** Phone access and the server already exist, but it's tied into a large, busy codebase and is hard to show on its own.
3. **A pane in the Claude desktop app (a mod).** No browser tab, but a pane lives inside one session, which fights "see every session", and it can't be shown outside the Mac.

Data source: tailing the log files was picked over capturing via hooks. It sees every session with no setup, including past ones, which gives rewind for free. Hooks are used only for nudges.

## Decided design

**Repo:** `~/code/mission-control`, its own git repo. Python 3.12 + uv.

**Server (FastAPI + uvicorn, binds 127.0.0.1 only, default port 7788):**

- **Watcher.** Discovers `.jsonl` files under `~/.claude/projects` (root overridable by env var) and follows appends, including `subagents/`.
  - A session is "active" if its file changed in the last 2 minutes.
  - Parses `tool_use` entries named Edit, Write, MultiEdit into edit events.
  - Each event carries: session, agent, file path, kind, old/new text, Claude's preceding assistant text, and a timestamp.
  - Also notes `tool_result` entries with `is_error` so the robot can react.
- **Diffs.** Edit: old_string to new_string. Write: against the last known content of that file in the session if seen, else the whole file as added.
- **Masking.** Runs server-side on every event before anything else sees it: known key formats, `KEY=value` lines in `.env`-like files, and high-entropy tokens.
- **Robot narrator.** Calls a model with the masked edit plus Claude's note, asking for a 1-to-2 sentence plain-English bubble.
  - Defaults to local Ollama (`qwen3-coder:30b`); Haiku is optional via env var.
  - If edits arrive faster than bubbles, it narrates the latest batch rather than queuing.
  - If the model is down, the bubble falls back to Claude's own note.
- **Push.** Events go to the page over Server-Sent Events. History for rewind is read from the same files on demand.
- **Nudges.** `POST /api/nudge {session_id, agent_id?, text}` appends to `~/.claude/mission-control/inbox/<session_id>.jsonl` with a unique id. The page polls delivery status.

**Approved mockup:** `docs/mockups/robot-and-layout.html`. It's a fragment styled by the brainstorm frame; its inline `<style>` holds everything that matters. Use the page layout at the top and robot card A; ignore cards B and C.

**Calls made after the builder check (Claude's calls; Anay can override):**

- **Where nudges go: Anay's call.** When a turn is about to end with a nudge waiting, a `Stop` hook returns `{"decision": "block", "reason": "Message from Anay via Mission Control: <text>"}`, so Claude reads it and keeps working on it. It blocks only when the inbox has an unclaimed message, so it can't loop.
  - A session whose turn has already ended can't be reached. The page shows "session idle, type in the chat instead".
  - A queued nudge that isn't claimed within 10 minutes is marked expired.
- **Subagents.**
  - Inbox messages carry a target `agent_id` (`"main"` when absent). The hook claims only messages whose target matches its own `agent_id` (or `"main"` when the hook input has none), so a subagent can never swallow a nudge meant for the main session.
  - The first build step is the subagent test. If a subagent's `additionalContext` doesn't land in that subagent, the nudge box is turned off on subagent tabs in v1, with a short note on why.
- **Whole file vs fragment.**
  - Each Edit/Write's tool result in the log (`toolUseResult`) carries `structuredPatch` (hunks with context lines), and often `originalFile` (verified 2026-11-24).
  - The editor shows the whole file when `originalFile` is present, and the patch hunks with their context otherwise. It never reads the file from disk, so rewind always shows the file as it was at that edit.
  - The +/− counter comes from `structuredPatch`.
- **Bash edits** (sed, heredocs) aren't replayed in v1. They show as a one-line event in the timeline ("ran: sed -i … file.py") so they're visible but not typed out.
- **Inbox trust.** Any process running as Anay can already edit `~/.claude/settings.json` and CLAUDE.md, so signing inbox files adds nothing. The inbox dir is created with 0700 and files with 0600. The threat model is the browser and the network, which the key and origin checks cover.
- **Starting it.**
  - v1 is started by hand: `uv run mission-control`.
  - The access key is persisted in `~/.claude/mission-control/key` (0600) so a bookmarked URL keeps working.
  - The hook never depends on the server being up: it only touches inbox files, and writes delivery receipts to a `delivered/` folder the server reads.
  - A LaunchAgent is a v2 candidate.

**Hooks (`mc-hook`, tiny Python script; PostToolUse with matcher all tools, plus Stop):**

- Reads its stdin JSON and looks for that session's inbox. If it's non-empty, it claims messages atomically (rename, then read, so there's no double delivery) and prints the `additionalContext` JSON:

  > Message from Anay via Mission Control: <text>

- It records each message as delivered for the page.
- No inbox means it prints nothing and exits 0 within milliseconds. Any exception means silence and exit 0.
- **Installed by `uv run mission-control install-hook`.** It shows the exact settings.json change and asks before writing; `uninstall-hook` removes it.

**Page (vanilla JS + one HTML/CSS file, vendored highlight.js and a small diff lib, no build step):**

- Session tabs (green dot when working), file tree, editor pane with the Typing/Diff toggle, Claude's note line, +/− counter, timeline with play, nudge box with queued/delivered state, and the robot in the bottom-right corner, all matching the approved mockup.
- Typing speed scales with edit size, so any edit finishes typing in about 3 s or less.
- Unchanged context lines in an Edit appear at once; only new lines type out.
- Dark theme first.

**Security:**

- Binds to 127.0.0.1.
- A random key is generated at start and required on every request (URL key → cookie, the same pattern as the brainstorm companion).
- Cross-origin POSTs are rejected.
- Nudge text is capped at 2,000 chars.
- The robot's model only ever gets masked content and has no tools.

## Left to the builder

- Session display names (project folder, git branch, first prompt, or a mix).
- Exact masking regexes and entropy threshold.
- File-follow mechanism (polling vs `watchfiles`), SSE reconnect behavior, and how much history to load per session.
- Robot animation details beyond the mockup (idle, talk, squint states).
- Robot prompt wording, within the 1-to-2 sentence, plain-English, no-filename-parroting brief.
- Tests: a parser test against recorded `.jsonl` fixtures, masking tests, hook tests (empty inbox, claim-once, crash fails open), and one end-to-end check that a nudge reaches a real session.
