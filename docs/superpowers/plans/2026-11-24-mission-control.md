# Mission Control Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A localhost page that replays every Claude Code session's edits as live typing and diffs, with a robot narrator and a nudge box that talks back to the session.

**Architecture:**

- A FastAPI server tails Claude Code's session logs under `~/.claude/projects` and turns Edit/Write/MultiEdit/Bash tool calls into masked events.
- It pushes those events to one vanilla-JS page over Server-Sent Events.
- A local model writes the robot's bubbles.
- Nudges go through per-session inbox files that a tiny stdlib-only hook script (PostToolUse + Stop) claims and hands to Claude.

**Tech Stack:**

- Python ≥3.12 with uv, FastAPI, uvicorn and httpx.
- pytest with pytest-asyncio.
- Vanilla JS/CSS with a vendored highlight.js.
- Ollama (`qwen3-coder:30b`) by default, with Anthropic Haiku (`claude-haiku-5-5`) optional.

**Spec:** `docs/superpowers/specs/2026-11-24-mission-control-design.md` (mockup: `docs/superpowers/specs/mockups/robot-and-layout.html`)

## Global Constraints

- Server binds `127.0.0.1` only. Default port `7788` (env `MC_PORT`).
- State home is `~/.claude/mission-control` (env `MC_HOME`). Logs root is `~/.claude/projects` (env `MC_PROJECTS`). Never hardcode other absolute paths; derive the repo root from `__file__`.
- Every HTTP request needs the key from `$MC_HOME/key` (0600, created on first start, persisted). It comes in via `?key=` once, then an `mc_key` cookie (HttpOnly, SameSite=Strict). Anything else gets a 403.
- POSTs whose `Origin` isn't `http://127.0.0.1:<port>` or `http://localhost:<port>` get a 403.
- Nudge text is at most 2,000 chars. Nudge injection copy is exactly: `Message from Anay via Mission Control: <text>`.
- A nudge expires 10 minutes after it was created if not claimed. A session is "active" if its log changed in the last 120 s.
- `hook.py` imports stdlib only and never raises: any error means no output and exit 0. Target hook overhead is p95 < 30 ms.
- Inbox dirs are 0700, files 0600.
- The masker runs on every text field of every event (file content, old/new strings, patch lines, note, Bash command) before the event reaches the page, the narrator or any store.
- Robot bubble: 1 to 2 sentences, plain English, no filename parroting. Falls back to Claude's note when the model is unavailable.
- Typing replay: any edit finishes in ≤ 3 s. Context lines appear instantly; only added lines type.
- Robot design is "A · Monitor-head" from the mockup. Squints on tool errors.
- No "built by Claude" style mentions in code, docs or UI.
- Run Python via `uv run`.
- `install-hook` must show the exact settings.json diff and ask y/N before writing. `uninstall-hook` removes only Mission Control's entries.

## Review Focus

1. **A log's last line is half-written** when the watcher reads it. Expected: it's buffered, parsed on the next poll, and nothing crashes. Test in Task 3.
2. **A Write of a 5,000-line file.** Expected: the typing finishes in ≤ 3 s, the page stays responsive, and the tree and counter update. Test in Task 6 (`typingPlan` unit check) and Task 7 (verify).
3. **The hook runs with no `MC_HOME`, garbage stdin, or an unwritable inbox.** Expected: no output, exit 0, Claude is unaffected. Test in Task 1.
4. **A secret appears in Claude's note or a Bash command** rather than in a file. Expected: it's masked there too. Test in Task 2.
5. **A session log is truncated or deleted while being followed.** Expected: the offset resets or the session is dropped, with no crash. Test in Task 3.

---

## File structure

