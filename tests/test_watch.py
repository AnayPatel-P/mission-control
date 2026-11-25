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


def test_bad_lines_and_raising_callback(tmp_path):
    got, calls = [], []

    def cb(ev):
        calls.append(ev)
        if len(calls) == 1:
            raise RuntimeError("boom")
        got.append(ev)

    proj = tmp_path / "proj"
    proj.mkdir()
    f = proj / "s1.jsonl"
    bad = '{"type":"assistant","message":{"content":[{"type":"tool_use","id":"b","name":"Edit","input":"str"}]}}'
    bad2 = '{"type":"user","message":{"content":[{"type":"tool_result","tool_use_id":"b"}]},"toolUseResult":{"structuredPatch":["x"]}}'
    f.write_text(bad + "\n" + bad2 + "\n" + APPEND + "\n" + APPEND + "\n")
    w = Watcher(tmp_path, cb)
    w.scan()
    assert len(calls) == 2 and len(got) == 1


def test_feed_exception_skips_line(tmp_path, monkeypatch):
    from mission_control import events
    got = []
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "s1.jsonl").write_text(APPEND + "\n" + APPEND + "\n")
    orig = events.LogParser.feed
    n = []

    def feed(self, line):
        n.append(1)
        if len(n) == 1:
            raise ValueError
        return orig(self, line)

    monkeypatch.setattr(events.LogParser, "feed", feed)
    Watcher(tmp_path, got.append).scan()
    assert len(got) == 1


def test_title_masked(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "s1.jsonl").write_text('{"type":"user","message":{"content":"use key AKIAIOSFODNN7EXAMPLE ok"}}\n')
    w = Watcher(tmp_path, lambda e: None)
    w.scan()
    assert "AKIAIOSFODNN7EXAMPLE" not in w.sessions()[0]["title"]


def _result(i, orig="orig\n"):
    import json
    use = {"type": "assistant", "timestamp": f"2026-11-24T10:00:{i:02d}Z", "message": {"content": [
        {"type": "tool_use", "id": f"t{i}", "name": "Edit", "input": {"file_path": "/a", "old_string": "a", "new_string": "b"}}]}}
    res = {"type": "user", "timestamp": f"2026-11-24T10:00:{i:02d}Z", "message": {"content": [{"type": "tool_result", "tool_use_id": f"t{i}"}]},
           "toolUseResult": {"filePath": "/a", "originalFile": orig, "structuredPatch": [{"lines": ["-a", "+b"]}]}}
    return json.dumps(use) + "\n" + json.dumps(res) + "\n"


def test_caps(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "s1.jsonl").write_text("".join(_result(i) for i in range(10)))
    got = []
    w = Watcher(tmp_path, got.append, max_events=8, max_originals=3)
    w.scan()
    h = w.history("s1")
    assert len(h) == 8 and len(got) == 20
    results = [e for e in h if e["type"] == "edit_result"]
    with_orig = [e for e in results if e["original_file"] is not None]
    assert [e["tool_use_id"] for e in with_orig] == ["t7", "t8", "t9"]
    assert all(e["original_file"] == "orig\n" for e in got if e["type"] == "edit_result")


def test_files_past_window_are_dropped(tmp_path):
    w, _, main = setup(tmp_path)
    w.scan()
    assert [s["id"] for s in w.sessions()] == ["s1"]
    old = time.time() - 3 * 86400
    os.utime(main, (old, old))
    w.scan()
    assert [s["agents"] for s in w.sessions()] == [["a1"]]  # subagent file still recent
    os.utime(main.parent / "s1" / "subagents" / "agent-a1.jsonl", (old, old))
    w.scan()
    assert w.sessions() == [] and w.history("s1") == []


def test_edit_text_capped(tmp_path):
    import json

    proj = tmp_path / "proj"
    proj.mkdir()
    big = "x" * 50
    use = {"type": "assistant", "timestamp": "t", "message": {"content": [
        {"type": "tool_use", "id": "t1", "name": "Edit", "input": {"file_path": "/a", "old_string": big, "new_string": "short"}}]}}
    (proj / "s1.jsonl").write_text(json.dumps(use) + "\n")
    got = []
    w = Watcher(tmp_path, got.append, max_edit_text=10)
    w.scan()
    ev = w.history("s1")[0]
    assert ev["old_text"] == "x" * 10 + "\n[... 40 more characters not shown ...]"
    assert ev["new_text"] == "short" and got[0]["old_text"] == ev["old_text"]


def test_bad_meta_line_keeps_batch(tmp_path):
    import json

    proj = tmp_path / "proj"
    proj.mkdir()
    bad = json.dumps({"type": "user", "message": {"content": [{"type": "text", "text": None}]}})
    (proj / "s1.jsonl").write_text(bad + "\n" + APPEND + "\n")
    got = []
    w = Watcher(tmp_path, got.append)
    w.scan()
    assert [e["type"] for e in got] == ["bash"]
