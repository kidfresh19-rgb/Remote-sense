"""Unit tests for start.py's hardened detached-frontend lifecycle (the code-review PID findings).
Zero network, no process is actually launched: subprocess and the identity check are stubbed. The
critical guard is that a recorded PID reused by an unrelated process is never killed."""

from __future__ import annotations

import start


class _Completed:
    def __init__(self, stdout: str) -> None:
        self.stdout = stdout
        self.returncode = 0


def test_frontend_pid_reads_and_cleans_malformed(tmp_path, monkeypatch):
    pidfile = tmp_path / ".frontend.pid"
    monkeypatch.setattr(start, "FRONTEND_PIDFILE", pidfile)
    assert start._frontend_pid() is None  # absent
    pidfile.write_text("1234", encoding="utf-8")
    assert start._frontend_pid() == 1234
    pidfile.write_text("not-a-pid", encoding="utf-8")
    assert start._frontend_pid() is None  # malformed -> treated as absent
    assert not pidfile.exists()  # ...and the bad pidfile is removed


def test_looks_like_frontend_true_for_node(monkeypatch):
    monkeypatch.setattr(
        start.subprocess, "run", lambda *a, **k: _Completed('"node.exe","1234","Console"')
    )
    assert start._looks_like_frontend(1234) is True


def test_looks_like_frontend_false_for_unrelated(monkeypatch):
    monkeypatch.setattr(start.subprocess, "run", lambda *a, **k: _Completed("nothing relevant"))
    assert start._looks_like_frontend(1234) is False


def test_stop_frontend_does_not_kill_a_recycled_pid(tmp_path, monkeypatch):
    pidfile = tmp_path / ".frontend.pid"
    pidfile.write_text("424242", encoding="utf-8")
    monkeypatch.setattr(start, "FRONTEND_PIDFILE", pidfile)
    monkeypatch.setattr(start, "_looks_like_frontend", lambda pid: False)  # PID reused / dead
    killed: list = []
    monkeypatch.setattr(start.subprocess, "run", lambda *a, **k: killed.append(a))
    start.stop_frontend()
    assert killed == []  # nothing is force-killed
    assert not pidfile.exists()  # the stale record is dropped


def test_stop_frontend_noop_without_pidfile(tmp_path, monkeypatch):
    monkeypatch.setattr(start, "FRONTEND_PIDFILE", tmp_path / "absent.pid")
    killed: list = []
    monkeypatch.setattr(start.subprocess, "run", lambda *a, **k: killed.append(a))
    start.stop_frontend()
    assert killed == []
