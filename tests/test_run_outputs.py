import copy
import csv
from datetime import datetime, timedelta
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

from experiment import runner
from experiment.api_collectors import ApiResult
from experiment.browser_collectors import BrowserResult
from experiment.vpn import VpnState


class RunOutputTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.cfg = copy.deepcopy(runner.load_config())
        for key, name in (("csv_path", "results.csv"), ("runs_dir", "runs"),
                          ("checks_dir", "checks"), ("index_path", "README.md"),
                          ("details_path", "results.md"), ("failure_screenshot_dir", "failures"),
                          ("schedule_path", "schedule.json")):
            self.cfg["results"][key] = str(self.root / name)
        question = {"day": 1, "question_id": "Q01", "en": "English", "he": "עברית"}
        def vpn(cfg, location, loc_cfg):
            return VpnState(location, location, "192.0.2.1", location, location, True, {})
        for name, value in (("load_config", lambda: self.cfg),
                            ("apply_active_study", lambda: self.cfg),
                            ("_question_for_day", lambda day: question),
                            ("load_schedule", lambda *a, **k: []), ("connect_vpn", vpn),
                            ("disconnect_vpn", lambda cfg: None)):
            mocked = patch.object(runner, name, value)
            mocked.start()
            self.addCleanup(mocked.stop)
        mocked = patch.object(runner, "collect_browser", return_value=BrowserResult(
            "Answer עברית", None, 25, manual_intervention=True, attempts=2))
        self.browser = mocked.start()
        self.addCleanup(mocked.stop)
        mocked = patch.dict(runner.API_ADAPTERS, {"anthropic": lambda prompt, cfg: ApiResult("API answer", "test", 10)})
        mocked.start()
        self.addCleanup(mocked.stop)

    def records(self):
        return [json.loads(p.read_text()) for p in sorted((self.root / "runs").glob("*.json"))]

    def rows(self):
        with (self.root / "results.csv").open() as stream:
            return list(csv.DictReader(stream))

    def test_both_outputs_match_and_index_links_to_both(self):
        runner.run_day(1, "both")
        record = self.records()[0]
        self.assertTrue(record["ok"])
        self.assertTrue(record["finished_at"] >= record["started_at"])
        rows = {r["observation_id"]: r for r in self.rows()}
        self.assertEqual(len(rows), 16)
        for location in record["locations"]:
            self.assertTrue(location["vpn"]["verified"])
            self.assertNotIn("raw", location["vpn"])
            for entry in location["api"] + location["browser"]:
                row = rows.pop(entry["observation_id"])
                for field in ("prompt", "response", "timestamp", "language", "provider", "status"):
                    self.assertEqual(row[field], entry[field])
                if entry in location["browser"]:
                    self.assertEqual(row["manual_intervention"], "True")
                    self.assertEqual(row["attempts"], "2")
                else:
                    self.assertEqual(row["manual_intervention"], "False")
            self.assertTrue(all(e["manual_intervention"] and e["attempts"] == 2 for e in location["browser"]))
        self.assertFalse(rows)
        index = (self.root / "README.md").read_text()
        self.assertIn("results.csv", index)
        self.assertIn("_day-1_both.json", index)
        self.assertIn("run-day --day 1 --mode both", (self.root / "results.md").read_text())

    def test_rerun_writes_separate_json_without_duplicate_csv_rows(self):
        runner.run_day(1, "browser")
        runner.run_day(1, "browser")
        self.assertEqual(len(self.rows()), 12)
        self.assertEqual(len(self.records()), 2)
        latest = self.records()[-1]
        self.assertEqual(sum(len(e["skipped"]) for e in latest["locations"]), 12)
        self.assertEqual(sum(len(e["browser"]) for e in latest["locations"]), 0)
        self.assertEqual(self.browser.call_count, 12)

    def test_exception_saves_partial_answers_and_error(self):
        self.browser.side_effect = [BrowserResult("First answer", None, 1), RuntimeError("Browser stopped")]
        with self.assertRaisesRegex(RuntimeError, "Browser stopped"):
            runner.run_day(1, "browser")
        record = self.records()[0]
        self.assertFalse(record["ok"])
        self.assertIn("Browser stopped", record["error"])
        self.assertEqual(len(self.rows()), 1)
        self.assertEqual(record["locations"][0]["browser"][0]["response"], "First answer")

    def test_google_is_added_to_existing_day_without_reasking_other_providers(self):
        self.cfg["browser"]["providers"]["google_search"]["enabled"] = False
        runner.run_day(1, "browser")
        self.assertEqual(len(self.rows()), 8)
        self.cfg["browser"]["providers"]["google_search"]["enabled"] = True
        self.browser.reset_mock()
        record = runner.run_day(1, "browser")
        self.assertTrue(record["ok"])
        self.assertEqual(len(self.rows()), 12)
        self.assertEqual(sum(len(e["skipped"]) for e in record["locations"]), 8)
        self.assertEqual(self.browser.call_count, 4)
        self.assertTrue(all(c.kwargs["provider"] == "google_search" for c in self.browser.call_args_list))

    def test_missing_google_overview_is_saved_without_substituting_search_results(self):
        metadata = {"surface": "ai_overview", "search_url": "https://www.google.com/search?q=test",
                    "sources": [], "overview_found": False}
        def collect(**kwargs):
            if kwargs["provider"] == "google_search":
                return BrowserResult("", None, 1, status="no_ai_overview", error="No overview", metadata=metadata)
            return BrowserResult("Chat answer", None, 1)
        self.browser.side_effect = collect
        record = runner.run_day(1, "browser")
        self.assertFalse(record["ok"])
        google_rows = [r for r in self.rows() if r["provider"] == "google_search"]
        self.assertEqual(len(google_rows), 4)
        self.assertTrue(all(r["response"] == "" and r["status"] == "no_ai_overview" for r in google_rows))
        google_json = [o for e in self.records()[0]["locations"] for o in e["browser"] if o["provider"] == "google_search"]
        self.assertTrue(all(o["metadata"] == metadata for o in google_json))

    def test_google_success_preserves_citations_in_run_json_and_answer_in_csv(self):
        metadata = {"surface": "ai_overview", "sources": [
            {"title": "Wikipedia", "url": "https://en.wikipedia.org/wiki/Fourth_Geneva_Convention"},
        ]}
        self.browser.return_value = BrowserResult("Overview answer", None, 10, metadata=metadata)
        runner.run_day(1, "browser")
        google_json = [o for e in self.records()[0]["locations"] for o in e["browser"] if o["provider"] == "google_search"]
        self.assertEqual(len(google_json), 4)
        self.assertTrue(all(o["metadata"] == metadata for o in google_json))
        self.assertTrue(all(r["response"] == "Overview answer" for r in self.rows()))
        self.assertTrue(all(r["source_urls"] == metadata["sources"][0]["url"] for r in self.rows()))

    def test_browser_links_are_saved_in_csv_and_json_without_altering_answer(self):
        sources = [{"title": "Wikipedia", "url": "https://example.org/wiki"},
                   {"title": "Source", "url": "https://example.org/source"}]
        self.browser.return_value = BrowserResult("Verbatim answer, עברית\nSecond line", None, 1, sources=sources)
        record = runner.run_day(1, "browser")
        for location in record["locations"]:
            for entry in location["browser"]:
                self.assertEqual(entry["sources"], sources)
        self.assertTrue(all(row["source_urls"] == "https://example.org/wiki\nhttps://example.org/source" for row in self.rows()))
        self.assertTrue(all(row["response"] == "Verbatim answer, עברית\nSecond line" for row in self.rows()))

    def test_failed_answer_and_scheduled_time_are_recorded(self):
        self.browser.return_value = BrowserResult("Partial", None, 1, status="timeout", error="Timed out")
        event = {"day": 1, "run_at": "2026-10-19T16:16:00-07:00", "location_order": ["US", "IL"]}
        with patch.object(runner, "load_schedule", return_value=[event]):
            runner.run_day(1, "browser")
        record = self.records()[0]
        self.assertFalse(record["ok"])
        self.assertEqual(record["run_at"], event["run_at"])
        self.assertEqual(record["locations"][0]["browser"][0]["status"], "timeout")

    def test_scheduled_pipeline_writes_outputs_marks_done_and_draws_next_day(self):
        now = datetime(2026, 10, 19, 17, tzinfo=ZoneInfo("America/Los_Angeles"))
        self.cfg["experiment"]["days"] = 2
        response = 'Smoke, "quoted" | עברית\nSecond line'
        self.browser.return_value = BrowserResult(response, None, 25)
        event = {"day": 1, "run_at": (now - timedelta(minutes=1)).isoformat(),
                 "location_order": ["IL", "US"], "completed_modes": []}
        schedule_path = self.root / "schedule.json"
        schedule_path.write_text(json.dumps([event]))
        def load_schedule(path=None, *, cfg=None):
            return json.loads(schedule_path.read_text())
        with patch.object(runner, "ROOT", self.root), patch.object(runner, "load_schedule", side_effect=load_schedule), patch.object(runner, "_now", return_value=now), patch.object(runner, "apply_active_study", lambda: self.cfg):
            runner.run_due("browser", cfg=self.cfg, schedule_path=schedule_path)
            runner.run_due("browser", cfg=self.cfg, schedule_path=schedule_path)
        schedule = load_schedule()
        self.assertEqual(schedule[0]["completed_modes"], ["browser"])
        self.assertEqual(len(schedule), 2)
        next_run = datetime.fromisoformat(schedule[1]["run_at"])
        self.assertEqual(next_run.date(), (now + timedelta(days=1)).date())
        self.assertTrue(8 <= next_run.hour <= 21)
        self.assertEqual(len(self.records()), 1)
        record = self.records()[0]
        self.assertTrue(record["ok"])
        self.assertEqual(record["checks"], ["vpn", "browser"])
        self.assertEqual(record["run_at"], event["run_at"])
        self.assertEqual(self.browser.call_count, 12)
        self.assertEqual(len(self.rows()), 12)
        self.assertTrue(all(r["response"] == response for r in self.rows()))
        details = (self.root / "results.md").read_text()
        self.assertIn('Smoke, "quoted" \\| עברית Second line', details)
        json_file = next((self.root / "runs").glob("*.json"))
        self.assertIn(f'id="{json_file.stem}"', details)
        self.assertIn(f"results.md#{json_file.stem}", (self.root / "README.md").read_text())


if __name__ == "__main__":
    unittest.main()
