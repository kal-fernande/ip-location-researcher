"""Exclusive lock so LaunchAgent cannot start a second collector while one is waiting."""
from __future__ import annotations

import fcntl
import os
from contextlib import contextmanager
from pathlib import Path

from .config import ROOT

LOCK_PATH = ROOT / "private" / "scheduler" / "collection.lock"


class CollectionBusy(RuntimeError):
    """Another collection process holds the lock."""


def collection_running() -> bool:
    """Inspect the lock without creating it or trusting an old PID."""
    try:
        with LOCK_PATH.open("r", encoding="utf-8") as handle:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_SH | fcntl.LOCK_NB)
            except BlockingIOError:
                return True
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except FileNotFoundError:
        pass
    return False


@contextmanager
def collection_lock(*, blocking: bool = False):
    """Hold an exclusive flock for the duration of VPN/browser collection.

    Non-blocking (default): raise CollectionBusy if another process is collecting.
    LaunchAgent can then exit cleanly instead of overlapping a CAPTCHA wait.
    """
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    fh = LOCK_PATH.open("a+", encoding="utf-8")
    flags = fcntl.LOCK_EX if blocking else fcntl.LOCK_EX | fcntl.LOCK_NB
    try:
        fcntl.flock(fh.fileno(), flags)
    except BlockingIOError as e:
        holder = ""
        try:
            fh.seek(0)
            holder = fh.read().strip()
        except Exception:
            pass
        fh.close()
        detail = f" (pid {holder})" if holder else ""
        raise CollectionBusy(
            f"Collection already running{detail}; skip this run-due. "
            f"Lock: {LOCK_PATH}"
        ) from e
    try:
        fh.seek(0)
        fh.truncate()
        fh.write(f"{os.getpid()}\n")
        fh.flush()
        yield Path(LOCK_PATH)
    finally:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        finally:
            fh.close()
