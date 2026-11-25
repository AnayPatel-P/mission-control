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


def _a(block):
    import json
    return json.dumps({"type": "assistant", "timestamp": "t", "message": {"content": [block]}})


def test_note_across_lines_and_replaced():
    p = LogParser("s1", "main")
    tool = lambda i: {"type": "tool_use", "id": i, "name": "Bash", "input": {"command": "ls"}}
    assert p.feed(_a({"type": "text", "text": "first"})) == []
    assert p.feed(_a({"type": "thinking", "thinking": "ignore me"})) == []
    assert p.feed(_a(tool("a")))[0]["note"] == "first"
    assert p.feed(_a(tool("b")))[0]["note"] == "first"
    p.feed(_a({"type": "text", "text": "second"}))
    assert p.feed(_a(tool("c")))[0]["note"] == "second"


def test_env_patch_and_pem_masked():
    import json
    pem = "-----BEGIN RSA PRIVATE KEY-----\nMIIBabcdefghijkl\nmnopqrstuvwx\n-----END RSA PRIVATE KEY-----"
    p = LogParser("s1", "main")
    use = {"type": "tool_use", "id": "e1", "name": "Edit",
           "input": {"file_path": "/p/.env", "old_string": pem, "new_string": "DB_HOST=secretvalue123"}}
    ev = p.feed(_a(use))[0]
    assert "MIIBabc" not in ev["old_text"] and "secretvalue123" not in ev["new_text"]
    plines = ["-" + x for x in pem.split("\n")] + ["+DB_HOST=secretvalue123", " KEEP=1"]
    res = {"type": "user", "timestamp": "t", "message": {"content": [{"type": "tool_result", "tool_use_id": "e1"}]},
           "toolUseResult": {"filePath": "/p/.env", "originalFile": None, "structuredPatch": [
               {"oldStart": 1, "oldLines": 5, "newStart": 1, "newLines": 2, "lines": plines}]}}
    r = p.feed(json.dumps(res))[0]
    blob = "\n".join(r["patch"][0]["lines"])
    assert "MIIBabc" not in blob and "secretvalue123" not in blob
    assert (r["added"], r["removed"]) == (1, 4)


def test_malformed_shapes_skipped():
    import json
    p = LogParser("s1", "main")
    assert p.feed(_a({"type": "tool_use", "id": "x", "name": "Edit", "input": "oops"})) == []
    p.feed(_a({"type": "tool_use", "id": "y", "name": "Edit", "input": {"file_path": "/a", "old_string": "a", "new_string": "b"}}))
    res = {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "y"}]},
           "toolUseResult": {"filePath": "/a", "structuredPatch": ["bad", {"lines": ["+z"]}]}}
    assert p.feed(json.dumps(res))[0]["added"] == 1


def test_pending_dropped_after_ttl():
    p = LogParser("s1", "main", pending_ttl=3)
    p.feed(_a({"type": "tool_use", "id": "y", "name": "Edit", "input": {"file_path": "/a", "old_string": "a", "new_string": "b"}}))
    for _ in range(5):
        p.feed("")
    assert p._pending == {}


def test_write_create_uses_content_when_patch_empty():
    import json
    content = "import os\nKEY = 'AKIAIOSFODNN7EXAMPLE'\nprint(KEY)\n"
    p = LogParser("s1", "main")
    use = {"type": "tool_use", "id": "w1", "name": "Write", "input": {"file_path": "/p/new.py", "content": content}}
    p.feed(_a(use))
    res = {"type": "user", "timestamp": "t", "message": {"content": [{"type": "tool_result", "tool_use_id": "w1"}]},
           "toolUseResult": {"type": "create", "filePath": "/p/new.py", "content": content,
                             "structuredPatch": [], "originalFile": None}}
    r = p.feed(json.dumps(res))[0]
    assert r["type"] == "edit_result" and r["original_file"] is None
    assert len(r["patch"]) == 1
    h = r["patch"][0]
    assert (h["oldStart"], h["oldLines"], h["newStart"], h["newLines"]) == (0, 0, 1, 3)
    assert h["lines"][0] == "+import os" and h["lines"][2] == "+print(KEY)"
    assert all(x.startswith("+") for x in h["lines"])
    assert "AKIAIOSFODNN7EXAMPLE" not in "\n".join(h["lines"])
    assert (r["added"], r["removed"]) == (3, 0)
