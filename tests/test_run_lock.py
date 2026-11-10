"""Exclusive collection lock for overlapping LaunchAgent run-due."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from experiment import run_lock as run_lock_mod
from experiment.run_lock import CollectionBusy, collection_lock


class CollectionLockTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        lock = self.root / "private" / "scheduler" / "collection.lock"
        mocked = patch.object(run_lock_mod, "LOCK_PATH", lock)
        mocked.start()
        self.addCleanup(mocked.stop)
        mocked = patch.object(run_lock_mod, "ROOT", self.root)
        mocked.start()
        self.addCleanup(mocked.stop)

    def test_second_acquire_raises_while_held(self):
        with collection_lock(blocking=False):
            with self.assertRaises(CollectionBusy):
                with collection_lock(blocking=False):
                    pass

    def test_lock_releases_for_next_caller(self):
        with collection_lock(blocking=False):
            pass
        with collection_lock(blocking=False):
            pass

    def test_running_status_tracks_the_lock_and_ignores_a_stale_pid(self):
        self.assertFalse(run_lock_mod.collection_running())
        self.assertFalse(run_lock_mod.LOCK_PATH.exists())
        with collection_lock():
            self.assertTrue(run_lock_mod.collection_running())
        # The PID remains on disk after release and must never trigger a stop.
        self.assertTrue(run_lock_mod.LOCK_PATH.read_text().strip())
        self.assertFalse(run_lock_mod.collection_running())


if __name__ == "__main__":
    unittest.main()
