import json
import statistics
import subprocess
import sys
import time
from io import StringIO
from pathlib import Path

from mission_control import hook, inbox

HOOK = Path(hook.__file__)
PREFIX = "Message from Anay via Mission Control: "


def run(kind, payload, home):
    out = StringIO()
    rc = hook.main(kind, StringIO(payload), out, home)
    assert rc == 0
    return out.getvalue()


def test_post_injects(tmp_path):
    inbox.enqueue(tmp_path, "s1", "main", "use sqlite")
    out = run("post", '{"session_id":"s1","hook_event_name":"PostToolUse"}', tmp_path)
    assert json.loads(out) == {
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": PREFIX + "use sqlite",
        }
    }


def test_post_subagent_uses_agent_id(tmp_path):
    inbox.enqueue(tmp_path, "s1", "a1", "for sub")
    assert run("post", '{"session_id":"s1"}', tmp_path) == ""
    out = run("post", '{"session_id":"s1","agent_id":"a1"}', tmp_path)
    assert "for sub" in json.loads(out)["hookSpecificOutput"]["additionalContext"]


def test_stop_blocks_only_with_mail(tmp_path):
    assert run("stop", '{"session_id":"s1"}', tmp_path) == ""
    inbox.enqueue(tmp_path, "s1", "main", "wait")
    out = run("stop", '{"session_id":"s1"}', tmp_path)
    assert json.loads(out) == {"decision": "block", "reason": PREFIX + "wait"}


def test_fail_open(tmp_path):
    assert run("post", "not json", tmp_path) == ""
    assert run("post", '{"session_id":"s1"}', tmp_path / "missing") == ""
    inbox.enqueue(tmp_path, "s1", "main", "x")
    d = tmp_path / "inbox" / "s1"
    d.chmod(0o500)
    try:
        assert run("post", '{"session_id":"s1"}', tmp_path) == ""
    finally:
        d.chmod(0o700)


def test_speed(tmp_path):
    times = []
    for _ in range(20):
        t = time.perf_counter()
        subprocess.run(
            [sys.executable, str(HOOK), "post"],
            input='{"session_id":"s1"}',
            text=True,
            capture_output=True,
            env={"MC_HOME": str(tmp_path), "PATH": "/usr/bin"},
        )
        times.append(time.perf_counter() - t)
    assert statistics.median(times) < 0.06


def test_fail_open_when_inbox_import_breaks(tmp_path):
    lone = tmp_path / "hook.py"
    lone.write_text(HOOK.read_text())
    r = subprocess.run(
        [sys.executable, str(lone), "post"],
        input='{"session_id":"s1"}',
        text=True,
        capture_output=True,
        env={"MC_HOME": str(tmp_path), "PATH": "/usr/bin"},
    )
    assert r.returncode == 0 and r.stdout == ""


def test_stop_marker(tmp_path):
    marker = tmp_path / "stopped" / "s1"
    run("stop", '{"session_id":"s1","agent_id":"a1"}', tmp_path)
    assert not marker.exists()  # subagents don't end the turn
    run("stop", '{"session_id":"s1"}', tmp_path)
    assert marker.exists()
    assert marker.stat().st_mode & 0o777 == 0o600
    assert (tmp_path / "stopped").stat().st_mode & 0o777 == 0o700
    run("post", '{"session_id":"s1","agent_id":"a1"}', tmp_path)
    assert marker.exists()
    run("post", '{"session_id":"s1"}', tmp_path)
    assert not marker.exists()
    run("post", '{"session_id":"s1"}', tmp_path)  # already gone: still fine


def test_stop_with_mail_leaves_no_marker(tmp_path):
    inbox.enqueue(tmp_path, "s1", "main", "wait")
    run("stop", '{"session_id":"s1"}', tmp_path)
    assert not (tmp_path / "stopped" / "s1").exists()
