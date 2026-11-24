import asyncio
import collections
import json
import time
from collections.abc import Callable
from pathlib import Path

from .events import LogParser
from .mask import mask_text

ACTIVE_SECS = 120


class _File:
    def __init__(self, path: Path, session_id: str, agent_id: str, project: str, max_events: int, pending_ttl: int):
        self.path, self.agent_id, self.project = path, agent_id, project
        self.pending_ttl = pending_ttl
        self.parser = LogParser(session_id, agent_id, pending_ttl)
        self.offset = 0
        self.tail = b""
        self.events: collections.deque[dict] = collections.deque(maxlen=max_events)
        self.originals: collections.deque[dict] = collections.deque()
        self.mtime = 0.0

    def reset(self):
        self.offset, self.tail = 0, b""
        self.events.clear()
        self.originals.clear()
        self.parser = LogParser(self.parser.session_id, self.agent_id, self.pending_ttl)


class Watcher:
    def __init__(
        self,
        root: Path,
        on_event: Callable[[dict], None],
        history_days: float = 2,
        max_events: int = 2000,
        max_originals: int = 200,
        pending_ttl: int = 500,
    ):
        self.max_events, self.max_originals, self.pending_ttl = max_events, max_originals, pending_ttl
        self.root = Path(root)
        self.on_event = on_event
        self.history_days = history_days
        self._files: dict[Path, _File] = {}
        self._meta: dict[str, dict] = {}

    def _discover(self) -> list[tuple[Path, str, str, str]]:
        found = []
        for p in self.root.glob("*/*.jsonl"):
            found.append((p, p.stem, "main", p.parent.name))
        for p in self.root.glob("*/*/subagents/agent-*.jsonl"):
            found.append((p, p.parent.parent.name, p.stem[len("agent-"):], p.parent.parent.parent.name))
        return found

    def scan(self) -> None:
        cutoff = time.time() - self.history_days * 86400
        seen = set()
        for path, sid, aid, proj in sorted(self._discover(), key=lambda t: (t[1], t[2] != "main", t[2])):
            try:
                st = path.stat()
            except OSError:
                continue
            f = self._files.get(path)
            if f is None:
                if st.st_mtime < cutoff:
                    continue
                f = self._files[path] = _File(path, sid, aid, proj, self.max_events, self.pending_ttl)
                self._meta.setdefault(sid, {"project": proj, "cwd": "", "branch": "", "title": ""})
            seen.add(path)
            f.mtime = st.st_mtime
            if st.st_size < f.offset:
                f.reset()
            if st.st_size > f.offset:
                self._read(f, sid)
        for path in set(self._files) - seen:
            del self._files[path]
        live = {f.parser.session_id for f in self._files.values()}
        for sid in set(self._meta) - live:
            del self._meta[sid]

    def _read(self, f: _File, sid: str) -> None:
        try:
            with open(f.path, "rb") as fh:
                fh.seek(f.offset)
                data = fh.read()
        except OSError:
            return
        f.offset += len(data)
        parts = (f.tail + data).split(b"\n")
        f.tail = parts.pop()
        for raw in parts:
            line = raw.decode("utf-8", errors="replace")
            self._meta_from(sid, f, line)
            try:
                evs = f.parser.feed(line)
            except Exception:
                continue
            for ev in evs:
                self._store(f, ev)
                try:
                    self.on_event(ev)
                except Exception:
                    pass

    def _store(self, f: _File, ev: dict) -> None:
        ev = dict(ev)  # history keeps its own copy so trimming never touches what callers hold
        f.events.append(ev)
        if ev["type"] == "edit_result" and ev["original_file"] is not None:
            f.originals.append(ev)
            while len(f.originals) > self.max_originals:
                f.originals.popleft()["original_file"] = None

    def _meta_from(self, sid: str, f: _File, line: str) -> None:
        try:
            o = json.loads(line)
        except ValueError:
            return
        if not isinstance(o, dict):
            return
        m = self._meta[sid]
        if f.agent_id == "main":
            m["cwd"] = m["cwd"] or o.get("cwd") or ""
            m["branch"] = m["branch"] or o.get("gitBranch") or ""
            if not m["title"] and o.get("type") == "user":
                c = (o.get("message") or {}).get("content")
                if isinstance(c, list):
                    c = " ".join(b.get("text", "") for b in c if isinstance(b, dict) and b.get("type") == "text")
                if isinstance(c, str) and c.strip():
                    m["title"] = mask_text(c.strip())[:60]

    def sessions(self) -> list[dict]:
        now = time.time()
        out = []
        for sid, m in self._meta.items():
            fs = [f for f in self._files.values() if f.parser.session_id == sid]
            last = max(f.mtime for f in fs)
            agents = sorted({f.agent_id for f in fs}, key=lambda a: (a != "main", a))
            out.append({"id": sid, **m, "active": now - last < ACTIVE_SECS, "last_change": last, "agents": agents})
        return sorted(out, key=lambda s: -s["last_change"])

    def history(self, session_id: str) -> list[dict]:
        fs = [f for f in self._files.values() if f.parser.session_id == session_id]
        fs.sort(key=lambda f: (f.agent_id != "main", f.agent_id))
        return sorted((e for f in fs for e in f.events), key=lambda e: e["ts"])

    async def run(self, interval: float = 0.25) -> None:
        while True:
            try:
                self.scan()
            except Exception:
                pass
            await asyncio.sleep(interval)
