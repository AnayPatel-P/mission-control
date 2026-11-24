"""File-based nudge inbox. Stdlib only: the hook imports this by path."""
import json
import os
import time
import uuid
from pathlib import Path
from typing import Literal, TypedDict

MAX_TEXT = 2000
TTL = 600


class Nudge(TypedDict):
    id: str
    session_id: str
    agent_id: str
    text: str
    created: float


def _mkdir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path, 0o700)


def _write(path: Path, data: dict) -> None:
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f)
    os.replace(tmp, path)


def enqueue(home: Path, session_id: str, agent_id: str, text: str) -> Nudge:
    if not text or len(text) > MAX_TEXT:
        raise ValueError(f"text must be 1-{MAX_TEXT} chars")
    nudge: Nudge = {
        "id": uuid.uuid4().hex,
        "session_id": session_id,
        "agent_id": agent_id,
        "text": text,
        "created": time.time(),
    }
    _mkdir(home)
    _mkdir(home / "inbox")
    d = home / "inbox" / session_id
    _mkdir(d)
    _write(d / f"{nudge['id']}.json", nudge)
    return nudge


def claim(home: Path, session_id: str, agent_id: str, now: float) -> list[Nudge]:
    d = home / "inbox" / session_id
    if not d.is_dir():
        return []
    got: list[dict] = []
    for f in d.glob("*.json"):
        try:
            n = json.loads(f.read_text())
            if n.get("agent_id", "main") != agent_id:
                continue
            expired = now - n["created"] > TTL
            dest = home / ("expired" if expired else "delivered")
            _mkdir(dest)
            os.rename(f, dest / f.name)  # atomic: only one claimer wins
        except (OSError, ValueError, KeyError):
            continue
        if not expired:
            n["delivered"] = now
            _write(dest / f.name, n)
            got.append(n)
    return sorted(got, key=lambda n: n["created"])  # type: ignore[return-value]


def status(
    home: Path, nudge_id: str, session_id: str, now: float
) -> Literal["queued", "delivered", "expired"]:
    if (home / "delivered" / f"{nudge_id}.json").exists():
        return "delivered"
    if (home / "expired" / f"{nudge_id}.json").exists():
        return "expired"
    f = home / "inbox" / session_id / f"{nudge_id}.json"
    try:
        created = json.loads(f.read_text())["created"]
    except (OSError, ValueError, KeyError):
        return "expired"
    return "expired" if now - created > TTL else "queued"
