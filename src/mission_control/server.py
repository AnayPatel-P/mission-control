import asyncio
import collections
import contextlib
import json
import os
import re
import secrets
import time
from pathlib import Path
from urllib.parse import urlencode

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import inbox
from .narrator import Narrator
from .watch import Watcher

SUBAGENT_NUDGES: bool = True
STATIC = Path(__file__).resolve().parents[2] / "static"
ID_RE = r"^[A-Za-z0-9_-]{1,128}$"
QUEUE_MAX = 1000
BUBBLES_PER_SESSION = 500
PENDING_EDITS = 500
PING_SECS = 15


def load_key(home: Path) -> str:
    home.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = home / "key"
    try:
        key = path.read_text().strip()
    except OSError:
        key = ""
    if len(key) < 32:  # missing, empty or truncated: replace atomically
        key = secrets.token_urlsafe(32)
        tmp = path.with_name(f"key.{os.getpid()}.tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(key)
        os.replace(tmp, path)
    os.chmod(path, 0o600)
    return key


class NudgeIn(BaseModel):
    session_id: str = Field(pattern=ID_RE)
    agent_id: str = Field("main", pattern=ID_RE)
    text: str = Field(min_length=1, max_length=inbox.MAX_TEXT)


def create_app(
    home: Path,
    projects: Path,
    port: int,
    narrator_backend: str | None = None,
    narrator_client: httpx.AsyncClient | None = None,
) -> FastAPI:
    home, projects = Path(home), Path(projects)
    key = load_key(home)
    origins = {f"http://127.0.0.1:{port}", f"http://localhost:{port}"}
    clients: set[asyncio.Queue] = set()
    bubbles: dict[str, collections.deque] = {}
    edits: collections.OrderedDict[str, dict] = collections.OrderedDict()
    state = {"live": False}

    def fan_out(msg: dict) -> None:
        for q in list(clients):
            try:
                q.put_nowait(msg)
            except asyncio.QueueFull:
                with contextlib.suppress(asyncio.QueueEmpty, asyncio.QueueFull):
                    q.get_nowait()  # slow client: drop its oldest message
                    q.put_nowait(msg)

    async def post_bubble(b: dict) -> None:
        bubbles.setdefault(b["session_id"], collections.deque(maxlen=BUBBLES_PER_SESSION)).append(b)
        fan_out(b)

    narrator = Narrator(post_bubble, backend=narrator_backend, client=narrator_client)

    def on_event(ev: dict) -> None:
        fan_out(ev)
        tid = ev.get("tool_use_id")
        if ev["type"] == "edit" and tid:
            edits[tid] = ev
            while len(edits) > PENDING_EDITS:
                edits.popitem(last=False)
        elif ev["type"] == "edit_result" and tid:
            src = edits.pop(tid, None)
            if not state["live"]:
                return  # history replay on startup is not narrated
            merged = dict(ev)
            if src:
                merged["note"], merged["kind"] = src.get("note"), src.get("kind")
            narrator.submit(merged)

    watcher = Watcher(projects, on_event)

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        watcher.scan()
        state["live"] = True
        task = asyncio.create_task(watcher.run())
        try:
            yield
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await narrator.aclose()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def guard(request: Request, call_next):
        supplied = request.query_params.get("key")
        from_query = supplied is not None
        supplied = supplied if from_query else request.cookies.get("mc_key", "")
        if not supplied or not secrets.compare_digest(supplied.encode(), key.encode()):
            return JSONResponse({"detail": "forbidden"}, status_code=403)
        if request.method == "POST" and request.headers.get("origin") not in origins:
            return JSONResponse({"detail": "bad origin"}, status_code=403)
        if from_query and request.method == "GET":
            rest = [(k, v) for k, v in request.query_params.multi_items() if k != "key"]
            resp = RedirectResponse(request.url.path + (f"?{urlencode(rest)}" if rest else ""), status_code=303)
        else:
            resp = await call_next(request)
        if from_query:
            resp.set_cookie("mc_key", key, httponly=True, samesite="strict")
        return resp

    @app.get("/")
    async def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/api/config")
    async def config():
        return {"subagent_nudges": SUBAGENT_NUDGES}

    @app.get("/api/sessions")
    async def sessions():
        return watcher.sessions()

    @app.get("/api/sessions/{session_id}/history")
    async def history(session_id: str):
        if not re.match(ID_RE, session_id):
            raise HTTPException(422)
        return {"events": watcher.history(session_id), "bubbles": list(bubbles.get(session_id, []))}

    @app.get("/api/stream")
    async def stream():
        q: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_MAX)

        async def gen():
            clients.add(q)
            try:
                while True:
                    try:
                        msg = await asyncio.wait_for(q.get(), PING_SECS)
                    except asyncio.TimeoutError:
                        yield ": ping\n\n"
                        continue
                    yield f"data: {json.dumps(msg)}\n\n"
            finally:
                clients.discard(q)

        return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})

    @app.post("/api/nudge")
    async def nudge(body: NudgeIn):
        active = any(s["id"] == body.session_id and s["active"] for s in watcher.sessions())
        if not active or (home / "stopped" / body.session_id).exists():  # the hook marks a finished turn
            return {"status": "idle"}
        n = inbox.enqueue(home, body.session_id, body.agent_id, body.text)
        return {"id": n["id"], "status": "queued"}

    @app.get("/api/nudge/{nudge_id}")
    async def nudge_status(nudge_id: str, session_id: str = ""):
        if not re.match(ID_RE, nudge_id) or not re.match(ID_RE, session_id):
            raise HTTPException(404)
        return {"status": inbox.status(home, nudge_id, session_id, time.time())}

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app
