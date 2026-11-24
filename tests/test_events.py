from pathlib import Path

from mission_control.events import LogParser

FIX = Path(__file__).parent / "fixtures"


def parse(name="main.jsonl", agent="main"):
    p = LogParser("s1", agent)
    out = []
    for line in (FIX / name).read_text().splitlines():
        out.extend(p.feed(line))
    return out


def test_edit_event_with_note():
    ev = parse()
    e, r = ev[0], ev[1]
    assert e["type"] == "edit" and e["kind"] == "Edit"
    assert e["note"] == "Adding the parser"
    assert (e["old_text"], e["new_text"]) == ("x = 1", "x = 2")
    assert e["session_id"] == "s1" and e["agent_id"] == "main"
    assert e["tool_use_id"] == "tu1" and e["ts"] == "2026-11-24T10:00:01Z"
    assert r["type"] == "edit_result" and r["tool_use_id"] == "tu1"
    assert r["file_path"] == "/tmp/p/a.py"
    assert (r["added"], r["removed"]) == (1, 1)
    assert r["original_file"] is None
    assert r["patch"][0]["lines"][1] == "-x = 1"


def test_write_and_bash():
    ev = parse()
    w = next(e for e in ev if e["type"] == "edit" and e["kind"] == "Write")
    assert w["new_text"] == "y = 1\n" and w["old_text"] == ""
    b = next(e for e in ev if e["type"] == "bash")
    assert b["command"] == "uv run pytest -q" and b["note"] == "Running tests"
    assert not any(e["type"] == "edit_result" and e["tool_use_id"] == "tu3" for e in ev)


def test_error():
    err = [e for e in parse() if e["type"] == "error"]
    assert len(err) == 1
    assert err[0]["tool_use_id"] == "tu4"
    assert err[0]["message"].startswith("String to replace")


def test_note_masked():
    ev = parse()
    w = next(e for e in ev if e["type"] == "edit" and e["kind"] == "Write")
    assert "AKIAIOSFODNN7EXAMPLE" not in w["note"]


def test_multiedit_joins():
    p = LogParser("s1", "main")
    line = (
        '{"type":"assistant","timestamp":"t","message":{"content":[{"type":"tool_use","id":"m1",'
        '"name":"MultiEdit","input":{"file_path":"/x.py","edits":[{"old_string":"a","new_string":"b"},'
        '{"old_string":"c","new_string":"d"}]}}]}}'
    )
    e = p.feed(line)[0]
    assert e["kind"] == "MultiEdit" and e["old_text"] == "a\nc" and e["new_text"] == "b\nd"
    assert e["note"] == ""


def test_subagent_fixture():
    ev = parse("subagent.jsonl", "a1")
    assert [e["type"] for e in ev] == ["edit", "edit_result"]
    assert ev[1]["original_file"] == "a\n" and all(e["agent_id"] == "a1" for e in ev)


def test_unknown_lines_ignored():
    p = LogParser("s1", "main")
    for line in ['{"type":"summary","summary":"x"}', '{"type":"attachment"}', "garbage{", "", "[1]", "null"]:
        assert p.feed(line) == []
