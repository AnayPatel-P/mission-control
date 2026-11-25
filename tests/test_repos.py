import os
import subprocess
from pathlib import Path

import pytest

from mission_control import repos


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


@pytest.fixture
def root(tmp_path: Path) -> Path:
    r = tmp_path / "code"
    p = r / "proj"
    (p / "src").mkdir(parents=True)
    (p / "a.py").write_text("KEY = 'AKIAIOSFODNN7EXAMPLE'\nprint(KEY)\n")
    (p / "src" / "b.py").write_text("x = 1\n")
    (p / ".gitignore").write_text(".env\n")
    (p / ".env").write_text("SECRET=hunter2hunter2\n")
    (p / "img.bin").write_bytes(b"\x89PNG\x00\x00\x01")
    (p / "big.txt").write_text("a" * 1_100_000)
    (p / "gone.py").write_text("y = 2\n")
    os.symlink("/etc/hosts", p / "out.txt")
    git(p, "init", "-q")
    git(p, "add", "-A")
    git(p, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init")
    (p / "gone.py").unlink()
    (r / "notes").mkdir()
    (r / "notes" / "n.md").write_text("hi\n")
    return r


def test_list_repos_only_git_dirs(root):
    assert [r["id"] for r in repos.list_repos(root)] == ["proj"]


def test_list_files_tracked_only(root):
    files = repos.list_files(root / "proj")
    assert "src/b.py" in files and "a.py" in files
    assert ".env" not in files


def test_read_masks(root):
    out = repos.read_file(root / "proj", "a.py")
    assert out["path"] == "a.py" and "print(KEY)" in out["text"]
    assert "AKIAIOSFODNN7EXAMPLE" not in out["text"]


@pytest.mark.parametrize("rel", [".env", "../notes/n.md", "out.txt", "gone.py", "src", "/etc/hosts"])
def test_read_rejects(root, rel):
    with pytest.raises(FileNotFoundError):
        repos.read_file(root / "proj", rel)


def test_read_binary_and_big(root):
    assert repos.read_file(root / "proj", "img.bin") == {"path": "img.bin", "note": "binary file"}
    assert repos.read_file(root / "proj", "big.txt") == {"path": "big.txt", "note": "file over 1 MB"}


def test_git_failure_returns_empty(root):
    assert repos.list_files(root / "notes") == []
    assert repos.list_repos(root / "missing") == []