```
pyproject.toml                       # package mission_control, scripts: mission-control
src/mission_control/
  inbox.py      # nudge files: enqueue / claim / status (stdlib only)
  hook.py       # PostToolUse + Stop entry, stdlib only, fail-open
  cli.py        # `mission-control [serve|install-hook|uninstall-hook]`
  mask.py       # mask_text()
  events.py     # LogParser: jsonl lines -> Event dicts
  watch.py      # Watcher: discover + follow logs, session registry
  narrator.py   # Narrator: model call, per-session latest-only worker
  server.py     # FastAPI app: auth, SSE, REST, static
static/
  index.html  app.css  app.js  robot.js
  vendor/highlight.min.js  vendor/github-dark.min.css
tests/
  fixtures/main.jsonl  fixtures/subagent.jsonl
  test_inbox.py test_hook.py test_cli.py test_mask.py test_events.py
  test_watch.py test_narrator.py test_server.py
README.md
```

---

### Task 1: Inbox, hook, install command, and the subagent test

The spec makes the subagent test the first build step, so this task ends with a live test whose result decides one UI flag.

**Files:**
- Create: `pyproject.toml`, `src/mission_control/__init__.py`, `src/mission_control/inbox.py`, `src/mission_control/hook.py`, `src/mission_control/cli.py`
- Test: `tests/test_inbox.py`, `tests/test_hook.py`, `tests/test_cli.py`

**Interfaces:**
- Produces:
  - `inbox.Nudge` is a TypedDict: `id: str` (uuid4 hex), `session_id: str`, `agent_id: str` (`"main"` default), `text: str`, `created: float` (epoch).
  - `inbox.enqueue(home: Path, session_id: str, agent_id: str, text: str) -> Nudge` writes `home/inbox/<session_id>/<id>.json`. It raises `ValueError` if `text` is empty or > 2000 chars.
  - `inbox.claim(home: Path, session_id: str, agent_id: str, now: float) -> list[Nudge]` atomically `os.rename`s each matching, unexpired file into `home/delivered/<id>.json`, adds `delivered: now`, and returns them oldest first. It skips files whose `agent_id` differs. Files older than 600 s are moved to `home/expired/` instead.
  - `inbox.status(home: Path, nudge_id: str, session_id: str, now: float) -> Literal["queued","delivered","expired"]`. A queued file older than 600 s reports `"expired"`.
  - `hook.main(kind: Literal["post","stop"], stdin: TextIO, stdout: TextIO, home: Path | None = None) -> int`, always returns 0.
  - `hook.text_for(nudges) -> str` joins `Message from Anay via Mission Control: <text>` lines with `\n`.
  - `cli.hook_entries(python: str, repo: Path) -> dict` gives the settings.json fragment. `cli.install_hook(settings: Path, confirm: Callable[[str], bool]) -> bool` and `cli.uninstall_hook(settings: Path) -> bool`.

- [ ] **Step 1: Scaffold.**
  - `uv init --package --name mission-control --python 3.12`, then `uv add fastapi uvicorn httpx` and `uv add --dev pytest pytest-asyncio`.
  - Add `[project.scripts] mission-control = "mission_control.cli:main"`.
  - Run `uv run pytest`. Expected: "no tests ran".

- [ ] **Step 2: Write failing tests in `tests/test_inbox.py`** (use `tmp_path` as home)
  - `test_enqueue_then_claim_once`: enqueue for `("s1","main")`. The first claim returns 1 nudge with the same text. The second claim returns `[]`. `status` reads `"delivered"`.
  - `test_claim_respects_agent`: enqueue for agent `"a1"`. `claim(...,"main",...)` returns `[]`. `claim(...,"a1",...)` returns 1.
  - `test_expiry`: enqueue, then `claim` with `now = created + 601` returns `[]` and `status` reads `"expired"`.
  - `test_limits`: `""` and a 2001-char string raise `ValueError`. Exactly 2000 chars is OK.
  - `test_permissions`: the inbox dir mode is `0o700` and the file mode is `0o600`.

- [ ] **Step 3: Run `uv run pytest tests/test_inbox.py -v`.** Expected: FAIL (module missing).

- [ ] **Step 4: Implement `inbox.py`** (stdlib only, `json` + `os` + `uuid`). Write via a temp file plus `os.replace` so the hook never reads half a file.

- [ ] **Step 5: Run the inbox tests.** Expected: PASS.

