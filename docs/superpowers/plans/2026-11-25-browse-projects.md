# Browse Any Project Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Projects button that lists every git repo in the code root and shows any tracked file read-only, masked, in the existing editor pane.

**Architecture:** A new stdlib module `repos.py` lists repos and reads files through `git ls-files`. Three GET endpoints in `server.py` expose it behind the existing key middleware. The page gains a browse mode that swaps the tree and editor content and hides the live-only controls.

**Tech Stack:** Python 3.12 (subprocess `git`), FastAPI, vanilla JS, highlight.js (already vendored).

**Spec:** `docs/superpowers/specs/2026-11-25-browse-projects-design.md`

## Global Constraints
- Code root = `MC_CODE_ROOT` env, default `~/code`. Repos = direct children with a `.git` entry.
- File list = `git -C <repo> ls-files -z` only. Untracked and ignored files never listed or served.
- Every served file body goes through `mask_text(text, path)`.
- Max file size 1 MB (1_048_576 bytes). Binary = NUL in first 8 KB or UTF-8 decode error.
- Read-only. No new dependencies. Same key auth and 127.0.0.1 bind.
- Copy: no em dashes.

## Review Focus
1. Repo folder names with dots (`anaypatel-p.github.io`) must work; `..`, `.`, slashes and names not in the listing must 404.
2. A tracked path that is a symlink pointing outside the repo must 404 (realpath check), even though git lists it.
3. A tracked file deleted on disk but not yet committed must 404, not 500.
4. A folder in the code root that isn't a git repo, or a repo where `git` fails, is skipped, not a 500.
5. Switching from browse mode back to a session tab restores the live view (timeline, nudge box, Typing/Diff) with nothing left over from browse mode.

---

### Task 1: repos module and endpoints

**Files:**
- Create: `src/mission_control/repos.py`
- Modify: `src/mission_control/server.py` (new `code_root: Path | None = None` param on `create_app`, three routes), `src/mission_control/cli.py` (pass `code_root` from `MC_CODE_ROOT` / `~/code` in `build_config`)
- Test: `tests/test_repos.py`, `tests/test_server.py`

**Interfaces:**
- Produces:
  - `REPO_RE = r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$"` (rejects `.` and `..` by the first char rule)
  - `list_repos(root: Path) -> list[dict]` → `[{"id": name, "path": str, "mtime": float}]`, newest first (mtime = max of repo dir and `.git` dir mtimes).
  - `list_files(repo: Path) -> list[str]` → sorted tracked paths; `[]` if git fails.
  - `read_file(repo: Path, rel: str) -> dict` → `{"path": rel, "text": masked}` or `{"path": rel, "note": "binary file" | "file over 1 MB"}`; raises `FileNotFoundError` for anything not tracked, missing, or resolving outside `repo`.
  - Routes: `GET /api/repos` → `list_repos`; `GET /api/repos/{repo_id}/files` → `{"files": [...]}`; `GET /api/repos/{repo_id}/file?path=<rel>` → `read_file`. Unknown/invalid repo or file → 404.

- [ ] **Step 1: Write failing tests** in `tests/test_repos.py` using a tmp root with a real `git init` repo (commit `a.py`, `src/b.py`, an `.env` listed in `.gitignore`, a binary `img.bin` with NUL bytes, a 1.1 MB `big.txt`, a symlink `out.txt -> /etc/hosts` committed, and a plain non-git folder `notes/`):
  - `test_list_repos_only_git_dirs`: ids == `["proj"]`.
  - `test_list_files_tracked_only`: `.env` not in list, `src/b.py` in list.
  - `test_read_masks`: `a.py` containing `AKIAIOSFODNN7EXAMPLE` comes back without it.
  - `test_read_rejects`: `FileNotFoundError` for `.env`, `../x`, `out.txt` (symlink escape), and a tracked file removed from disk.
  - `test_read_binary_and_big`: notes `"binary file"` and `"file over 1 MB"`.
  - `test_git_failure_returns_empty`: `list_files` on a non-repo dir returns `[]`.
- [ ] **Step 2:** `uv run pytest tests/test_repos.py -q` → fails (module missing).
- [ ] **Step 3:** Implement `repos.py` (subprocess with `timeout=5`, `check=False`).
- [ ] **Step 4:** Add to `tests/test_server.py`: `test_repo_routes` (needs key; `/api/repos` lists the repo; files and file routes work; `/api/repos/..%2F/files`, `/api/repos/nope/files`, and `file?path=.env` → 404) and `test_repo_id_with_dot` (repo named `site.github.io` lists and opens a file).
- [ ] **Step 5:** Wire the routes and `code_root`; `uv run pytest -q` → all pass.
- [ ] **Step 6:** Commit `feat: list and read tracked files of repos in the code root`.

### Task 2: Projects view in the page

**Files:**
- Modify: `static/index.html`, `static/app.js`, `static/app.css`

**Interfaces:**
- Consumes: Task 1 routes.
- Produces: browse mode state `S.browse = {repo, files, open}` (null when live); `openProjects()`, `selectRepo(id)`, `openFile(rel)`.

- [ ] **Step 1:** A `Projects` button as the first item in `.top` before `#tabs` (`aria-haspopup="listbox"`, `aria-expanded`). Clicking it shows a list (popover under the button, keyboard reachable, Escape closes) of repos from `/api/repos`.
- [ ] **Step 2:** `selectRepo(id)`: set `S.browse`, cancel any replay, render the repo's file tree in `#tree` as collapsible folders (`<details>`/`<summary>`, top-level open), relabel the aside "Files in <repo>", hide the timeline, nudge box and Typing/Diff toggle, and set the note line to `Browsing <repo> · read-only`. Files touched by a session whose `cwd` equals the repo path get the existing hot marker.
- [ ] **Step 3:** `openFile(rel)`: fetch the file, render all lines at once (no typing) with line numbers and `highlight()`, or the note text; `#file` shows the path.
- [ ] **Step 4:** Clicking any session tab clears `S.browse` and calls the existing `selectTab`, restoring the live UI (Review Focus 5). The Projects button shows `aria-pressed="true"` while browsing.
- [ ] **Step 5:** Check by hand in the browser pane at 1440 and 390 px: open `mission-control`, open `src/mission_control/server.py`, then click a session tab and confirm the timeline and nudge box come back.
- [ ] **Step 6:** Commit `feat(ui): Projects view to browse any repo read-only`.

### Task 3: Verify and document
- [ ] Run `design:ui-verify` against the running server (0 blocking), note results in the README's feature list (one line on Projects + `MC_CODE_ROOT`), commit `docs: Projects view`.
