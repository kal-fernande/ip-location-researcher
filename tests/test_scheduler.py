import copy
from datetime import datetime, timedelta
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

from experiment import runner


class SchedulerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.cfg = copy.deepcopy(runner.load_config())
        self.cfg["experiment"]["alerts"] = {"enabled": False, "advance_notice_minutes": 0}
        self.cfg["results"]["schedule_path"] = str(self.root / "schedule.json")
        self.cfg["results"]["csv_path"] = str(self.root / "results.csv")
        self.now = datetime(2026, 10, 19, 22, tzinfo=ZoneInfo("America/Los_Angeles"))
        for name, value in (("ROOT", self.root), ("load_config", lambda: self.cfg),
                            ("apply_active_study", lambda: self.cfg),
                            ("_now", lambda cfg: self.now)):
            mock = patch.object(runner, name, value)
            mock.start()
            self.addCleanup(mock.stop)
        run_patch = patch.object(runner, "run_day")
        self.run = run_patch.start()
        self.addCleanup(run_patch.stop)

    def start(self):
        return runner.create_schedule("2026-10-19", seed=354)

    def test_creates_only_first_day_and_does_not_redraw_while_waiting(self):
        events = self.start()
        self.assertEqual(len(events), 1)
        self.now = datetime.fromisoformat(events[0]["run_at"]) - timedelta(minutes=1)
        runner.run_due("browser")
        self.run.assert_not_called()
        self.assertEqual(runner.load_schedule(), events)

    def test_completion_draws_tomorrow_once_and_preserves_history(self):
        first = self.start()[0]
        runner.run_due("browser")
        runner.run_due("browser")
        self.run.assert_called_once()
        self.assertEqual(self.run.call_args.args, (1,))
        self.assertEqual(self.run.call_args.kwargs["mode"], "browser")
        events = runner.load_schedule()
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]["run_at"], first["run_at"])
        self.assertEqual(events[0]["completed_modes"], ["browser"])
        at = datetime.fromisoformat(events[1]["run_at"])
        self.assertEqual(at.date(), (self.now + timedelta(days=1)).date())
        self.assertTrue(8 <= at.hour <= 21)
        self.assertEqual(sorted(events[1]["location_order"]), sorted(self.cfg["locations"]))

    def test_advance_notice_is_sent_once_without_redrawing_or_collecting(self):
        events = self.start()
        self.cfg["experiment"]["alerts"] = {"enabled": True, "advance_notice_minutes": 10}
        self.now = datetime.fromisoformat(events[0]["run_at"]) - timedelta(minutes=1)
        with patch("experiment.alerts.notify_upcoming_run") as notify:
            runner.run_due("browser")
            runner.run_due("browser")
        notify.assert_called_once()
        self.run.assert_not_called()
        saved = runner.load_schedule()
        self.assertEqual(saved[0]["run_at"], events[0]["run_at"])
        self.assertEqual(saved[0]["advance_notice_sent_for"], events[0]["run_at"])
        self.assertEqual(len(saved), 1)

    def test_study_length_can_grow_and_shrink_without_regenerating(self):
        self.cfg["experiment"]["days"] = 1
        self.start()
        runner.run_due("browser")
        self.assertEqual(len(runner.load_schedule()), 1)
        self.cfg["experiment"]["days"] = 2
        runner.run_due("browser")
        self.assertEqual(len(runner.load_schedule()), 2)
        self.cfg["experiment"]["days"] = 1
        self.now += timedelta(days=3)
        runner.run_due("browser")
        self.run.assert_called_once()
        self.assertEqual(self.run.call_args.args, (1,))
        self.assertEqual(self.run.call_args.kwargs["mode"], "browser")

    def test_downtime_does_not_batch_missed_days(self):
        self.start()
        self.now += timedelta(days=5)
        runner.run_due("browser")
        self.run.assert_called_once()
        self.assertEqual(self.run.call_args.args, (1,))
        self.assertEqual(self.run.call_args.kwargs["mode"], "browser")
        next_at = datetime.fromisoformat(runner.load_schedule()[1]["run_at"])
        self.assertEqual(next_at.date(), (self.now + timedelta(days=1)).date())

    def test_aborted_run_retries_same_day_without_drawing_next(self):
        events = self.start()
        self.run.side_effect = RuntimeError("VPN unavailable")
        with self.assertRaises(RuntimeError):
            runner.run_due("browser")
        self.assertEqual(runner.load_schedule(), events)
        self.run.side_effect = None
        runner.run_due("browser")
        self.assertEqual(len(runner.load_schedule()), 2)

    def test_recovers_if_process_stops_between_completion_and_next_draw(self):
        events = self.start()
        events[0]["completed_modes"] = ["browser"]
        runner.save_schedule(events)
        runner.run_due("browser")
        self.run.assert_not_called()
        self.assertEqual(runner.load_schedule()[1]["day"], 2)

    def test_zero_days_and_invalid_window(self):
        self.cfg["experiment"]["days"] = 0
        self.assertEqual(self.start(), [])
        runner.run_due("browser")
        self.run.assert_not_called()
        self.cfg["experiment"]["days"] = 1
        self.cfg["experiment"]["schedule_window"]["end_hour"] = 24
        with self.assertRaises(ValueError):
            self.start()

    def test_extra_days_cycle_questions_without_editing_question_list(self):
        questions = runner.load_questions()
        self.assertEqual(runner._question_for_day(len(questions) + 1), questions[0])
        self.assertEqual(runner._question_for_day(1), questions[0])


if __name__ == "__main__":
    unittest.main()
