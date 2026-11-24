import os
import shutil
import time
from pathlib import Path

from mission_control.watch import Watcher

FIX = Path(__file__).parent / "fixtures"


def setup(tmp_path):
    proj = tmp_path / "proj"
    (proj / "s1" / "subagents").mkdir(parents=True)
    main = proj / "s1.jsonl"
    shutil.copy(FIX / "main.jsonl", main)
    shutil.copy(FIX / "subagent.jsonl", proj / "s1" / "subagents" / "agent-a1.jsonl")
    got = []
    return Watcher(tmp_path, got.append), got, main


APPEND = (
    '{"type":"assistant","timestamp":"2026-11-24T11:00:00Z","message":{"content":'
    '[{"type":"tool_use","id":"n1","name":"Bash","input":{"command":"ls"}}]}}'
)


def test_discovers_main_and_subagent(tmp_path):
    w, got, _ = setup(tmp_path)
    w.scan()
    ss = w.sessions()
    assert len(ss) == 1
    s = ss[0]
    assert s["id"] == "s1" and s["agents"] == ["main", "a1"]
    assert s["project"] == "proj" and s["cwd"] == "/tmp/p" and s["branch"] == "main"
    assert s["title"] == "Build the parser please, it should be quick"[:60]
    assert s["active"] is True
    assert got


def test_follows_appends(tmp_path):
    w, got, main = setup(tmp_path)
    w.scan()
    n = len(got)
    with main.open("a") as f:
        f.write(APPEND + "\n")
    w.scan()
    assert len(got) == n + 1 and got[-1]["command"] == "ls"
    w.scan()
    assert len(got) == n + 1


def test_partial_line_buffered(tmp_path):
    w, got, main = setup(tmp_path)
    w.scan()
    n = len(got)
    half = len(APPEND) // 2
    with main.open("a") as f:
        f.write(APPEND[:half])
    w.scan()
    assert len(got) == n
    with main.open("a") as f:
        f.write(APPEND[half:] + "\n")
    w.scan()
    assert len(got) == n + 1


def test_truncate_and_delete(tmp_path):
    w, got, main = setup(tmp_path)
    w.scan()
    main.write_text("")
    w.scan()
    n = len(got)
    with main.open("a") as f:
        f.write(APPEND + "\n")
    w.scan()
    assert len(got) == n + 1
    shutil.rmtree(tmp_path / "proj" / "s1")
    main.unlink()
    w.scan()
    assert w.sessions() == []
    assert w.history("s1") == []


def test_active_flag(tmp_path):
    w, _, main = setup(tmp_path)
    old = time.time() - 300
    for p in (tmp_path / "proj").rglob("*.jsonl"):
        os.utime(p, (old, old))
    w.scan()
    assert w.sessions()[0]["active"] is False


def test_history_window(tmp_path):
    w, got, main = setup(tmp_path)
    old = time.time() - 5 * 86400
    os.utime(main, (old, old))
    for p in (tmp_path / "proj" / "s1").rglob("*.jsonl"):
        os.utime(p, (old, old))
    w.scan()
    assert w.sessions() == []


def test_history(tmp_path):
    w, _, _ = setup(tmp_path)
    w.scan()
    h = w.history("s1")
    assert [e["ts"] for e in h] == sorted(e["ts"] for e in h)
    assert any(e["agent_id"] == "a1" for e in h)
    assert h[0]["type"] == "edit" and h[0]["agent_id"] == "main"
