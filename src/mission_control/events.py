import json

from .mask import mask_text

EDIT_TOOLS = {"Edit", "Write", "MultiEdit"}


class LogParser:
    def __init__(self, session_id: str, agent_id: str):
        self.session_id = session_id
        self.agent_id = agent_id
        self._pending: dict[str, str] = {}  # tool_use_id -> file_path of edit calls

    def _base(self, typ: str, tid: str, ts: str) -> dict:
        return {
            "type": typ,
            "session_id": self.session_id,
            "agent_id": self.agent_id,
            "ts": ts,
            "tool_use_id": tid,
        }

    def feed(self, line: str) -> list[dict]:
        line = line.strip()
        if not line:
            return []
        try:
            obj = json.loads(line)
        except ValueError:
            return []
        if not isinstance(obj, dict):
            return []
        kind = obj.get("type")
        if kind == "assistant":
            return self._assistant(obj)
        if kind == "user":
            return self._user(obj)
        return []

    def _assistant(self, obj: dict) -> list[dict]:
        content = (obj.get("message") or {}).get("content")
        if not isinstance(content, list):
            return []
        ts = obj.get("timestamp") or ""
        note, out = "", []
        for b in content:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "text":
                note = b.get("text") or ""
            elif b.get("type") == "tool_use":
                ev = self._tool_use(b, note, ts)
                if ev:
                    out.append(ev)
        return out

    def _tool_use(self, b: dict, note: str, ts: str) -> dict | None:
        name, inp, tid = b.get("name"), b.get("input") or {}, b.get("id") or ""
        if name in EDIT_TOOLS:
            path = inp.get("file_path") or ""
            if name == "Write":
                old, new = "", inp.get("content") or ""
            elif name == "Edit":
                old, new = inp.get("old_string") or "", inp.get("new_string") or ""
            else:
                edits = [e for e in inp.get("edits") or [] if isinstance(e, dict)]
                old = "\n".join(e.get("old_string") or "" for e in edits)
                new = "\n".join(e.get("new_string") or "" for e in edits)
            self._pending[tid] = path
            ev = self._base("edit", tid, ts)
            ev.update(
                kind=name,
                file_path=path,
                note=mask_text(note, path),
                old_text=mask_text(old, path),
                new_text=mask_text(new, path),
            )
            return ev
        if name == "Bash":
            ev = self._base("bash", tid, ts)
            ev.update(command=mask_text(inp.get("command") or ""), note=mask_text(note))
            return ev
        return None

    def _user(self, obj: dict) -> list[dict]:
        content = (obj.get("message") or {}).get("content")
        if not isinstance(content, list):
            return []
        ts = obj.get("timestamp") or ""
        res = obj.get("toolUseResult")
        out = []
        for b in content:
            if not isinstance(b, dict) or b.get("type") != "tool_result":
                continue
            tid = b.get("tool_use_id") or ""
            if b.get("is_error"):
                self._pending.pop(tid, None)
                ev = self._base("error", tid, ts)
                ev["message"] = mask_text(_text(b.get("content")))[:300]
                out.append(ev)
            elif tid in self._pending and isinstance(res, dict):
                path = res.get("filePath") or self._pending.pop(tid)
                self._pending.pop(tid, None)
                out.append(self._edit_result(tid, ts, path, res))
        return out

    def _edit_result(self, tid: str, ts: str, path: str, res: dict) -> dict:
        patch, added, removed = [], 0, 0
        for h in res.get("structuredPatch") or []:
            lines = [mask_text(str(x), path) for x in h.get("lines") or []]
            added += sum(1 for x in lines if x.startswith("+"))
            removed += sum(1 for x in lines if x.startswith("-"))
            patch.append({**{k: h.get(k) for k in ("oldStart", "oldLines", "newStart", "newLines")}, "lines": lines})
        orig = res.get("originalFile")
        ev = self._base("edit_result", tid, ts)
        ev.update(
            file_path=path,
            patch=patch,
            original_file=mask_text(orig, path) if isinstance(orig, str) else None,
            added=added,
            removed=removed,
        )
        return ev


def _text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b.get("text", "") for b in content if isinstance(b, dict))
    return ""
