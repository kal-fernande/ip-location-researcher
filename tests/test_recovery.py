import copy
import csv
import json
from datetime import datetime, timedelta
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

from experiment import runner
from experiment.browser_collectors import BrowserResult
from experiment.storage import skip_keys
from experiment.vpn import VpnState


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.cfg = copy.deepcopy(runner.load_config())
        self.cfg["experiment"]["days"] = 2
        self.cfg["experiment"]["recovery_policy"] = "retry"
        self.cfg["experiment"]["recovery"] = {
            "max_attempts_per_cell": 3,
            "retry_window": {"start_hour": 8, "end_hour": 21},
        }
        self.cfg["results"]["csv_path"] = str(self.root / "results.csv")
        self.cfg["results"]["runs_dir"] = str(self.root / "runs")
        self.cfg["results"]["failure_screenshot_dir"] = str(self.root / "failures")
        self.cfg["results"]["index_path"] = str(self.root / "README.md")
        self.cfg["results"]["details_path"] = str(self.root / "results.md")
        self.cfg["results"]["checks_dir"] = str(self.root / "checks")
        self.schedule_path = self.root / "schedule.json"
        self.now = datetime(2026, 10, 19, 12, tzinfo=ZoneInfo("America/Los_Angeles"))
        question = {"day": 1, "question_id": "Q01", "en": "English", "he": "עברית"}

        def vpn(cfg, location, loc_cfg):
            return VpnState(location, location, "192.0.2.1", location, location, True, {})

        for name, value in (
            ("load_config", lambda: copy.deepcopy(self.cfg)),
            ("_question_for_day", lambda day: question),
            ("_now", lambda cfg: self.now),
            ("connect_vpn", vpn),
            ("disconnect_vpn", lambda cfg: None),
            ("default_schedule_path", lambda: self.schedule_path),
        ):
            mocked = patch.object(runner, name, value)
            mocked.start()
            self.addCleanup(mocked.stop)

    def _write_due_schedule(self):
        event = {
            "day": 1,
            "run_at": (self.now - timedelta(minutes=1)).isoformat(),
            "location_order": ["US", "IL"],
            "completed_modes": [],
        }
        self.schedule_path.write_text(json.dumps([event]), encoding="utf-8")

    def test_skip_keys_retry_allows_rerun_of_captcha(self):
        csv_path = Path(self.cfg["results"]["csv_path"])
        csv_path.parent.mkdir(parents=True, exist_ok=True)
        with csv_path.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=["day", "question_id", "collector", "provider",
                                              "language", "vpn_location", "status"])
            w.writeheader()
            w.writerow({"day": "1", "question_id": "Q01", "collector": "browser",
                        "provider": "chatgpt", "language": "en", "vpn_location": "US",
                        "status": "captcha"})
        key = ("1", "Q01", "browser", "chatgpt", "en", "US")
        self.assertNotIn(key, skip_keys(csv_path, recovery_policy="retry", max_attempts_per_cell=3))
        self.assertIn(key, skip_keys(csv_path, recovery_policy="skip", max_attempts_per_cell=3))

    def test_retry_does_not_complete_day_and_redraws_run_at(self):
        self._write_due_schedule()
        with patch.object(runner, "collect_browser",
                          return_value=BrowserResult("", None, 1, status="captcha", error="CAPTCHA")):
            schedule = runner.run_due("browser", cfg=self.cfg, schedule_path=self.schedule_path,
                                      log_index=False, recovery="retry")
        self.assertEqual(schedule[0]["completed_modes"], [])
        self.assertEqual(len(schedule), 1)
        self.assertGreaterEqual(schedule[0]["retry_count"], 1)
        retry_at = datetime.fromisoformat(schedule[0]["run_at"])
        self.assertGreater(retry_at, self.now)

    def test_retry_completes_when_all_ok_and_draws_next_day(self):
        self._write_due_schedule()
        with patch.object(runner, "collect_browser",
                          return_value=BrowserResult("answer", None, 1)):
            schedule = runner.run_due("browser", cfg=self.cfg, schedule_path=self.schedule_path,
                                      log_index=False, recovery="retry")
        self.assertEqual(schedule[0]["completed_modes"], ["browser"])
        self.assertEqual(len(schedule), 2)
        self.assertEqual(schedule[1]["day"], 2)

    def test_agenda_loads_fixed_times(self):
        agenda = self.root / "agenda.json"
        agenda.write_text(json.dumps([
            {"day": 1, "run_at": "2026-10-20T16:16:00-07:00", "location_order": ["IL", "US"]},
            {"day": 2, "run_at": "2026-10-21T10:00:00-07:00", "location_order": ["US", "IL"]},
        ]), encoding="utf-8")
        self.cfg["experiment"]["schedule_policy"] = "agenda"
        self.cfg["experiment"]["agenda_path"] = str(agenda)
        schedule = runner.create_schedule(cfg=self.cfg, schedule_path=self.schedule_path,
                                          agenda_path=agenda)
        self.assertEqual(len(schedule), 2)
        self.assertEqual(schedule[0]["run_at"], "2026-10-20T16:16:00-07:00")
        self.assertEqual(schedule[0]["location_order"], ["IL", "US"])


