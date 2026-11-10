"""Manual recovery with temporary study data and fully mocked VPN/browsers."""
from __future__ import annotations

import csv
import json
import tempfile
import unittest
import contextlib
import io
from contextlib import nullcontext
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

from experiment import manual_retry, run_lock, runner
from experiment.browser_collectors import BrowserResult
from experiment.storage import FIELDNAMES, ok_keys
from experiment.vpn import VpnState, VpnSessionTimeout


class ManualRetryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.csv = self.root / "results.csv"
        self.schedule_path = self.root / "schedule.json"
        self.now = datetime(2026, 11, 9, 12, tzinfo=ZoneInfo("America/Los_Angeles"))
        self.cfg = {
            "experiment": {
                "timezone": "America/Los_Angeles", "days": 2, "recovery_policy": "skip",
                "recovery": {"max_attempts_per_cell": 3}, "schedule_policy": "random",
                "schedule_window": {"start_hour": 8, "end_hour": 21},
                "browser_locale": "en-US", "browser_timezone": "UTC",
            },
            "api": {"enabled": False, "providers": {}},
            "browser": {"enabled": True, "providers": {"chatgpt": {"enabled": True, "url": "https://example.invalid"}}},
            "languages": {"en": "English", "he": "Hebrew"},
            "locations": {"US": {}, "IL": {}},
            "results": {
                "study_id": "test-study", "csv_path": str(self.csv), "schedule_path": str(self.schedule_path),
                "runs_dir": str(self.root / "runs"), "failure_screenshot_dir": str(self.root / "failures"),
            },
        }
        self.event = {"day": 1, "run_at": (self.now - timedelta(hours=2)).isoformat(),
                      "location_order": ["IL", "US"], "completed_modes": ["browser"]}
        self.next_event = {"day": 2, "run_at": (self.now + timedelta(days=1)).isoformat(),
                           "location_order": ["US", "IL"], "completed_modes": []}
        self.save_schedule([self.event, self.next_event])
        self.collect = Mock(return_value=BrowserResult("Recovered answer", None, 1))
        self.connect = Mock(side_effect=lambda cfg, name, location: VpnState(name, name, "192.0.2.1", name, name, True, {}))
        self.session = Mock(side_effect=lambda cfg: nullcontext())
        for module, key, value in (
            (run_lock, "LOCK_PATH", self.root / "collection.lock"),
            (runner, "_now", lambda cfg: self.now),
            (runner, "_question_for_day", lambda day: {"day": day, "question_id": f"Q{day:02d}", "en": "Question", "he": "שאלה"}),
            (runner, "collect_browser", self.collect), (runner, "connect_vpn", self.connect),
            (runner, "disconnect_vpn", Mock()), (runner, "vpn_session", self.session),
        ):
            mocked = patch.object(module, key, value)
            mocked.start()
            self.addCleanup(mocked.stop)

    def save_schedule(self, events):
        self.schedule_path.write_text(json.dumps(events), encoding="utf-8")

    def rows(self):
        if not self.csv.exists():
            return []
        with self.csv.open(newline="", encoding="utf-8") as handle:
            return list(csv.DictReader(handle))

    def seed_rows(self, statuses):
        with self.csv.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=FIELDNAMES)
            writer.writeheader()
            for index, (location, language, status) in enumerate(statuses):
                writer.writerow({
                    "observation_id": f"old-{index}", "day": 1, "question_id": "Q01", "collector": "browser",
                    "provider": "chatgpt", "language": language, "vpn_location": location, "status": status,
                    "response": "Original answer" if status == "ok" else "", "prompt": "Original prompt",
                })

    def snapshot(self):
        return {str(path.relative_to(self.root)): path.read_bytes() for path in self.root.rglob("*") if path.is_file()}

    def retry(self):
        return manual_retry.retry_day(1, cfg=self.cfg, study_id="test-study", log_index=False)

    def test_recovers_failed_and_absent_answers_after_skip_and_automatic_attempt_limit(self):
        self.seed_rows([("US", "en", "ok")] + [("US", "he", "captcha")] * 3)
        before = self.rows()
        options = manual_retry.retry_options(cfg=self.cfg)
        self.assertEqual(options[0]["missing"], 3)
        record = self.retry()
        self.assertTrue(record["ok"])
        self.assertEqual(self.collect.call_count, 3)
        self.assertEqual(self.rows()[:len(before)], before)
        self.assertEqual(len(self.rows()), len(before) + 3)
        self.assertEqual(len(ok_keys(self.csv)), 4)
        self.assertEqual(manual_retry.retry_options(cfg=self.cfg), [])
        schedule = json.loads(self.schedule_path.read_text())
        self.assertEqual(schedule[1], self.next_event)
        self.assertEqual(schedule[0]["run_at"], self.event["run_at"])
        self.assertEqual(schedule[0]["manual_recoveries"][0]["missing_after"], 0)
        saved = json.loads(next((self.root / "runs").glob("*.json")).read_text())
        self.assertEqual(saved["trigger"], "manual_recovery")
        self.assertEqual(saved["command"], "retry-day --day 1")
        self.assertEqual(saved["manual_recovery"]["missing_before"], 3)

    def test_pending_automatic_retry_can_be_recovered_now_and_schedules_next_day_once(self):
        self.cfg["experiment"]["recovery_policy"] = "retry"
        retry_at = self.now + timedelta(hours=3)
        pending = {**self.event, "run_at": retry_at.isoformat(), "completed_modes": [], "retry_count": 1}
        self.save_schedule([pending])
        runs = self.root / "runs"
        runs.mkdir()
        (runs / "original.json").write_text(json.dumps({
            "day": 1, "mode": "browser", "run_at": self.event["run_at"],
            "started_at": (self.now - timedelta(hours=1)).isoformat(),
        }))
        self.seed_rows([("US", "en", "ok"), ("US", "he", "timeout")])
        record = self.retry()
        self.assertTrue(record["ok"])
        self.assertEqual(record["run_at"], self.event["run_at"])
        self.assertEqual(record["manual_recovery"]["scheduled_run_at"], retry_at.isoformat())
        schedule = json.loads(self.schedule_path.read_text())
        self.assertEqual(schedule[0]["completed_modes"], ["browser"])
        self.assertNotIn("retry_count", schedule[0])
        self.assertEqual([event["day"] for event in schedule], [1, 2])
        self.assertGreater(datetime.fromisoformat(schedule[1]["run_at"]).date(), self.now.date())
        self.assertTrue(self.retry()["no_op"])
        self.assertEqual(len(json.loads(self.schedule_path.read_text())), 2)

    def test_another_captcha_preserves_future_schedule_and_can_be_retried_again(self):
        self.collect.return_value = BrowserResult("", None, 1, status="captcha", error="Still blocked")
        record = self.retry()
        self.assertFalse(record["ok"])
        self.assertEqual(manual_retry.retry_options(cfg=self.cfg)[0]["missing"], 4)
        schedule = json.loads(self.schedule_path.read_text())
        self.assertEqual(schedule[1], self.next_event)
        self.assertEqual(schedule[0]["manual_recoveries"][0]["missing_after"], 4)

    def test_repeated_retries_append_times_per_answer_and_match_run_json(self):
        self.seed_rows([("US", "en", "ok"), ("US", "he", "captcha")])
        originals = self.rows()
        times = []
        for index, status in enumerate(("captcha", "timeout", "ok")):
            if index:
                self.now += timedelta(minutes=5)
            times.append(self.now.isoformat())
            self.collect.return_value = BrowserResult("Recovered" if status == "ok" else "", None, 1, status=status)
            self.retry()
            current = self.rows()
            self.assertEqual(current[:len(originals)], originals)
            for row in current[-3:]:
                self.assertEqual(row["retry"], "\n".join(times))
                self.assertEqual(row["timestamp"], times[-1])
            latest_run = sorted((self.root / "runs").glob("*.json"))[-1]
            observations = [observation for location in json.loads(latest_run.read_text())["locations"] for observation in location["browser"]]
            self.assertEqual(len(observations), 3)
            self.assertTrue(all(observation["retry"] == "\n".join(times) for observation in observations))
        self.assertEqual(self.collect.call_count, 9)
        by_cell = {}
        for row in self.rows():
            if row["retry"]:
                by_cell.setdefault((row["language"], row["vpn_location"]), []).append(row["retry"])
        self.assertEqual(len(by_cell), 3)
        for snapshots in by_cell.values():
            self.assertEqual(snapshots, ["\n".join(times[:i]) for i in range(1, 4)])
        before = self.snapshot()
        self.assertTrue(self.retry()["no_op"])
        self.assertEqual(self.snapshot(), before)

    def test_complete_legacy_csv_no_op_does_not_add_retry_column(self):
        old_fields = [field for field in FIELDNAMES if field != "retry"]
        with self.csv.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=old_fields)
            writer.writeheader()
            for location in ("US", "IL"):
                for language in ("en", "he"):
                    writer.writerow({"day": 1, "question_id": "Q01", "collector": "browser", "provider": "chatgpt",
                                     "language": language, "vpn_location": location, "status": "ok"})
        before = self.csv.read_bytes()
        self.assertTrue(self.retry()["no_op"])
        self.assertEqual(self.csv.read_bytes(), before)
        self.assertFalse(self.csv.with_suffix(".xlsx").exists())
        self.session.assert_not_called()

    def test_session_timeout_keeps_pending_day_recoverable_and_releases_lock(self):
        self.save_schedule([{**self.event, "completed_modes": []}, self.next_event])
        self.seed_rows([("US", "en", "ok")])
        before = self.rows()
        self.collect.side_effect = VpnSessionTimeout("Test session time limit")
        record = self.retry()
        self.assertFalse(record["ok"])
        self.assertIn("VpnSessionTimeout", record["error"])
        self.assertEqual(self.rows(), before)
        schedule = json.loads(self.schedule_path.read_text())
        self.assertEqual(schedule[0]["completed_modes"], [])
        self.assertEqual(schedule[1], self.next_event)
        self.assertEqual(manual_retry.retry_options(cfg=self.cfg)[0]["missing"], 3)
        with run_lock.collection_lock():
            pass

    def test_future_or_already_successful_day_never_starts_collection(self):
        self.save_schedule([self.next_event])
        self.assertEqual(manual_retry.retry_options(cfg=self.cfg), [])
        with self.assertRaises(ValueError):
            manual_retry.retry_day(2, cfg=self.cfg, log_index=False)
        self.seed_rows([(location, language, "ok") for location in ("US", "IL") for language in ("en", "he")])
        self.save_schedule([self.event])
        record = self.retry()
        self.assertTrue(record["ok"])
        self.assertTrue(record["no_op"])
        self.session.assert_not_called()
        self.collect.assert_not_called()

    def test_completed_day_no_op_preserves_all_research_artifacts(self):
        self.seed_rows([(location, language, "ok") for location in ("US", "IL") for language in ("en", "he")])
        (self.root / "results.xlsx").write_bytes(b"Existing Excel export")
        (self.root / "README.md").write_text("Existing run index\n")
        runs = self.root / "runs"
        runs.mkdir()
        (runs / "previous.json").write_text(json.dumps({"day": 1, "mode": "browser", "run_at": self.event["run_at"], "started_at": self.now.isoformat()}))
        with run_lock.collection_lock():
            pass
        before = self.snapshot()
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = self.retry()
        self.assertEqual(result, {"ok": True, "no_op": True, "day": 1})
        self.assertIn("already complete. No answers were collected.", output.getvalue())
        self.assertEqual(self.snapshot(), before)
        self.connect.assert_not_called()
        self.session.assert_not_called()
        self.collect.assert_not_called()
        visible = manual_retry.retry_options(cfg=self.cfg, include_complete=True)
        self.assertEqual(len(visible), 1)
        self.assertEqual(visible[0]["missing"], 0)
        self.assertEqual(visible[0]["successful"], visible[0]["total"])

    def test_completed_day_still_checks_the_selected_study(self):
        self.seed_rows([(location, language, "ok") for location in ("US", "IL") for language in ("en", "he")])
        with self.assertRaisesRegex(ValueError, "active study changed"):
            manual_retry.retry_day(1, cfg=self.cfg, study_id="previous-study", log_index=False)
        self.collect.assert_not_called()

    def test_cli_reports_a_completed_day_as_a_successful_no_op(self):
        import main

        with patch.object(manual_retry, "retry_day", return_value={"ok": True, "no_op": True, "day": 1}) as retry, \
                patch("sys.argv", ["main.py", "retry-day", "--day", "1"]):
            with self.assertRaises(SystemExit) as stopped:
                main.main()
        self.assertEqual(stopped.exception.code, 0)
        retry.assert_called_once_with(1, study_id=None)

    def test_busy_collector_refuses_retry_without_changes(self):
        with run_lock.collection_lock():
            before = self.snapshot()
            with self.assertRaises(run_lock.CollectionBusy):
                self.retry()
            self.assertEqual(self.snapshot(), before)
        self.session.assert_not_called()

    def test_active_study_mismatch_and_unconfigured_day_are_refused(self):
        with self.assertRaisesRegex(ValueError, "active study changed"):
            manual_retry.retry_day(1, cfg=self.cfg, study_id="previous-study", log_index=False)
        self.cfg["experiment"]["days"] = 0
        self.assertEqual(manual_retry.retry_options(cfg=self.cfg), [])
        with self.assertRaises(ValueError):
            self.retry()
        self.collect.assert_not_called()

    def test_missing_schedule_is_not_created_by_read_or_retry(self):
        self.schedule_path.unlink()
        with self.assertRaises(ValueError):
            manual_retry.retry_options(cfg=self.cfg)
        with self.assertRaises(ValueError):
            self.retry()
        self.assertFalse(self.schedule_path.exists())
        self.session.assert_not_called()

    def test_malformed_csv_is_rejected_before_any_collection_or_overwrite(self):
        self.csv.write_text("unexpected,header\nvalue,value\n")
        before = self.csv.read_bytes()
        with self.assertRaisesRegex(ValueError, "missing columns"):
            self.retry()
        self.assertEqual(self.csv.read_bytes(), before)
        self.session.assert_not_called()

    def test_options_are_read_only_and_ignore_an_api_only_attempt_before_browser_due(self):
        self.save_schedule([self.next_event])
        runs = self.root / "runs"
        runs.mkdir()
        (runs / "api.json").write_text(json.dumps({"day": 2, "mode": "api", "run_at": self.event["run_at"], "started_at": self.now.isoformat()}))
        before = self.snapshot()
        self.assertEqual(manual_retry.retry_options(cfg=self.cfg), [])
        self.assertEqual(self.snapshot(), before)
        self.assertFalse((self.root / "collection.lock").exists())

    def test_cli_invokes_recovery_with_the_selected_study_and_reports_gaps(self):
        import main

        with patch.object(manual_retry, "retry_day", return_value={"ok": False}) as retry, \
                patch("sys.argv", ["main.py", "retry-day", "--day", "1", "--study-id", "test-study"]):
            with self.assertRaises(SystemExit) as stopped:
                main.main()
        self.assertEqual(stopped.exception.code, 1)
        retry.assert_called_once_with(1, study_id="test-study")

    def test_cli_reports_a_busy_collector_without_a_traceback_or_collection(self):
        import main

        output = io.StringIO()
        with patch.object(manual_retry, "retry_day", side_effect=run_lock.CollectionBusy("Collection already running")), \
                patch("sys.argv", ["main.py", "retry-day", "--day", "1"]), contextlib.redirect_stderr(output):
            with self.assertRaises(SystemExit) as stopped:
                main.main()
        self.assertEqual(stopped.exception.code, 1)
        self.assertIn("Collection already running", output.getvalue())
        self.assertNotIn("Traceback", output.getvalue())


if __name__ == "__main__":
    unittest.main()
