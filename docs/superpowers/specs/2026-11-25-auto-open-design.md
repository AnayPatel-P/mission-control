# Auto-open: design

Date: 2026-11-25. Status: approved by Anay (vet 18/25, "Opener + LaunchAgent").

## Intent
When Claude starts changing code, Mission Control should already be on screen, so Anay actually keeps it open on build days.

## Decided
- **Trigger:** a live `edit` or `edit_result` (never startup history) in any session, only when all of these hold: no browser page holds the live feed; no page has connected or disconnected in the last 60 s (and the server has been up 60 s), so restarts, reloads and reconnects don't count as "closed"; a page has been on screen (`POST /api/seen` on load and on becoming visible) since the last auto-open, so a background tab that was never looked at or got discarded doesn't trigger another; and 30 minutes since the last auto-open.
- **Open:** `open -g <url>` on macOS only, so it lands behind the current window. URL is `/?key=<key>#session=<session_id>`; the page selects that session's tab from the hash, then clears it.
- **Switch:** `MC_AUTO_OPEN=off` disables it. Tests and `create_app` default to off.
- **LaunchAgent:** `mission-control install-agent` writes `~/Library/LaunchAgents/com.mission-control.server.plist` (runs `<repo>/.venv/bin/mission-control serve`, RunAtLoad, KeepAlive, logs in `~/Library/Logs/mission-control/`, ThrottleInterval 60), refuses if the repo's `.venv` is missing, shows it, asks y/N, then boots out any running copy by label and `launchctl bootstrap gui/<uid>` (one retry; failure is reported, not printed as success). The agent doesn't inherit shell env vars such as `MC_NARRATOR` or `ANTHROPIC_API_KEY`. `uninstall-agent` boots it out and deletes the file.

## Approaches
1. Server-side opener gated on open pages + LaunchAgent (chosen): one place knows both "an edit happened" and "a page is open".
2. Open from the hook: rejected, the hook must stay stdlib, fast and fail-open, and can't tell whether a page is open.
3. Desktop-app pane like session-hub: rejected for now, it lives in one chat and fights "see every session".

## Anti-goals
Tab spam, stealing focus, a hook that slows sessions.

## Plan (native, TDD)
1. `opener.py`: `AutoOpener(url_for, pages_open, cooldown_s=1800, launch=..., clock=time.monotonic)` with `maybe_open(session_id) -> bool`. Tests: opens when no pages; not when a page is open; not twice inside cooldown; again after cooldown; launch failure is swallowed. Wire into `on_event` behind `state["live"]`; server test that history replay never opens and a live edit does.
2. `cli.py`: `agent_plist(repo) -> bytes`, `install_agent(path, confirm, launchctl)`, `uninstall_agent(path, launchctl)`; commands `install-agent` / `uninstall-agent`. Tests with a fake launchctl and tmp paths.
3. `app.js`: `#session=<id>` selects that tab on load (falls back to the newest session), then clears the hash. Checked in the browser pane.
4. Install the agent, stop the hand-started server, trigger a real edit with the page closed, confirm it opens once.
