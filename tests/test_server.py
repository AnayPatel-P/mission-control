import asyncio
import contextlib
import socket
import json
import os
import shutil
import threading
import time
from pathlib import Path

import httpx
import uvicorn

from mission_control import cli, inbox
from mission_control.server import create_app

FIX = Path(__file__).parent / "fixtures"
PORT = 7788
EDIT = (
    '{"type":"assistant","sessionId":"s1","timestamp":"2026-11-24T11:00:00Z","message":{"id":"m9","content":'
    '[{"type":"tool_use","id":"tu9","name":"Edit","input":{"file_path":"/tmp/p/a.py",'
    '"old_string":"x = 2","new_string":"x = 3"}}]}}\n'
)


def offline_client():
    def boom(request):
        raise httpx.ConnectError("offline")

    return httpx.AsyncClient(transport=httpx.MockTransport(boom))


def free_port():
    with socket.socket() as sk:
        sk.bind(("127.0.0.1", 0))
        return sk.getsockname()[1]


def make(tmp_path, port=PORT):
    proj = tmp_path / "projects" / "proj"
    proj.mkdir(parents=True)
    log = proj / "s1.jsonl"
    shutil.copy(FIX / "main.jsonl", log)
    app = create_app(tmp_path / "home", tmp_path / "projects", port, narrator_client=offline_client())
    key = (tmp_path / "home" / "key").read_text().strip()
    return app, key, log


@contextlib.contextmanager
def served(tmp_path):
    port = free_port()
    app, key, log = make(tmp_path, port)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    with httpx.Client(base_url=f"http://127.0.0.1:{port}", follow_redirects=True) as c:
        yield c, key, log, {"Origin": f"http://127.0.0.1:{port}"}
    server.should_exit = True
    t.join(5)


def test_requires_key(tmp_path):
    with served(tmp_path) as (c, key, _, ORIGIN):
        assert c.get("/api/sessions").status_code == 403
        assert c.get("/api/sessions?key=wrong").status_code == 403
        r = c.get(f"/api/sessions?key={key}", follow_redirects=False)
        assert r.status_code == 303
        assert "mc_key" in c.cookies
        assert "httponly" in r.headers["set-cookie"].lower()
        assert "samesite=strict" in r.headers["set-cookie"].lower()
        assert c.get("/api/sessions").status_code == 200  # cookie only


def test_key_persists(tmp_path):
    _, k1, _ = make(tmp_path)
    create_app(tmp_path / "home", tmp_path / "projects", PORT, narrator_client=offline_client())
    assert (tmp_path / "home" / "key").read_text().strip() == k1
    assert (tmp_path / "home" / "key").stat().st_mode & 0o777 == 0o600


def test_post_origin(tmp_path):
    with served(tmp_path) as (c, key, _, ORIGIN):
        body = {"session_id": "s1", "text": "hi"}
        bad = c.post(f"/api/nudge?key={key}", json=body, headers={"Origin": "http://evil.test"})
        assert bad.status_code == 403
        assert c.post(f"/api/nudge?key={key}", json=body, headers=ORIGIN).status_code == 200


def test_nudge_idle_vs_active(tmp_path):
    with served(tmp_path) as (c, key, log, ORIGIN):
        r = c.post(f"/api/nudge?key={key}", json={"session_id": "s1", "text": "hello"}, headers=ORIGIN).json()
        assert r["status"] == "queued"
        assert (tmp_path / "home" / "inbox" / "s1" / f"{r['id']}.json").exists()
        st = c.get(f"/api/nudge/{r['id']}?session_id=s1&key={key}").json()
        assert st == {"status": "queued"}
        old = time.time() - 600
        os.utime(log, (old, old))
        time.sleep(0.6)  # let the watcher see the new mtime
        r2 = c.post(f"/api/nudge?key={key}", json={"session_id": "s1", "text": "again"}, headers=ORIGIN).json()
        assert r2 == {"status": "idle"}
        assert len(list((tmp_path / "home" / "inbox" / "s1").glob("*.json"))) == 1