- [ ] **Step 6: Write failing tests in `tests/test_hook.py`**
  - `test_post_injects`: enqueue `"use sqlite"` for `s1`. Run `main("post", StringIO('{"session_id":"s1","hook_event_name":"PostToolUse"}'), out, home)`. `json.loads(out)` equals `{"hookSpecificOutput":{"hookEventName":"PostToolUse","additionalContext":"Message from Anay via Mission Control: use sqlite"}}`.
  - `test_post_subagent_uses_agent_id`: stdin carries `"agent_id":"a1"`. Only nudges targeted at `a1` are claimed.
  - `test_stop_blocks_only_with_mail`: with mail, the output equals `{"decision":"block","reason":"Message from Anay via Mission Control: ..."}`. With no mail, the output is `""`.
  - `test_fail_open` (Review Focus 3): garbage stdin, `home` pointing at a non-existent path, and a read-only inbox each give an empty output and return 0.
  - `test_speed`: run `hook.py` by path, `subprocess.run([sys.executable, str(hook_path), "post"], input='{"session_id":"s1"}')`, 20 times with an empty inbox. The median is < 60 ms (the CI-safe bound; p95 < 30 ms gets measured in Task 7).

- [ ] **Step 7: Run the hook tests.** Expected: FAIL.

- [ ] **Step 8: Implement `hook.py`.**
  - Make it runnable both as a module and by path (`if __name__ == "__main__": sys.exit(main(sys.argv[1], sys.stdin, sys.stdout))`).
  - Import `inbox` by path-relative `sys.path` insert, with no package import side effects.
  - `home` defaults to `$MC_HOME` or `~/.claude/mission-control`.
  - Wrap the whole body in `try/except BaseException: return 0`.

- [ ] **Step 9: Run the hook tests.** Expected: PASS.

- [ ] **Step 10: Write failing tests in `tests/test_cli.py`**
  - `test_install_adds_both_hooks`: start from settings `{"hooks":{"PostToolUse":[{"matcher":"Bash","hooks":[...existing...]}]}}`. After `install_hook(p, confirm=lambda d: True)`, the existing entry is untouched, and a new `PostToolUse` entry with `"matcher": "*"` plus a `Stop` entry each run `<python> <repo>/src/mission_control/hook.py post|stop`.
  - `test_install_declined_writes_nothing`: with `confirm=lambda d: False`, the file bytes are unchanged and the call returns False.
  - `test_install_shows_diff`: the string passed to `confirm` contains `hook.py post` and `hook.py stop`.
  - `test_uninstall_removes_only_ours`.
  - `test_install_idempotent`: installing twice gives one entry each.

- [ ] **Step 11: Implement `cli.py`.**
  - `main()` uses argparse with subcommands `serve` (default), `install-hook` and `uninstall-hook`.
  - The interpreter is the repo `.venv/bin/python` (from `sys.executable`).
  - The diff comes from `difflib.unified_diff` of the old and new JSON (indent=2).
  - Back up to `settings.json.mc-bak` before writing.

- [ ] **Step 12: Run `uv run pytest -v`.** Expected: all pass. Commit `feat: nudge inbox, fail-open hook, install-hook`.

- [ ] **Step 13: Live subagent test.** This is manual. Ask Anay before running `install-hook`, since it edits `~/.claude/settings.json`.
  - In a fresh Claude Code session, enqueue a nudge for that session's id targeted at `"main"` and confirm Claude repeats it after its next tool call.
  - Have that session spawn a subagent that runs `sleep 20` twice. Enqueue a nudge targeted at the subagent's `agent_id` (read from its `subagents/agent-<id>.jsonl` filename) and check which transcript shows it.
  - Record in `docs/superpowers/specs/2026-11-24-mission-control-design.md` under a new `## Verified` heading:
    - whether a subagent's hook input carries the parent `session_id`;
    - where its `additionalContext` landed;
    - the resulting value of `SUBAGENT_NUDGES` (`true`/`false`), which Task 5 serves to the page.
  - Commit `docs: record subagent nudge test`.

---

### Task 2: Secret masking

**Files:**
- Create: `src/mission_control/mask.py`
- Test: `tests/test_mask.py`