class AlertTests(unittest.TestCase):
    def setUp(self):
        from experiment import alert_settings, alerts

        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        for module in (alert_settings, alerts):
            mocked = patch.object(module, "ROOT", root)
            mocked.start()
            self.addCleanup(mocked.stop)

    def test_disabled_alerts_only_print(self):
        from experiment import alerts
        cfg = {"experiment": {"alerts": {"enabled": False}}}
        with patch.object(alerts, "_send_ntfy") as send, \
             patch.object(alerts, "_send_macos") as mac:
            alerts.notify_human_action("chatgpt", "solve me", cfg=cfg)
            send.assert_not_called()
            mac.assert_not_called()

    def test_macos_and_ntfy_both_send_when_enabled(self):
        from experiment import alerts
        cfg = {"experiment": {"alerts": {
            "enabled": True,
            "macos_notification": True,
            "provider": "ntfy",
            "ntfy_topic": "test-topic",
            "ntfy_server": "https://ntfy.sh",
        }}}
        with patch.object(alerts, "_send_ntfy") as send, \
             patch.object(alerts, "_send_macos") as mac:
            alerts.notify_human_action("chatgpt", "solve me", cfg=cfg)
            send.assert_called_once()
            mac.assert_called_once()

    def test_macos_only_when_topic_empty(self):
        from experiment import alerts
        cfg = {"experiment": {"alerts": {
            "enabled": True,
            "macos_notification": True,
            "provider": "ntfy",
            "ntfy_topic": "",
        }}}
        with patch.object(alerts, "_send_ntfy") as send, \
             patch.object(alerts, "_send_macos") as mac:
            alerts.notify_human_action("chatgpt", "solve me", cfg=cfg)
            send.assert_not_called()
            mac.assert_called_once()

    def test_telegram_sends_when_configured(self):
        from experiment import alerts
        cfg = {"experiment": {"alerts": {
            "enabled": True,
            "macos_notification": False,
            "provider": "telegram",
            "telegram_bot_token": "123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw",
            "telegram_chat_id": "987654321",
        }}}
        with patch.object(alerts, "_send_telegram") as send, \
             patch.object(alerts, "_send_ntfy") as ntfy, \
             patch.object(alerts, "_send_macos") as mac:
            alerts.notify_human_action("chatgpt", "solve me", cfg=cfg)
            send.assert_called_once()
            ntfy.assert_not_called()
            mac.assert_not_called()


class AdvanceNoticeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.cfg = copy.deepcopy(runner.load_config())
        self.cfg["experiment"]["days"] = 2
        self.cfg["experiment"]["alerts"] = {
            "enabled": True,
            "provider": "ntfy",
            "ntfy_topic": "test-topic",
            "ntfy_server": "https://ntfy.sh",
            "advance_notice_minutes": 10,
        }
        self.cfg["results"]["csv_path"] = str(self.root / "results.csv")
        self.cfg["results"]["runs_dir"] = str(self.root / "runs")
        self.cfg["results"]["failure_screenshot_dir"] = str(self.root / "failures")
        self.cfg["results"]["index_path"] = str(self.root / "README.md")
        self.cfg["results"]["details_path"] = str(self.root / "results.md")
        self.cfg["results"]["checks_dir"] = str(self.root / "checks")
        self.schedule_path = self.root / "schedule.json"
        self.now = datetime(2026, 10, 19, 12, tzinfo=ZoneInfo("America/Los_Angeles"))
        for name, value in (
            ("load_config", lambda: copy.deepcopy(self.cfg)),
            ("_now", lambda cfg: self.now),
            ("apply_active_study", lambda cfg=None: copy.deepcopy(self.cfg)),
        ):
            mocked = patch.object(runner, name, value)
            mocked.start()
            self.addCleanup(mocked.stop)

    def test_advance_notice_once_inside_window(self):
        from experiment import alerts
        run_at = (self.now + timedelta(minutes=8)).isoformat()
        self.schedule_path.write_text(json.dumps([{
            "day": 1,
            "run_at": run_at,
            "location_order": ["US", "IL"],
            "completed_modes": [],
        }]), encoding="utf-8")
        with patch.object(alerts, "_send_ntfy") as send, \
             patch.object(runner, "run_day") as run_day:
            schedule = runner.run_due(
                "browser", cfg=self.cfg, schedule_path=self.schedule_path, log_index=False,
            )
            schedule = runner.run_due(
                "browser", cfg=self.cfg, schedule_path=self.schedule_path, log_index=False,
            )
        send.assert_called_once()
        run_day.assert_not_called()
        self.assertEqual(schedule[0]["advance_notice_sent_for"], run_at)

    def test_no_advance_notice_outside_window(self):
        from experiment import alerts
        run_at = (self.now + timedelta(minutes=30)).isoformat()
        self.schedule_path.write_text(json.dumps([{
            "day": 1,
            "run_at": run_at,
            "location_order": ["US", "IL"],
            "completed_modes": [],
        }]), encoding="utf-8")
        with patch.object(alerts, "_send_ntfy") as send, \
             patch.object(runner, "run_day"):
            runner.run_due(
                "browser", cfg=self.cfg, schedule_path=self.schedule_path, log_index=False,
            )
        send.assert_not_called()


if __name__ == "__main__":
    unittest.main()
