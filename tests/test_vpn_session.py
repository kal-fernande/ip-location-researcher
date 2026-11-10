"""VPN session abort, wall-clock flag, and vpn-off study preservation."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from experiment import vpn


class VpnSessionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.abort = self.root / "abort.request"
        self.lock = self.root / "collection.lock"
        self.cfg = {
            "vpn": {
                "enabled": True,
                "disconnect_command": ["true"],
                "connect_command": ["true"],
                "max_session_seconds": 0,
            }
        }
        vpn.clear_abort()
        self.addCleanup(vpn.clear_abort)
        patcher_abort = patch.object(vpn, "ABORT_PATH", self.abort)
        patcher_lock = patch.object(vpn, "LOCK_PATH", self.lock)
        patcher_abort.start()
        patcher_lock.start()
        self.addCleanup(patcher_abort.stop)
        self.addCleanup(patcher_lock.stop)

    def test_raise_if_aborted_distinguishes_timeout(self):
        vpn.request_abort(timeout=True)
        with self.assertRaises(vpn.VpnSessionTimeout):
            vpn.raise_if_aborted()

    def test_raise_if_aborted_for_vpn_off(self):
        vpn.request_abort()
        with self.assertRaises(vpn.VpnSessionAborted):
            vpn.raise_if_aborted()

    def test_vpn_off_disconnects_and_clears_abort_when_idle(self):
        with patch.object(vpn, "disconnect_vpn") as disconnect:
            result = vpn.vpn_off(cfg=self.cfg, force_after_seconds=0.1)
        disconnect.assert_called_once()
        self.assertTrue(result["disconnected"])
        self.assertIsNone(result["collector_pid"])
        self.assertFalse(self.abort.exists())

    def test_vpn_off_signals_live_collector_then_clears_abort(self):
        fake_pid = 999999
        self.lock.write_text(f"{fake_pid}\n", encoding="utf-8")
        alive = {fake_pid: True}
        kills: list[int] = []

        def fake_alive(pid: int) -> bool:
            return bool(alive.get(pid))

        def fake_kill(pid: int, sig: int) -> None:
            kills.append(sig)
            alive[pid] = False

        with patch.object(vpn, "disconnect_vpn") as disconnect, \
             patch.object(vpn, "_pid_alive", side_effect=fake_alive), \
             patch.object(os, "kill", side_effect=fake_kill):
            result = vpn.vpn_off(cfg=self.cfg, force_after_seconds=0.2)

        disconnect.assert_called()
        self.assertEqual(result["collector_pid"], fake_pid)
        self.assertFalse(result["collector_alive"])
        self.assertIn("SIGUSR1", result["signaled"])
        self.assertTrue(kills)
        self.assertFalse(self.abort.exists())

    def test_vpn_session_clears_prior_abort(self):
        vpn.request_abort()
        self.assertTrue(self.abort.exists())
        with patch.object(vpn, "disconnect_vpn"), \
             patch.object(vpn, "install_cleanup_handlers"):
            with vpn.vpn_session(self.cfg):
                self.assertFalse(vpn.abort_requested())
        self.assertFalse(self.abort.exists())


if __name__ == "__main__":
    unittest.main()
