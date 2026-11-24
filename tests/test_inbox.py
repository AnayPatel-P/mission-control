import os
import stat

import pytest

from mission_control import inbox


def test_enqueue_then_claim_once(tmp_path):
    n = inbox.enqueue(tmp_path, "s1", "main", "hello")
    got = inbox.claim(tmp_path, "s1", "main", n["created"] + 1)
    assert len(got) == 1 and got[0]["text"] == "hello"
    assert inbox.claim(tmp_path, "s1", "main", n["created"] + 2) == []
    assert inbox.status(tmp_path, n["id"], "s1", n["created"] + 3) == "delivered"


def test_queued_status(tmp_path):
    n = inbox.enqueue(tmp_path, "s1", "main", "hi")
    assert inbox.status(tmp_path, n["id"], "s1", n["created"] + 1) == "queued"


def test_claim_respects_agent(tmp_path):
    n = inbox.enqueue(tmp_path, "s1", "a1", "hello")
    assert inbox.claim(tmp_path, "s1", "main", n["created"] + 1) == []
    assert len(inbox.claim(tmp_path, "s1", "a1", n["created"] + 1)) == 1


def test_expiry(tmp_path):
    n = inbox.enqueue(tmp_path, "s1", "main", "hello")
    assert inbox.claim(tmp_path, "s1", "main", n["created"] + 601) == []
    assert inbox.status(tmp_path, n["id"], "s1", n["created"] + 601) == "expired"


def test_limits(tmp_path):
    for bad in ("", "x" * 2001):
        with pytest.raises(ValueError):
            inbox.enqueue(tmp_path, "s1", "main", bad)
    inbox.enqueue(tmp_path, "s1", "main", "x" * 2000)


def test_permissions(tmp_path):
    n = inbox.enqueue(tmp_path, "s1", "main", "hello")
    d = tmp_path / "inbox" / "s1"
    assert stat.S_IMODE(os.stat(d).st_mode) == 0o700
    assert stat.S_IMODE(os.stat(d / f"{n['id']}.json").st_mode) == 0o600


def test_claim_returns_nudge_when_rewrite_fails(tmp_path, monkeypatch):
    n = inbox.enqueue(tmp_path, "s1", "main", "hello")

    def boom(*a, **k):
        raise OSError("disk")

    monkeypatch.setattr(inbox, "_write", boom)
    got = inbox.claim(tmp_path, "s1", "main", n["created"] + 1)
    assert [g["id"] for g in got] == [n["id"]]
