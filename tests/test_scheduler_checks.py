import copy
from datetime import datetime, timedelta
import json
from pathlib import Path
import plistlib
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

from experiment import scheduler_checks as checks


class SchedulerCheckTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.cfg = copy.deepcopy(checks.load_config())
        self.cfg["experiment"]["schedule_policy"] = "random"
        self.cfg["experiment"]["recovery_policy"] = "skip"
        study = self.root / "studies" / "test_study"
        study.mkdir(parents=True)
        (study / "runs").mkdir()
        self.cfg["results"]["studies_dir"] = str(self.root / "studies")
        for key, name in (("csv_path", "results.csv"), ("checks_dir", "results/checks"),
                          ("runs_dir", "runs"), ("index_path", "README.md"),
                          ("details_path", "results.md"), ("failure_screenshot_dir", "failures"),
                          ("schedule_path", "schedule.json")):
            if key == "checks_dir":
                self.cfg["results"][key] = str(self.root / name)
            else:
                self.cfg["results"][key] = str(study / name)
        (self.root / "studies" / "ACTIVE").write_text("test_study\n", encoding="utf-8")
        python = self.root / ".venv/bin/python"
        python.parent.mkdir(parents=True)
        python.write_text("fixture")
        python.chmod(0o755)
        (self.root / "main.py").write_text("fixture")
        self.schedule_path = study / "schedule.json"
        self.schedule_path.write_text(json.dumps([{
            "day": 1, "run_at": (datetime.now(ZoneInfo("UTC")) + timedelta(days=1)).isoformat(),
            "location_order": list(self.cfg["locations"]), "completed_modes": [],
        }]))
        self.agent = copy.deepcopy(checks.agent_config())
        self.agent["ProgramArguments"][0] = str(python)
        destination = self.root / "Library/LaunchAgents" / (checks.LABEL + ".plist")
        destination.parent.mkdir(parents=True)
        destination.write_bytes(plistlib.dumps(self.agent))
        self.destination = destination
        for target, kwargs in (("ROOT", {"new": self.root}),
                               ("load_config", {"return_value": self.cfg}),
                               ("agent_config", {"return_value": self.agent})):
            mocked = patch.object(checks, target, **kwargs)
            mocked.start()
            self.addCleanup(mocked.stop)
        from experiment import study as study_mod
        mocked = patch.object(study_mod, "load_config", lambda: copy.deepcopy(self.cfg))
        mocked.start()
        self.addCleanup(mocked.stop)
        mocked = patch.object(study_mod, "ROOT", self.root)
        mocked.start()
        self.addCleanup(mocked.stop)
        def resolve(path_str: str) -> Path:
            p = Path(path_str)
            return p if p.is_absolute() else self.root / p
        mocked = patch.object(study_mod, "resolve_path", resolve)
        mocked.start()
        self.addCleanup(mocked.stop)
        mocked = patch.object(checks, "resolve_path", resolve)
        mocked.start()
        self.addCleanup(mocked.stop)
        mocked = patch.object(Path, "home", return_value=self.root)
        mocked.start()
        self.addCleanup(mocked.stop)
        mocked = patch.object(checks.sys, "platform", "darwin")
        mocked.start()
        self.addCleanup(mocked.stop)
        mocked = patch.object(checks.shutil, "which", return_value="/usr/local/bin/mullvad")
        mocked.start()
        self.addCleanup(mocked.stop)

    def test_preflight_saves_json_and_report_without_changing_schedule(self):
        before = self.schedule_path.read_bytes()
        status = {"state": "not running", "runs": 5, "last_exit_code": 0}
        with patch.object(checks, "_status", return_value=status), patch.object(checks, "_launchctl") as launch:
            path, ok = checks.run_scheduler_check()
        self.assertTrue(ok)
        launch.assert_not_called()
        self.assertEqual(self.schedule_path.read_bytes(), before)
        record = json.loads(path.read_text())
        self.assertEqual(len(record["diagnostics"]), 4)
        self.assertEqual(record["locations"], [])
        self.assertIn("configuration_and_schedule", (self.root / "results/checks/results.md").read_text())
        # Study-local index (active study) plus global checks report.
        study_readme = self.root / "studies" / "test_study" / "README.md"
        self.assertTrue(study_readme.is_file() or (self.root / "results/README.md").is_file())
        self.assertFalse((self.root / "studies" / "test_study" / "results.csv").exists())

    def test_missing_plist_is_saved_as_failure(self):
        self.destination.unlink()
        path, ok = checks.run_scheduler_check(probe=True)
        self.assertFalse(ok)
        record = json.loads(path.read_text())
        self.assertTrue(any(d["name"] == "installed_launchagent" and d["status"] == "error" for d in record["diagnostics"]))
        self.assertEqual(record["diagnostics"][-1]["detail"], "Probe not attempted because prerequisites failed")

    def test_plist_drift_and_bad_schedule_do_not_pass(self):
        self.agent = {**self.agent, "StartInterval": 120}
        self.destination.write_bytes(plistlib.dumps(self.agent))
        # Installer still expects the original interval.
        with patch.object(checks, "agent_config", return_value={**self.agent, "StartInterval": 60}):
            _, ok = checks.run_scheduler_check()
        self.assertFalse(ok)
        event = json.loads(self.schedule_path.read_text())[0]
        event["run_at"] = "2026-10-19T10:00:00"
        self.schedule_path.write_text(json.dumps([event]))
        with self.assertRaisesRegex(ValueError, "timezone"):
            checks._read_schedule(self.cfg)

    def test_missing_schedule_is_not_created(self):
        self.schedule_path.unlink()
        with self.assertRaises(ValueError):
            checks._configuration(self.cfg)
        self.assertFalse(self.schedule_path.exists())

    def test_extra_program_override_is_rejected(self):
        self.destination.write_bytes(plistlib.dumps({**self.agent, "Program": "/bin/false"}))
        with self.assertRaisesRegex(ValueError, "Program"):
            checks._installed_agent()

    def test_failed_old_status_can_be_replaced_by_successful_probe(self):
        after = {"state": "not running", "runs": 4, "last_exit_code": 0}
        with patch.object(checks, "_loaded", side_effect=ValueError("Old failure")), patch.object(checks, "_probe", return_value=after):
            path, ok = checks.run_scheduler_check(probe=True)
        self.assertTrue(ok)
        status = next(d for d in json.loads(path.read_text())["diagnostics"] if d["name"] == "launchd_status")
        self.assertIn("Old failure", status["previous_detail"])
        self.assertEqual(status["detail"], after)

    def test_due_or_running_agent_cannot_be_probed(self):
        event = json.loads(self.schedule_path.read_text())[0]
        event["run_at"] = (datetime.now(ZoneInfo("UTC")) - timedelta(minutes=1)).isoformat()
        self.schedule_path.write_text(json.dumps([event]))
        with patch.object(checks, "_launchctl") as launch:
            with self.assertRaisesRegex(ValueError, "due"):
                checks._probe(self.cfg)
            launch.assert_not_called()
        event["run_at"] = (datetime.now(ZoneInfo("UTC")) + timedelta(days=1)).isoformat()
        self.schedule_path.write_text(json.dumps([event]))
        with patch.object(checks, "_status", return_value={"state": "running"}), patch.object(checks, "_launchctl") as launch:
            with self.assertRaisesRegex(ValueError, "already running"):
                checks._probe(self.cfg)
            launch.assert_not_called()

    def test_fresh_probe_and_nonzero_exit(self):
        before = {"state": "not running", "runs": 3, "last_exit_code": 1}
        after = {"state": "not running", "runs": 4, "last_exit_code": 0}
        with patch.object(checks, "_status", side_effect=[before, after]), patch.object(checks, "_launchctl") as launch:
            result = checks._probe(self.cfg)
        self.assertTrue(result["live_schedule_unchanged"])
        launch.assert_called_once()
        self.assertEqual(launch.call_args.args[0], "kickstart")
        with patch.object(checks, "_status", side_effect=[before, {**after, "last_exit_code": 1}]), patch.object(checks, "_launchctl"):
            with self.assertRaisesRegex(ValueError, "exited 1"):
                checks._probe(self.cfg)

    def test_launchctl_parsing_and_unloaded_error(self):
        output = "state = not running\nruns = 7\nlast exit code = 0\n"
        with patch.object(checks.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, output, "")):
            self.assertEqual(checks._status(), {"state": "not running", "runs": 7, "last_exit_code": 0})
        with patch.object(checks.subprocess, "run", return_value=subprocess.CompletedProcess([], 1, "", "Service not found")):
            with self.assertRaisesRegex(RuntimeError, "unloaded"):
                checks._loaded()


if __name__ == "__main__":
    unittest.main()