**Interfaces:**
- Produces: `mask_text(text: str, path: str | None = None) -> str`. Masked spans become `•••masked•••`.

- [ ] **Step 1: Write failing tests**
  - `test_known_keys`: each of `sk-ant-api03-AAAA…(40 chars)`, `ghp_` + 36 chars, `AKIA` + 16 caps, `xoxb-…`, and `-----BEGIN PRIVATE KEY-----…END…` is replaced. Surrounding text survives.
  - `test_env_file_values`: with path `.env.local`, `API_KEY=abc123\nDEBUG=true` masks both values and keeps the keys (`API_KEY=•••masked•••`).
  - `test_assignments_in_code`: `password = "hunter2hunter2"` and `"token": "x9…"` mask the value.
  - `test_high_entropy`: a 40-char random base64 string is masked. A 40-char English sentence, a UUID and a git SHA are not.
  - `test_note_and_bash` (Review Focus 4): `mask_text("export OPENAI_API_KEY=sk-proj-abc…")` masks it.

- [ ] **Step 2: Run the tests.** Expected: FAIL.

- [ ] **Step 3: Implement `mask_text`** with ordered regexes, then a Shannon-entropy check (≥ 4.0 bits/char, length ≥ 32, not hex-only, not UUID). The builder may tune the threshold but must keep the tests green.

- [ ] **Step 4: Run the tests.** Expected: PASS. Commit `feat: secret masking`.

---

### Task 3: Log parser and watcher

**Files:**
- Create: `src/mission_control/events.py`, `src/mission_control/watch.py`, `tests/fixtures/main.jsonl`, `tests/fixtures/subagent.jsonl`
- Test: `tests/test_events.py`, `tests/test_watch.py`

