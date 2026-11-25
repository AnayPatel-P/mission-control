import json

from .mask import mask_text

EDIT_TOOLS = {"Edit", "Write", "MultiEdit"}


class LogParser:
    def __init__(self, session_id: str, agent_id: str, pending_ttl: int = 500):
        self.session_id = session_id
        self.agent_id = agent_id
        self.pending_ttl = pending_ttl
        self._pending: dict[str, tuple[str, int]] = {}  # tool_use_id -> (file_path, line no)
        self._lines = 0
        self._note = ""  # latest assistant text block seen in this file

    def _base(self, typ: str, tid: str, ts: str) -> dict:
        return {
            "type": typ,
            "session_id": self.session_id,
            "agent_id": self.agent_id,
            "ts": ts,
            "tool_use_id": tid,
        }

    def feed(self, line: str) -> list[dict]:
        self._lines += 1
        if self._pending:
            cut = self._lines - self.pending_ttl
            for k in [k for k, (_, n) in self._pending.items() if n < cut]:
                del self._pending[k]
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
        out = []
        for b in content:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "text":
                self._note = b.get("text") or ""
            elif b.get("type") == "tool_use":
                ev = self._tool_use(b, self._note, ts)
                if ev:
                    out.append(ev)
        return out

    def _tool_use(self, b: dict, note: str, ts: str) -> dict | None:
        name, inp, tid = b.get("name"), b.get("input"), b.get("id") or ""
        if not isinstance(inp, dict):
            return None
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
            self._pending[tid] = (path, self._lines)
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
                path = res.get("filePath") or self._pending[tid][0]
                self._pending.pop(tid, None)
                out.append(self._edit_result(tid, ts, path, res))
        return out

    def _edit_result(self, tid: str, ts: str, path: str, res: dict) -> dict:
        patch, added, removed = [], 0, 0
        for h in res.get("structuredPatch") or []:
            if not isinstance(h, dict):
                continue
            raw = [str(x) for x in h.get("lines") or []]
            added += sum(1 for x in raw if x.startswith("+"))
            removed += sum(1 for x in raw if x.startswith("-"))
            patch.append({**{k: h.get(k) for k in ("oldStart", "oldLines", "newStart", "newLines")}, "lines": _mask_hunk(raw, path)})
        content = res.get("content")
        if not patch and isinstance(content, str) and content:
            # A Write that creates a file logs an empty structuredPatch and the full text in `content`.
            lines = mask_text(content, path).split("\n")
            if lines[-1] == "":
                lines.pop()
            added = len(lines)
            patch.append({"oldStart": 0, "oldLines": 0, "newStart": 1, "newLines": added, "lines": ["+" + x for x in lines]})
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


def _mask_hunk(lines: list[str], path: str) -> list[str]:
    """Mask a hunk as one text (prefix stripped) so line- and block-level rules fire."""
    if not lines:
        return []
    prefixes = [x[:1] for x in lines]
    out = mask_text("\n".join(x[1:] for x in lines), path).split("\n")
    if len(out) == len(lines):
        return [p + o for p, o in zip(prefixes, out)]
    res, i = [], 0  # a multi-line secret collapsed: mask each run of same-prefix lines on its own
    while i < len(lines):
        j = i
        while j < len(lines) and prefixes[j] == prefixes[i]:
            j += 1
        chunk = mask_text("\n".join(x[1:] for x in lines[i:j]), path).split("\n")
        res += [prefixes[i] + o for o in chunk]
        i = j
    return res
