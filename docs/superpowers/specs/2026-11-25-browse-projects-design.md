# Browse any project: design

Date: 2026-11-25. Status: approved by Anay. Small change to Mission Control v1.

## Intent
Anay wants to open any of his projects in Mission Control and read its code, not only the edits that showed up in the last 2 days of session logs.

## Goals
- A **Projects** button at the start of the tab bar opens a list of every git repo directly inside `~/code` (root overridable with `MC_CODE_ROOT`), most recently changed first.
- Picking a repo shows its full file tree (folders collapsible) in the left panel and the selected file read-only in the editor pane, with the same highlight.js highlighting.
- If a Claude session is working in that repo, files it touched are marked the same way as in the live view. Clicking a session tab returns to the live view.

## Non-goals
Editing, cross-file search, folders outside the code root, untracked files.

## Anti-goals
- Showing a secret on screen. Showing anything outside the chosen repo.
- Slowing the live view or the hook.

## Constraints and safety
- File list = `git ls-files` for the repo (tracked files only, so gitignored `.env`, `.venv`, keys never appear).
- File contents pass through `mask_text` before leaving the server.
- Requested paths must be in that repo's `git ls-files` list and must resolve (realpath) inside the repo; anything else is 404. Repo ids are folder names validated with the existing id regex.
- Binary files (NUL byte in the first 8 KB or not UTF-8) and files over 1 MB return a short note instead of contents.
- Same key auth and localhost bind as the rest of the server.

## Approaches
1. **Repos in `~/code` via git (chosen).** Clean list, git gives the file list and hides ignored files for free.
2. Project folders from session logs. Rejected: logs include temp dirs and the whole home folder.
3. Walk the filesystem with a skip list. Rejected: easy to leak `.env`-style files and slow on big repos.

## Left to the builder
API shape, tree rendering details, which session-touched marker to reuse, sort tie-breaks.
