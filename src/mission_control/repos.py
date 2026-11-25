"""Read-only access to tracked files of the git repos in the code root."""

from __future__ import annotations

import subprocess
from pathlib import Path

from .mask import mask_text

REPO_RE = r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$"
MAX_BYTES = 1_048_576


def list_repos(root: Path) -> list[dict]:
    try:
        dirs = [d for d in Path(root).iterdir() if d.is_dir() and (d / ".git").exists()]
    except OSError:
        return []
    out = [{"id": d.name, "path": str(d), "mtime": max(d.stat().st_mtime, (d / ".git").stat().st_mtime)} for d in dirs]
    return sorted(out, key=lambda r: -r["mtime"])


def list_files(repo: Path) -> list[str]:
    try:
        r = subprocess.run(["git", "-C", str(repo), "ls-files", "-z"], capture_output=True, timeout=5, check=False)
    except (OSError, subprocess.SubprocessError):
        return []
    if r.returncode:
        return []
    return sorted(p for p in r.stdout.decode("utf-8", "replace").split("\0") if p)


def read_file(repo: Path, rel: str) -> dict:
    if rel not in set(list_files(repo)):
        raise FileNotFoundError(rel)
    base = Path(repo).resolve()
    target = (base / rel).resolve()
    if not target.is_relative_to(base) or not target.is_file():
        raise FileNotFoundError(rel)
    if target.stat().st_size > MAX_BYTES:
        return {"path": rel, "note": "file over 1 MB"}
    data = target.read_bytes()
    try:
        if b"\0" in data[:8192]:
            raise UnicodeDecodeError("utf-8", b"", 0, 1, "nul")
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return {"path": rel, "note": "binary file"}
    return {"path": rel, "text": mask_text(text, rel)}
