import argparse
import copy
import difflib
import json
import os
import shlex
import shutil
import stat
import sys
from pathlib import Path
from typing import Callable

REPO = Path(__file__).resolve().parents[2]
SETTINGS = Path.home() / ".claude" / "settings.json"
MARK = "mission_control/hook.py"


def hook_entries(python: str, repo: Path) -> dict:
    script = f"{repo}/src/mission_control/hook.py"

    def entry(kind: str, **extra) -> dict:
        cmd = f"{shlex.quote(python)} {shlex.quote(script)} {kind}"
        return {**extra, "hooks": [{"type": "command", "command": cmd}]}

    return {"PostToolUse": [entry("post", matcher="*")], "Stop": [entry("stop")]}


def _is_ours(entry: dict) -> bool:
    return any(MARK in h.get("command", "") for h in entry.get("hooks", []))


def _load(settings: Path) -> dict:
    return json.loads(settings.read_text()) if settings.exists() else {}


def _dump(data: dict) -> str:
    return json.dumps(data, indent=2) + "\n"


def _atomic_write(path: Path, text: str) -> None:
    path = path.resolve()  # keep a symlinked settings.json a symlink
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o600
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text)
    os.chmod(tmp, mode)
    os.replace(tmp, path)


def install_hook(
    settings: Path,
    confirm: Callable[[str], bool],
    python: str = sys.executable,
    repo: Path = REPO,
) -> bool:
    old = _load(settings)
    new = copy.deepcopy(old)
    for event, entries in hook_entries(python, repo).items():
        lst = new.setdefault("hooks", {}).setdefault(event, [])
        lst[:] = [e for e in lst if not _is_ours(e)] + entries
    diff = "".join(
        difflib.unified_diff(_dump(old).splitlines(True), _dump(new).splitlines(True), "settings.json", "settings.json (new)")
    )
    if not diff or not confirm(diff):
        return False
    if settings.exists():
        shutil.copy2(settings, settings.with_name(settings.name + ".mc-bak"))
    _atomic_write(settings, _dump(new))
    return True


def uninstall_hook(settings: Path) -> bool:
    if not settings.exists():
        return False
    data = _load(settings)
    hooks = data.get("hooks", {})
    changed = False
    for event in list(hooks):
        kept = [e for e in hooks[event] if not _is_ours(e)]
        if len(kept) != len(hooks[event]):
            changed = True
            if kept:
                hooks[event] = kept
            else:
                del hooks[event]
    if not changed:
        return False
    if not hooks:
        del data["hooks"]
    shutil.copy2(settings, settings.with_name(settings.name + ".mc-bak"))
    _atomic_write(settings, _dump(data))
    return True


def _ask(diff: str) -> bool:
    print(diff)
    return input("Write these changes to settings.json? [y/N] ").strip().lower() == "y"


def build_config(home: Path, projects: Path, port: int, narrator_client=None):
    import uvicorn

    from .server import create_app

    return uvicorn.Config(
        create_app(
            home, projects, port, narrator_client=narrator_client,
            code_root=Path(os.environ.get("MC_CODE_ROOT", Path.home() / "code")),
        ),
        host="127.0.0.1", port=port, log_level="warning",
        timeout_graceful_shutdown=1,  # the live feed never ends on its own, so don't wait on it
    )


def serve() -> None:
    import uvicorn

    from .server import load_key

    home = Path(os.environ.get("MC_HOME", Path.home() / ".claude" / "mission-control"))
    projects = Path(os.environ.get("MC_PROJECTS", Path.home() / ".claude" / "projects"))
    port = int(os.environ.get("MC_PORT", "7788"))
    print(f"Mission Control: http://127.0.0.1:{port}/?key={load_key(home)}")
    uvicorn.Server(build_config(home, projects, port)).run()


def main() -> int:
    ap = argparse.ArgumentParser(prog="mission-control")
    ap.add_argument("command", nargs="?", default="serve", choices=["serve", "install-hook", "uninstall-hook"])
    cmd = ap.parse_args().command
    if cmd == "install-hook":
        print("Installed." if install_hook(SETTINGS, _ask) else "No changes made.")
    elif cmd == "uninstall-hook":
        print("Removed." if uninstall_hook(SETTINGS) else "Nothing to remove.")
    else:
        serve()
    return 0


if __name__ == "__main__":
    sys.exit(main())
