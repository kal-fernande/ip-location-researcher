import copy
from datetime import datetime, timedelta
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

from experiment import launchagent_study as delayed
from scripts.install_scheduler import agent_config, LABEL


class DelayedStudyTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.cfg = copy.deepcopy(delayed.load_config())
        self.cfg["results"]["checks_dir"] = str(self.root / "checks")
        self.cfg["results"]["csv_path"] = str(self.root / "live.csv")
        self.schedule = [{"day": 1, "run_at": (datetime.now(ZoneInfo("UTC")) + timedelta(days=1)).isoformat(),
                          "location_order": list(self.cfg["locations"]), "completed_modes": []}]
        (self.root / "schedule.json").write_text(json.dumps(self.schedule))
        (self.root / "live.csv").write_text("LIVE DATA UNCHANGED\n")
        for name, value in (("ROOT", self.root), ("load_config", lambda: self.cfg),
                            ("add_entry", lambda *args, **kwargs: None)):
            mock = patch.object(delayed, name, value)
            mock.start()
            self.addCleanup(mock.stop)
        mock = patch.object(delayed.sys, "platform", "darwin")
        mock.start()
        self.addCleanup(mock.stop)
        mock = patch.object(delayed, "_status", return_value={"state": "not running", "runs": 2, "last_exit_code": 0})
        self.status = mock.start()
        self.addCleanup(mock.stop)

    def fake_worker(self, sandbox, label, log_dir):
        manifest = delayed._read(sandbox / "launch-request.json")
        started = {"started_at": (datetime.fromisoformat(manifest["not_before"]) + timedelta(seconds=1)).isoformat()}
        delayed._save(sandbox / "check-summary.json", {"steps": {"sandbox": str(sandbox.relative_to(self.root))},
                                                       "locations": []})
        return started, {"ok": True}, {"state": "not running", "runs": 1, "last_exit_code": 0}

    def test_calendar_definition_keeps_real_background_settings(self):
        agent = delayed._temporary_agent(self.root, LABEL + ".test", self.root)
        expected = agent_config()
        for key in ("ProcessType", "EnvironmentVariables", "WorkingDirectory", "LimitLoadToSessionType"):
            self.assertEqual(agent[key], expected[key])
        self.assertFalse(agent["RunAtLoad"])
        self.assertEqual(agent["StartCalendarInterval"], [{"Minute": n} for n in range(60)])
        self.assertNotIn("StartInterval", agent)
        self.assertEqual(agent["ProgramArguments"][-2:], ["--launchagent-worker", str(self.root)])

    def test_parent_bootstraps_and_cleans_up_without_kickstart(self):
        before = (self.root / "schedule.json").read_bytes()
        with patch.object(delayed, "_launchctl") as ctl, patch.object(delayed, "_wait_for_worker", side_effect=self.fake_worker):
            path, ok = delayed.run_delayed_study_check()
        self.assertTrue(ok)
        self.assertEqual([c.args[0] for c in ctl.call_args_list], ["bootstrap", "bootout"])
        plist = Path(ctl.call_args_list[0].args[2])
        self.assertFalse(plist.exists())
        self.assertNotEqual(ctl.call_args_list[-1].args[1], f"gui/{delayed.os.getuid()}/{LABEL}")
        record = delayed._read(path)
        self.assertTrue(record["steps"]["launchagent"]["cleanup_ok"])
        self.assertFalse(record["steps"]["launchagent"]["manual_launch"])
        self.assertEqual((self.root / "schedule.json").read_bytes(), before)
        self.assertEqual((self.root / "live.csv").read_text(), "LIVE DATA UNCHANGED\n")

    def test_timer_timeout_saves_failure_and_unloads(self):
        with patch.object(delayed, "_launchctl") as ctl, patch.object(delayed, "_wait_for_worker", side_effect=TimeoutError("Timer did not fire")):
            path, ok = delayed.run_delayed_study_check()
        self.assertFalse(ok)
        self.assertIn("Timer did not fire", delayed._read(path)["errors"][0])
        self.assertEqual(ctl.call_args_list[-1].args[0], "bootout")

    def test_cleanup_failure_is_not_reported_as_pass(self):
        def ctl(command, *args):
            if command == "bootout":
                raise RuntimeError("Unload denied")
        with patch.object(delayed, "_launchctl", side_effect=ctl), patch.object(delayed, "_wait_for_worker", side_effect=self.fake_worker):
            path, ok = delayed.run_delayed_study_check()
        self.assertFalse(ok)
        self.assertFalse(delayed._read(path)["steps"]["launchagent"]["cleanup_ok"])

    def test_due_live_collection_prevents_temporary_agent_install(self):
        self.schedule[0]["run_at"] = datetime.now(ZoneInfo("UTC")).isoformat()
        (self.root / "schedule.json").write_text(json.dumps(self.schedule))
        with patch.object(delayed, "_launchctl") as ctl:
            path, ok = delayed.run_delayed_study_check()
        self.assertFalse(ok)
        ctl.assert_not_called()
        self.assertIn("Live browser collection is due", delayed._read(path)["errors"][0])

    def test_worker_uses_existing_smoke_path_and_does_not_repeat(self):
        sandbox = self.root / "checks/study-smoke/worker"
        sandbox.mkdir(parents=True)
        delayed._save(sandbox / "launch-request.json", {"not_before": (datetime.now(ZoneInfo("UTC")) - timedelta(seconds=1)).isoformat()})
        with patch("experiment.checks.run_browser_study_check", return_value=(sandbox / "check-summary.json", True)) as smoke:
            self.assertEqual(delayed.run_worker(sandbox), 0)
            self.assertEqual(delayed.run_worker(sandbox), 0)
        smoke.assert_called_once_with(sandbox=sandbox.resolve(), log_index=False)
        self.assertTrue(delayed._read(sandbox / "worker-result.json")["ok"])

    def test_worker_refuses_to_collect_before_delay(self):
        sandbox = self.root / "checks/study-smoke/early"
        sandbox.mkdir(parents=True)
        delayed._save(sandbox / "launch-request.json", {"not_before": (datetime.now(ZoneInfo("UTC")) + timedelta(seconds=60)).isoformat()})
        with patch("experiment.checks.run_browser_study_check") as smoke:
            self.assertEqual(delayed.run_worker(sandbox), 0)
        smoke.assert_not_called()
        self.assertFalse((sandbox / "worker.lock").exists())
        self.assertFalse((sandbox / "worker-started.json").exists())
        delayed._save(sandbox / "launch-request.json", {"not_before": (datetime.now(ZoneInfo("UTC")) - timedelta(seconds=1)).isoformat()})
        with patch("experiment.checks.run_browser_study_check", return_value=(sandbox / "check-summary.json", True)) as smoke:
            self.assertEqual(delayed.run_worker(sandbox), 0)
        smoke.assert_called_once()

    def test_wait_allows_successful_early_calendar_tick(self):
        sandbox = self.root / "waiting"
        sandbox.mkdir()
        self.status.side_effect = [
            {"state": "not running", "runs": 1, "last_exit_code": 0},
            {"state": "not running", "runs": 2, "last_exit_code": 0},
        ]
        def advance(seconds):
            delayed._save(sandbox / "worker-started.json", {"started_at": datetime.now(ZoneInfo("UTC")).isoformat()})
            delayed._save(sandbox / "worker-result.json", {"ok": True})
        with patch.object(delayed.time, "sleep", side_effect=advance):
            started, result, status = delayed._wait_for_worker(sandbox, "test", self.root)
        self.assertTrue(result["ok"])
        self.assertEqual(status["runs"], 2)

    def test_wait_reports_failure_if_python_never_starts(self):
        sandbox = self.root / "waiting"
        sandbox.mkdir()
        self.status.return_value = {"state": "not running", "runs": 1, "last_exit_code": 1}
        with self.assertRaisesRegex(RuntimeError, "Background worker exited 1"):
            delayed._wait_for_worker(sandbox, "test", self.root)


if __name__ == "__main__":
    unittest.main()
