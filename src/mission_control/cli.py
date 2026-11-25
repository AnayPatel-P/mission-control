import argparse
import copy
import difflib
import json
import os
import plistlib
import shlex
import shutil
import stat
import subprocess
import time
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


AGENT_LABEL = "com.mission-control.server"
AGENT_PLIST = Path.home() / "Library" / "LaunchAgents" / f"{AGENT_LABEL}.plist"
AGENT_LOGS = Path.home() / "Library" / "Logs" / "mission-control"


def agent_plist(repo: Path, logs: Path) -> bytes:
    return plistlib.dumps({
        "Label": AGENT_LABEL,
        "ProgramArguments": [f"{repo}/.venv/bin/mission-control", "serve"],
        "WorkingDirectory": str(repo),
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": 60,  # if it can't start (port taken), retry once a minute, not every 10 s
        "StandardOutPath": f"{logs}/server.log",
        "StandardErrorPath": f"{logs}/server.log",
        "EnvironmentVariables": {"PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"},
    })


def _launchctl(args: list[str]) -> tuple[int, str]:
    r = subprocess.run(["launchctl", *args], capture_output=True, text=True, check=False)
    return r.returncode, (r.stderr or r.stdout).strip()


def install_agent(
    plist: Path,
    confirm: Callable[[str], bool],
    launchctl: Callable[[list[str]], tuple[int, str]] = _launchctl,
    repo: Path = REPO,
    logs: Path = AGENT_LOGS,
    retry_wait: float = 1.0,
) -> bool:
    exe = Path(repo) / ".venv" / "bin" / "mission-control"
    if not exe.exists():
        raise RuntimeError(f"{exe} not found. Run `uv sync` in {repo} first.")
    data = agent_plist(repo, logs)
    if not confirm(f"{plist}:\n{data.decode()}"):
        return False
    logs.mkdir(parents=True, exist_ok=True)
    plist.parent.mkdir(parents=True, exist_ok=True)
    domain = f"gui/{os.getuid()}"
    launchctl(["bootout", f"{domain}/{AGENT_LABEL}"])  # reload if it was already running
    plist.write_bytes(data)
    code, err = launchctl(["bootstrap", domain, str(plist)])
    if code:  # often "Input/output error" while the old job is still exiting
        time.sleep(retry_wait)
        code, err = launchctl(["bootstrap", domain, str(plist)])
    if code:
        raise RuntimeError(f"launchctl bootstrap failed: {err}")
    return True


def uninstall_agent(plist: Path, launchctl: Callable[[list[str]], tuple[int, str]] = _launchctl) -> bool:
    code, _ = launchctl(["bootout", f"gui/{os.getuid()}/{AGENT_LABEL}"])
    if not plist.exists():
        return code == 0
    plist.unlink()
    return True


def _ask_agent(text: str) -> bool:
    print(text)
    return input("Install this LaunchAgent (starts Mission Control at login)? [y/N] ").strip().lower() == "y"


def _ask(diff: str) -> bool:
    print(diff)
    return input("Write these changes to settings.json? [y/N] ").strip().lower() == "y"


def _page_opener():
    if sys.platform != "darwin" or os.environ.get("MC_AUTO_OPEN", "on").lower() == "off":
        return None
    from .opener import open_in_background

    return open_in_background


def build_config(home: Path, projects: Path, port: int, narrator_client=None):
    import uvicorn

    from .server import create_app

    return uvicorn.Config(
        create_app(
            home, projects, port, narrator_client=narrator_client,
            code_root=Path(os.environ.get("MC_CODE_ROOT", Path.home() / "code")),
            open_page=_page_opener(),
        ),
        host="127.0.0.1", port=port, log_level="warning",
        timeout_graceful_shutdown=1,  # the live feed never ends on its own, so don't wait on it
    )


def startup_line(port: int, key: str, home: Path, tty: bool) -> str:
    if tty:
        return f"Mission Control: http://127.0.0.1:{port}/?key={key}"
    return f"Mission Control on http://127.0.0.1:{port}/ (access key in {home}/key, kept out of logs)"


def serve() -> None:
    import uvicorn

    from .server import load_key

    home = Path(os.environ.get("MC_HOME", Path.home() / ".claude" / "mission-control"))
    projects = Path(os.environ.get("MC_PROJECTS", Path.home() / ".claude" / "projects"))
    port = int(os.environ.get("MC_PORT", "7788"))
    print(startup_line(port, load_key(home), home, sys.stdout.isatty()), flush=True)
    uvicorn.Server(build_config(home, projects, port)).run()


def main() -> int:
    ap = argparse.ArgumentParser(prog="mission-control")
    ap.add_argument("command", nargs="?", default="serve", choices=["serve", "install-hook", "uninstall-hook", "install-agent", "uninstall-agent"])
    cmd = ap.parse_args().command
    if cmd == "install-hook":
        print("Installed." if install_hook(SETTINGS, _ask) else "No changes made.")
    elif cmd == "uninstall-hook":
        print("Removed." if uninstall_hook(SETTINGS) else "Nothing to remove.")
    elif cmd == "install-agent":
        try:
            ok = install_agent(AGENT_PLIST, _ask_agent)
        except RuntimeError as e:
            print(e)
            return 1
        print("Installed. It starts now and at every login." if ok else "No changes made.")
    elif cmd == "uninstall-agent":
        print("Removed." if uninstall_agent(AGENT_PLIST) else "Nothing to remove.")
    else:
        serve()
    return 0


if __name__ == "__main__":
    sys.exit(main())
