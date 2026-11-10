import copy
import csv
import json
from contextlib import nullcontext
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from experiment import checks, run_lock
from experiment.browser_collectors import BrowserResult
from experiment.vpn import VpnState


class BrowserStudyCheckTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.cfg = copy.deepcopy(checks.load_config())
        self.cfg["results"]["checks_dir"] = str(self.root / "checks")
        self.cfg["results"]["csv_path"] = str(self.root / "live-results.csv")
        self.cfg["results"]["studies_dir"] = str(self.root / "studies")
        (self.root / "live-results.csv").write_text(
            "observation_id,day,question_id,collector,provider,language,vpn_location,"
            "timestamp,exit_ip,detected_country_code,vpn_verified,model_requested,"
            "model_observed,prompt,response,status,error,latency_ms,attempts,"
            "manual_intervention\n",
            encoding="utf-8",
        )
        self.live_schedule = self.root / "schedule.json"
        self.live_schedule.write_text('[{"day": 1, "run_at": "2099-01-01T12:00:00+00:00", '
                                      '"location_order": ["US", "IL"], "completed_modes": []}]\n',
                                      encoding="utf-8")
        self.schedule_before = self.live_schedule.read_bytes()
        self.csv_before = (self.root / "live-results.csv").read_bytes()

        def vpn(cfg, location, loc_cfg):
            return VpnState(location, location, "192.0.2.1", location, location, True, {})

        question = {"day": 1, "question_id": "Q01", "en": "English", "he": "עברית"}
        for name, value in (
            ("load_config", lambda: copy.deepcopy(self.cfg)),
            ("connect_vpn", vpn),
            ("disconnect_vpn", lambda cfg: None),
            ("add_entry", lambda *a, **k: None),
            ("collect_browser", lambda **k: BrowserResult("answer", None, 1)),
        ):
            mocked = patch.object(checks, name, value)
            mocked.start()
            self.addCleanup(mocked.stop)
        mocked = patch.object(checks.runner, "load_config", lambda: copy.deepcopy(self.cfg))
        mocked.start()
        self.addCleanup(mocked.stop)
        mocked = patch.object(checks.runner, "_question_for_day", lambda day: question)
        mocked.start()
        self.addCleanup(mocked.stop)
        mocked = patch.object(checks.runner, "default_schedule_path", lambda: self.live_schedule)
        mocked.start()
        self.addCleanup(mocked.stop)
        mocked = patch.object(checks.runner, "ROOT", self.root)
        mocked.start()
        self.addCleanup(mocked.stop)
        mocked = patch.object(checks, "ROOT", self.root)
        mocked.start()
        self.addCleanup(mocked.stop)
        mocked = patch.object(checks.runner, "collect_browser",
                              lambda **k: BrowserResult("answer", None, 1))
        mocked.start()
        self.addCleanup(mocked.stop)
        mocked = patch.object(checks.runner, "connect_vpn", vpn)
        mocked.start()
        self.addCleanup(mocked.stop)
        mocked = patch.object(checks.runner, "disconnect_vpn", lambda cfg: None)
        mocked.start()
        self.addCleanup(mocked.stop)
        mocked = patch.object(checks.runner, "vpn_session", lambda cfg: nullcontext())
        mocked.start()
        self.addCleanup(mocked.stop)
        mocked = patch.object(run_lock, "LOCK_PATH", self.root / "collection.lock")
        mocked.start()
        self.addCleanup(mocked.stop)

    def test_busy_collection_refuses_test_before_sandbox_or_live_changes(self):
        with run_lock.collection_lock():
            before = {str(path): path.read_bytes() for path in self.root.rglob("*") if path.is_file()}
            with patch.object(checks.runner, "run_day") as collect:
                with self.assertRaises(run_lock.CollectionBusy):
                    checks.run_browser_study_check()
                collect.assert_not_called()
            after = {str(path): path.read_bytes() for path in self.root.rglob("*") if path.is_file()}
            self.assertEqual(after, before)
        self.assertFalse((self.root / "checks").exists())

    def test_initial_collection_and_direct_rerun_both_hold_collection_lock(self):
        run_day = checks.runner.run_day
        calls = []

        def collect(*args, **kwargs):
            with self.assertRaises(run_lock.CollectionBusy):
                with run_lock.collection_lock():
                    self.fail("A second collector acquired the lock during the smoke test")
            calls.append(args[0])
            return run_day(*args, **kwargs)

        with patch.object(checks.runner, "run_day", side_effect=collect):
            _, ok = checks.run_browser_study_check()
        self.assertTrue(ok)
        self.assertEqual(calls, [1, 1])
        with run_lock.collection_lock():
            pass

    def test_sandboxed_pipeline_skips_rerun_and_draws_day_two(self):
        path, ok = checks.run_browser_study_check()
        self.assertTrue(ok)
        record = json.loads(Path(path).read_text(encoding="utf-8"))
        self.assertTrue(record["ok"])
        self.assertEqual(len(record.get("locations", [])), 2)
        self.assertTrue(any(loc.get("browser") for loc in record["locations"]))
        self.assertEqual(record["steps"]["expected_rows"], 12)
        self.assertEqual(record["steps"]["first_run"]["csv_rows"], 12)
        self.assertEqual(record["steps"]["second_run"]["skipped"], 12)
        self.assertEqual(record["steps"]["second_run"]["new_browser_answers"], 0)
        self.assertTrue(record["steps"]["live_schedule_unchanged"])
        self.assertTrue(record["steps"]["live_csv_unchanged"])
        self.assertEqual(self.live_schedule.read_bytes(), self.schedule_before)
        self.assertEqual((self.root / "live-results.csv").read_bytes(), self.csv_before)

        sandbox = self.root / record["steps"]["sandbox"]
        with (sandbox / "results.csv").open(encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        self.assertEqual(len(rows), 12)
        self.assertTrue(all(r["status"] == "ok" for r in rows))
        schedule = json.loads((sandbox / "schedule.json").read_text(encoding="utf-8"))
        self.assertIn("browser", schedule[0]["completed_modes"])
        self.assertEqual(schedule[1]["day"], 2)


if __name__ == "__main__":
    unittest.main()