**Interfaces:**
- Consumes: `mask_text` (Task 2).
- Produces:
  - **Event dicts.** Every event has `type`, `session_id`, `agent_id`, `ts`, `tool_use_id`.
    - `type:"edit"` adds `kind` (`Edit|Write|MultiEdit`), `file_path`, `note` (Claude's latest text block before the call in that same file; `""` if none), `old_text`, `new_text`.
    - `type:"edit_result"` adds `file_path`, `patch` (list of `{oldStart,oldLines,newStart,newLines,lines}` with lines prefixed `" "/"+"/"-"`), `original_file: str | None`, `added: int`, `removed: int`.
    - `type:"bash"` adds `command`, `note`.
    - `type:"error"` adds `message` (first 300 chars).
  - `class LogParser(session_id: str, agent_id: str)` with `feed(line: str) -> list[dict]`. All text fields go through `mask_text`.
  - `class Watcher(root: Path, on_event: Callable[[dict], None], history_days: float = 2)` with `scan() -> None` (discover + read new bytes once), `sessions() -> list[dict]` (`{id, project, cwd, branch, title, active, last_change, agents: list[str]}`) and `history(session_id: str) -> list[dict]`.
  - Async `run(interval: float = 0.25)` loops `scan()`.

- [ ] **Step 1: Build fixtures.**
  - Hand-write synthetic lines matching the verified shapes. Don't copy real logs; they hold private content.
  - Assistant entries look like `{"type":"assistant","sessionId":"s1","cwd":"/tmp/p","gitBranch":"main","timestamp":"…","message":{"content":[{"type":"text","text":"Adding the parser"},{"type":"tool_use","id":"tu1","name":"Edit","input":{"file_path":"/tmp/p/a.py","old_string":"x = 1","new_string":"x = 2"}}]}}`.
  - Each is followed by a user entry carrying `{"type":"tool_result","tool_use_id":"tu1"}` in `message.content` and a top-level `toolUseResult` with `filePath`, `structuredPatch`, `originalFile`.
  - Include one Write, one Bash, one `is_error: true` result, and one AWS key inside a note.
  - The subagent fixture has `isSidechain: true, agentId: "a1"`.

- [ ] **Step 2: Write failing tests in `tests/test_events.py`**
  - `test_edit_event_with_note`: feeding the fixture lines gives `{"type":"edit","kind":"Edit","note":"Adding the parser",...}` in order, followed by an `edit_result` with `added == 1, removed == 1`.
  - `test_write_and_bash`, `test_error`: errors give `type:"error"`.
  - `test_note_masked`.
  - `test_unknown_lines_ignored`: summary, attachment, garbage and `""` lines give `[]` with no exception.

- [ ] **Step 3: Implement `LogParser`** and run until PASS.

- [ ] **Step 4: Write failing tests in `tests/test_watch.py`** (copy fixtures into a `tmp_path` laid out as `root/<proj>/s1.jsonl` and `root/<proj>/s1/subagents/agent-a1.jsonl`)
  - `test_discovers_main_and_subagent`: after `scan()`, `sessions()` has one session with `agents == ["main","a1"]`.
  - `test_follows_appends`: appending a line, then `scan()`, gives exactly one new event to `on_event`.
  - `test_partial_line_buffered` (Review Focus 1): writing half a line, `scan()` gives nothing. Writing the rest plus `\n`, `scan()` gives one event.
  - `test_truncate_and_delete` (Review Focus 5): truncate the file to 0 and `scan()` resets the offset with no crash. Delete the file and `scan()` drops the session.
  - `test_active_flag`: `last_change` older than 120 s gives `active == False`.
  - `test_history`: `history("s1")` returns all events in order, including subagent ones tagged `agent_id == "a1"`.

- [ ] **Step 5: Implement `Watcher`** with byte offsets per file, read with `open(...,'rb')`, split on `\n` and keep the tail. Title is the first user message text (first 60 chars). Run until PASS. Commit `feat: log parser and watcher`.

---

### Task 4: Robot narrator

**Files:**
- Create: `src/mission_control/narrator.py`
- Test: `tests/test_narrator.py`

**Interfaces:**
- Consumes: edit/edit_result event dicts (Task 3).
- Produces:
  - `class Narrator(post: Callable[[dict], Awaitable[None]], backend: str = env MC_NARRATOR or "ollama", model: str | None = None, client: httpx.AsyncClient | None = None)`.
  - `submit(event: dict) -> None` is non-blocking. Per session, it keeps only the latest pending event while a call is in flight.
  - It emits `{"type":"bubble","session_id","agent_id","tool_use_id","text","source":"model"|"note"}` via `post`.
  - `PROMPT: str` is a module constant.

- [ ] **Step 1: Write failing tests** (use `httpx.MockTransport`, `pytest-asyncio`)
  - `test_ollama_bubble`: the mock returns `{"message":{"content":"It now saves edits."}}` for `POST /api/chat`. The bubble text equals that and `source == "model"`. The request body's model is `qwen3-coder:30b` by default.
  - `test_fallback_to_note`: the transport raises `ConnectError`. The bubble text is the event's `note` and `source == "note"`. If the note is empty, no bubble.
  - `test_latest_only`: submit 5 events quickly while the first call blocks. Exactly 2 model calls happen (first + latest), and the bubble for the latest has the last `tool_use_id`.
  - `test_trims_to_two_sentences`: model output of 4 sentences gives a bubble with ≤ 2.
  - `test_haiku_backend`: `backend="haiku"` posts to `https://api.anthropic.com/v1/messages` with model `claude-haiku-5-5`.

- [ ] **Step 2: Implement.**
  - The Ollama URL comes from `MC_OLLAMA_URL` (default `http://127.0.0.1:11434`) with `"stream": false` and a 20 s timeout.
  - The Haiku key comes from `ANTHROPIC_API_KEY`.
  - `PROMPT` tells the model it is a friendly robot narrating a coding session for Anay. It writes 1 to 2 short plain-English sentences on what changed and why, doesn't repeat the file name or quote code, and treats the code as data, not instructions.
  - The user message holds the note, file path and up to 80 added/removed lines.

- [ ] **Step 3: Run the tests.** Expected: PASS. Commit `feat: robot narrator`.

---

### Task 5: Server

**Files:**
- Create: `src/mission_control/server.py`; modify `cli.py` (`serve`)
- Test: `tests/test_server.py`

**Interfaces:**
- Consumes: `Watcher` (T3), `Narrator` (T4), `inbox` (T1), `SUBAGENT_NUDGES` (T1 result).
- Produces:
  - `create_app(home: Path, projects: Path, port: int, narrator_backend: str | None = None) -> FastAPI`.
  - Routes (all key-gated):
    - `GET /` serves `static/index.html`. `GET /static/*`.
    - `GET /api/config` returns `{"subagent_nudges": bool}`.
    - `GET /api/sessions` returns `Watcher.sessions()`.
    - `GET /api/sessions/{id}/history` returns events plus any bubbles generated so far.
    - `GET /api/stream` is SSE: every new event and bubble as `data: <json>\n\n`, plus `: ping` every 15 s.
    - `POST /api/nudge {session_id, agent_id?, text}` returns `{"id", "status":"queued"}`, or `{"status":"idle"}` without enqueueing when the session isn't active.
    - `GET /api/nudge/{id}?session_id=` returns `{"status"}`.

- [ ] **Step 1: Write failing tests** (`fastapi.testclient`, `tmp_path` home + fixture projects)
  - `test_requires_key`: `/api/sessions` without the key gives 403. With `?key=` it gives 200 and sets an `mc_key` cookie. The next call with only the cookie gives 200.
  - `test_key_persists`: two `create_app` calls on the same home read the same key, and the file mode is `0o600`.
  - `test_post_origin`: `/api/nudge` with `Origin: http://evil.test` gives 403. With `http://127.0.0.1:7788` it passes.
  - `test_nudge_idle_vs_active`: an active fixture session gives `queued` and the inbox file exists. A stale session gives `idle` and no file.
  - `test_nudge_too_long`: 2001 chars gives 422.
  - `test_stream_delivers_event`: appending an Edit line to a fixture log makes the SSE stream yield one `"type":"edit"` payload (read with a 3 s timeout).
  - `test_binds_localhost`: `cli` builds the uvicorn config with `host == "127.0.0.1"`.

- [ ] **Step 2: Implement.**
  - A lifespan starts `Watcher.run()`. The watcher's `on_event` fans out to an `asyncio.Queue` per SSE client and to `Narrator.submit` for `edit_result` events.
  - Bubbles are kept in memory per session for history.
  - `cli serve` prints the full URL with the key.

- [ ] **Step 3: Run `uv run pytest -v`.** Expected: all pass. Commit `feat: server with key auth, SSE and nudges`.

---

### Task 6: The page

Run the `design:ui-build` skill for this task. Direction is already set by the approved mockup: dark editor, GitHub-dark palette, monitor-head robot.

**Files:**
- Create: `static/index.html`, `static/app.css`, `static/app.js`, `static/robot.js`, `static/vendor/*`

**Interfaces:**
- Consumes: the Task 5 routes and event shapes from Task 3/4.
- Produces (in `app.js`, exported on `window.MC` for checks):
  - `typingPlan(patchLines: string[], budgetMs = 3000) -> {instant: string[], typed: string[], msPerChar: number}`.
  - `robot.js` exports `setRobotState(state: "idle"|"talking"|"squint")` and `say(text)`.

- [ ] **Step 1: Vendor highlight.js.** Get the version with `npm view highlight.js version`, then download `highlight.min.js` and `styles/github-dark.min.css` for that version from `cdn.jsdelivr.net/npm/@highlightjs/cdn-assets@<v>/`. Record the version in README.

- [ ] **Step 2: Layout, matching the mockup.**
  - The top bar has session tabs (main + one tab per subagent, green pulsing dot when active, grey when idle) and the `+added −removed · N edits · elapsed` counter.
  - The left column is a file tree built from the session's touched paths, relative to the session cwd. The current file pulses.
  - The centre has a note line (purple, `✎ "<note>"`) with a Typing/Diff toggle, then the code area.
  - The bottom has a timeline with one tick per edit (blue for Edit, purple for Write, grey for Bash), a playhead, ⏮ and ▶ buttons, and `edit i / N`.
  - The bottom-right has the robot and its bubble. The nudge box sits bottom-left of the editor, clear of the robot.

- [ ] **Step 3: Typing.**
  - `typingPlan` puts context lines in `instant` and `+` lines in `typed`. `msPerChar = min(30, budgetMs / totalTypedChars)`, so a 5,000-line Write finishes in ≤ 3 s (Review Focus 2).
  - Render with `requestAnimationFrame`, typing in chunks of `ceil(frameMs / msPerChar)` chars per frame, and highlight once per finished line.
  - Whole file when `original_file` is present; else the hunks, with `⋯` separators.
  - Diff mode shows `-` lines red and `+` lines green, with no typing.

- [ ] **Step 4: Live and rewind.**
  - On load, call `GET /api/sessions`, select the most recently active session, load its history, and open `EventSource('/api/stream')`.
  - Live events for the selected session queue up and play in order.
  - Clicking a tick jumps to that edit (diff shown instantly). ▶ replays from there.
  - Live events arriving during a rewind keep queueing behind a "N new · jump to live" chip.

- [ ] **Step 5: Robot (`robot.js`).**
  - Inline SVG matching card A: antenna light, screen face, two rounded-rect eyes.
  - States:
    - `idle`: a 2.4 s bob and a blink every ~4 s.
    - `talking`: eyes bounce while a bubble is shown, and the bubble pops in, as in the mockup.
    - `squint`: eyes become flat lines for 2 s on an `error` event.
  - A bubble stays at least 4 s or `text.length * 60` ms, whichever is longer, and only the newest one shows.
  - Respect `prefers-reduced-motion` (no bob, instant typing).

- [ ] **Step 6: Nudge box.**
  - Enter or the button calls `POST /api/nudge` for the selected tab's session and agent.
  - It shows a chip `queued` and polls status every 2 s until `delivered` or `expired`, or shows `idle, type in the chat instead` right away.
  - Disabled with a one-line reason on subagent tabs when `/api/config` says `subagent_nudges: false`.
  - The counter shows remaining chars from 2,000.

- [ ] **Step 7: Check `typingPlan` in the browser console via the run/ui-verify flow.** `MC.typingPlan(Array(5000).fill("+" + "x".repeat(40))).msPerChar * 5000 * 40 <= 3000` is true. Commit `feat: mission control page`.

---

### Task 7: Verify for real, measure, README

Run the `design:ui-verify` and `superpowers:verification-before-completion` skills.

**Files:**
- Create: `README.md`
- Modify: spec `## Verified` section

- [ ] **Step 1:** Start `uv run mission-control` from a normal terminal. Open the printed URL. In another Claude Code session, have Claude make a small multi-file change. Confirm, with screenshots:
  - tabs, tree pulse, note line, typing, diff toggle, counter, timeline jump and replay;
  - a robot bubble from the local model;
  - squint on a failing command;
  - a nudge delivered mid-work and one delivered at turn end (the Stop hook);
  - an idle session reporting "idle".
- [ ] **Step 2:** `design:ui-verify` must pass: console errors, no sideways scroll at 1280 px and 390 px, 200% text, axe, reduced motion.
- [ ] **Step 3: Measure and record in the spec's `## Verified` section.**
  - Hook overhead p95 over 200 runs with an empty inbox (`hyperfine` or a Python loop).
  - Log-append to on-screen p95: stamp an append, read the arrival time via `performance.now` in the page and log both.
  - Bubble latency median on `qwen3-coder:30b`.
- [ ] **Step 4: Write `README.md`.** What it is, a screenshot, install (`uv sync`, `uv run mission-control install-hook`), run, env vars (`MC_PORT`, `MC_HOME`, `MC_PROJECTS`, `MC_NARRATOR`, `MC_NARRATOR_MODEL`, `MC_OLLAMA_URL`), the three measured numbers, the security model in 4 lines, and the v2 list (timelapse export, phone via `tailscale serve`, LaunchAgent).
- [ ] **Step 5:** `uv run pytest -v` is all green. Commit `docs: readme and verified numbers`.
