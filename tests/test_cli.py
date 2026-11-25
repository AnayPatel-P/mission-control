import json
from pathlib import Path

from mission_control import cli

PY = "/venv/bin/python"
REPO = Path("/repo")


def ours(entry):
    return any("hook.py" in h["command"] for h in entry["hooks"])


def setup(tmp_path, data):
    p = tmp_path / "settings.json"
    p.write_text(json.dumps(data, indent=2))
    return p


def install(p, confirm=lambda d: True):
    return cli.install_hook(p, confirm, python=PY, repo=REPO)


EXISTING = {"hooks": {"PostToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "echo hi"}]}]}}


def test_install_adds_both_hooks(tmp_path):
    p = setup(tmp_path, EXISTING)
    assert install(p) is True
    hooks = json.loads(p.read_text())["hooks"]
    assert hooks["PostToolUse"][0] == EXISTING["hooks"]["PostToolUse"][0]
    new = hooks["PostToolUse"][1]
    assert new["matcher"] == "*"
    assert new["hooks"][0]["command"] == f"{PY} {REPO}/src/mission_control/hook.py post"
    assert hooks["Stop"][0]["hooks"][0]["command"] == f"{PY} {REPO}/src/mission_control/hook.py stop"
    assert (tmp_path / "settings.json.mc-bak").exists()


def test_install_declined_writes_nothing(tmp_path):
    p = setup(tmp_path, EXISTING)
    before = p.read_bytes()
    assert install(p, lambda d: False) is False
    assert p.read_bytes() == before


def test_install_shows_diff(tmp_path):
    p = setup(tmp_path, EXISTING)
    seen = []
    install(p, lambda d: seen.append(d) or True)
    assert "hook.py post" in seen[0] and "hook.py stop" in seen[0]


def test_uninstall_removes_only_ours(tmp_path):
    p = setup(tmp_path, EXISTING)
    install(p)
    assert cli.uninstall_hook(p) is True
    assert json.loads(p.read_text()) == EXISTING


def test_install_idempotent(tmp_path):
    p = setup(tmp_path, EXISTING)
    install(p)
    install(p)
    hooks = json.loads(p.read_text())["hooks"]
    assert sum(ours(e) for e in hooks["PostToolUse"]) == 1
    assert sum(ours(e) for e in hooks["Stop"]) == 1


def test_install_creates_missing_settings(tmp_path):
    p = tmp_path / "settings.json"
    assert install(p) is True
    assert "Stop" in json.loads(p.read_text())["hooks"]


def test_mode_preserved(tmp_path):
    p = setup(tmp_path, EXISTING)
    p.chmod(0o600)
    install(p)
    assert p.stat().st_mode & 0o777 == 0o600
    cli.uninstall_hook(p)
    assert p.stat().st_mode & 0o777 == 0o600


def test_symlink_survives(tmp_path):
    real = tmp_path / "real.json"
    real.write_text(json.dumps(EXISTING))
    link = tmp_path / "settings.json"
    link.symlink_to(real)
    install(link)
    assert link.is_symlink()
    assert "Stop" in json.loads(real.read_text())["hooks"]
    cli.uninstall_hook(link)
    assert link.is_symlink() and json.loads(real.read_text()) == EXISTING


def test_paths_with_spaces(tmp_path):
    import shlex

    p = setup(tmp_path, EXISTING)
    py, repo = "/my venv/bin/python", Path("/Users/me/My Code/mission-control")
    assert cli.install_hook(p, lambda d: True, python=py, repo=repo) is True
    cmd = json.loads(p.read_text())["hooks"]["Stop"][0]["hooks"][0]["command"]
    assert shlex.split(cmd) == [py, f"{repo}/src/mission_control/hook.py", "stop"]
    assert cli.uninstall_hook(p) is True
    assert json.loads(p.read_text()) == EXISTING


def test_uninstall_matches_old_unquoted_entries(tmp_path):
    old = {"type": "command", "command": "/venv/bin/python /repo/src/mission_control/hook.py post"}
    data = {"hooks": {**EXISTING["hooks"], "Stop": [{"hooks": [old]}]}}
    p = setup(tmp_path, data)
    assert cli.uninstall_hook(p) is True
    assert json.loads(p.read_text()) == EXISTING