def test_nudge_idle_after_turn_ends(tmp_path):
    from io import StringIO

    from mission_control import hook

    home = tmp_path / "home"
    with served(tmp_path) as (c, key, log, ORIGIN):
        hook.main("stop", StringIO('{"session_id":"s1"}'), StringIO(), home)
        log.touch()
        time.sleep(0.6)  # the log is fresh, so only the marker says the turn is over
        r = c.post(f"/api/nudge?key={key}", json={"session_id": "s1", "text": "late"}, headers=ORIGIN).json()
        assert r == {"status": "idle"}
        assert not (home / "inbox" / "s1").exists()
        hook.main("post", StringIO('{"session_id":"s1"}'), StringIO(), home)
        r = c.post(f"/api/nudge?key={key}", json={"session_id": "s1", "text": "now"}, headers=ORIGIN).json()
        assert r["status"] == "queued"


def test_nudge_too_long_and_bad_ids(tmp_path):
    with served(tmp_path) as (c, key, _, ORIGIN):
        def post(**kw):
            return c.post(f"/api/nudge?key={key}", json=kw, headers=ORIGIN).status_code

        assert post(session_id="s1", text="x" * 2001) == 422
        assert post(session_id="s1", text="") == 422
        assert post(session_id="../x", text="hi") == 422
        assert post(session_id="s1", agent_id="a/b", text="hi") == 422
        assert c.get(f"/api/nudge/..%2Fx?session_id=s1&key={key}").status_code == 404
        assert c.get(f"/api/nudge/abc?session_id=a.b&key={key}").status_code == 404


def test_config_sessions_history_index(tmp_path):
    with served(tmp_path) as (c, key, _, ORIGIN):
        assert c.get(f"/api/config?key={key}").json() == {"subagent_nudges": True}
        ss = c.get(f"/api/sessions?key={key}").json()
        assert [s["id"] for s in ss] == ["s1"]
        h = c.get(f"/api/sessions/s1/history?key={key}").json()
        assert any(e["type"] == "edit" for e in h["events"]) and h["bubbles"] == []
        assert c.get(f"/?key={key}").status_code == 200


def test_bubbles_in_history(tmp_path):
    with served(tmp_path) as (c, key, log, ORIGIN):
        with log.open("a") as f:
            f.write(EDIT)
            f.write(
                '{"type":"user","sessionId":"s1","timestamp":"2026-11-24T11:00:01Z","message":{"content":'
                '[{"type":"tool_result","tool_use_id":"tu9","content":"ok"}]},"toolUseResult":{"filePath":"/tmp/p/a.py",'
                '"oldString":"x = 2","newString":"x = 3","originalFile":null,"structuredPatch":[{"oldStart":1,'
                '"oldLines":1,"newStart":1,"newLines":1,"lines":["-x = 2","+x = 3"]}]}}\n'
            )
        for _ in range(50):
            time.sleep(0.1)
            h = c.get(f"/api/sessions/s1/history?key={key}").json()
            if h["bubbles"]:
                break
        assert h["bubbles"] and h["bubbles"][0]["type"] == "bubble"


def test_binds_localhost(tmp_path):
    cfg = cli.build_config(tmp_path / "home", tmp_path / "projects", 7799, narrator_client=offline_client())
    assert cfg.host == "127.0.0.1" and cfg.port == 7799


def test_stream_delivers_event(tmp_path):
    with served(tmp_path) as (c0, key, log, _):
        base = str(c0.base_url).rstrip("/")

        async def go():
            async with httpx.AsyncClient(timeout=3, cookies={"mc_key": key}) as c:
                async with c.stream("GET", f"{base}/api/stream") as r:
                    assert r.status_code == 200
                    with log.open("a") as f:
                        f.write(EDIT)
                    async for line in r.aiter_lines():
                        if line.startswith("data:"):
                            return json.loads(line[5:])

        ev = asyncio.run(asyncio.wait_for(go(), 3))
        assert ev["type"] == "edit" and ev["tool_use_id"] == "tu9"


def test_empty_key_file_regenerated(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    (home / "key").write_text("")
    with served(tmp_path) as (c, key, _, _):
        assert len(key) >= 32
        assert c.get("/api/sessions").status_code == 403
        assert c.get("/api/sessions?key=").status_code == 403
        c.cookies.set("mc_key", "")
        assert c.get("/api/sessions").status_code == 403


def test_origin_variants_rejected(tmp_path):
    with served(tmp_path) as (c, key, _, good):
        port = good["Origin"].rsplit(":", 1)[1]
        body = {"session_id": "s1", "text": "hi"}
        for origin in [None, "null", f"http://127.0.0.1:{int(port) + 1}", f"http://LOCALHOST:{port}", "http://127.0.0.1"]:
            h = {} if origin is None else {"Origin": origin}
            assert c.post(f"/api/nudge?key={key}", json=body, headers=h).status_code == 403, origin


def test_wrong_cookie_and_ungated_routes(tmp_path):
    with served(tmp_path) as (c, key, _, _):
        for path in ["/api/stream", "/static/index.html", "/", "/api/config"]:
            assert httpx.get(f"{c.base_url}{path}").status_code == 403, path
        assert httpx.get(f"{c.base_url}/api/sessions", cookies={"mc_key": "nope"}).status_code == 403


def test_key_redirect_strips_key(tmp_path):
    with served(tmp_path) as (c, key, _, _):
        r = httpx.get(f"{c.base_url}/api/nudge/abc?key={key}&session_id=s1")
        assert r.status_code == 303
        assert r.headers["location"] == "/api/nudge/abc?session_id=s1"
        assert "mc_key" in r.cookies


def test_history_bad_id_is_422(tmp_path):
    with served(tmp_path) as (c, key, _, _):
        assert c.get(f"/api/sessions/a.b/history?key={key}").status_code == 422


def test_shutdown_not_blocked_by_open_stream(tmp_path):
    port = free_port()
    (tmp_path / "projects").mkdir(exist_ok=True)
    server = uvicorn.Server(cli.build_config(tmp_path / "home", tmp_path / "projects", port, narrator_client=offline_client()))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    from mission_control.server import load_key

    key = load_key(tmp_path / "home")
    opened = threading.Event()

    def hold_stream():
        try:
            with httpx.stream("GET", f"http://127.0.0.1:{port}/api/stream", cookies={"mc_key": key}, timeout=30) as r:
                opened.set()
                for _ in r.iter_lines():
                    pass
        except httpx.HTTPError:
            pass

    threading.Thread(target=hold_stream, daemon=True).start()
    assert opened.wait(3)
    server.should_exit = True
    t.join(4)
    assert not t.is_alive()


def _code_root(tmp_path, name):
    import subprocess

    repo = tmp_path / "code" / name
    repo.mkdir(parents=True)
    (repo / ".gitignore").write_text(".env\n")
    (repo / ".env").write_text("SECRET=x\n")
    (repo / "a.py").write_text("x = 1\n")
    for args in (["init", "-q"], ["add", "-A"], ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "i"]):
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)
    return tmp_path / "code"


def _repo_client(tmp_path, name):
    from fastapi.testclient import TestClient

    app = create_app(tmp_path / "home", tmp_path / "projects", PORT, narrator_client=offline_client(), code_root=_code_root(tmp_path, name))
    key = (tmp_path / "home" / "key").read_text().strip()
    c = TestClient(app, base_url=f"http://127.0.0.1:{PORT}")
    c.cookies.set("mc_key", key)
    return c


def test_repo_routes(tmp_path):
    (tmp_path / "projects").mkdir()
    with _repo_client(tmp_path, "proj") as c:
        c.cookies.clear()
        assert c.get("/api/repos").status_code == 403
        c.cookies.set("mc_key", (tmp_path / "home" / "key").read_text().strip())
        assert [r["id"] for r in c.get("/api/repos").json()] == ["proj"]
        assert c.get("/api/repos/proj/files").json() == {"files": [".gitignore", "a.py"]}
        assert c.get("/api/repos/proj/file", params={"path": "a.py"}).json() == {"path": "a.py", "text": "x = 1\n"}
        for url in ["/api/repos/../files", "/api/repos/..%2F/files", "/api/repos/nope/files", "/api/repos/.git/files"]:
            assert c.get(url).status_code == 404, url
        assert c.get("/api/repos/proj/file", params={"path": ".env"}).status_code == 404
        assert c.get("/api/repos/proj/file", params={"path": "../../home/key"}).status_code == 404


def test_repo_id_with_dot(tmp_path):
    (tmp_path / "projects").mkdir()
    with _repo_client(tmp_path, "site.github.io") as c:
        assert [r["id"] for r in c.get("/api/repos").json()] == ["site.github.io"]
        assert c.get("/api/repos/site.github.io/file", params={"path": "a.py"}).status_code == 200
