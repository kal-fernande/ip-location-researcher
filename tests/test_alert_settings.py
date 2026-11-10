"""Private phone configuration without real notifications or project changes."""
from __future__ import annotations

import copy
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from experiment import alert_settings, alerts, config


class AlertSettingsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.path = self.root / "private/phone-alerts.json"
        for module in (alert_settings, alerts, config):
            mocked = patch.object(module, "ROOT", self.root)
            mocked.start()
            self.addCleanup(mocked.stop)
        mocked = patch.dict(os.environ, {"NTFY_TOPIC": ""})
        mocked.start()
        self.addCleanup(mocked.stop)
        self.base = {
            "experiment": {"alerts": {
                "enabled": True, "macos_notification": True, "provider": "none",
                "ntfy_topic": "shared-topic", "ntfy_server": "https://ntfy.sh",
                "advance_notice_minutes": 10, "webhook_url": "https://example.invalid/hook",
            }},
        }
        self.values = {
            "enabled": True, "macos_notification": False, "provider": "ntfy",
            "ntfy_topic": "private-topic", "ntfy_server": "https://phone.example.invalid",
            "telegram_bot_token": "", "telegram_chat_id": "",
            "advance_notice_minutes": 25,
        }
        self.config_text = (
            "# Keep comments in the shared research configuration.\n"
            "experiment:\n"
            "  alerts:\n"
            "    enabled: true\n"
            "    macos_notification: true\n"
            "    provider: none\n"
            "    ntfy_topic: shared-topic\n"
            "    ntfy_server: https://ntfy.sh\n"
            "    advance_notice_minutes: 10\n"
        )
        (self.root / "config.yaml").write_text(self.config_text, encoding="utf-8")

    def test_private_preferences_override_environment_and_are_visible_to_scheduler(self):
        before = copy.deepcopy(self.base)
        with patch.dict(os.environ, {"NTFY_TOPIC": "inherited-old-topic"}):
            self.assertEqual(alert_settings.effective_alerts(self.base)["ntfy_topic"], "inherited-old-topic")
            alert_settings.save_settings(self.values)
            effective = alert_settings.effective_alerts(self.base)
            loaded = config.load_config()
            self.assertEqual(effective["ntfy_topic"], "private-topic")
            self.assertEqual(loaded["experiment"]["alerts"]["advance_notice_minutes"], 25)
            self.assertEqual(loaded["experiment"]["alerts"]["ntfy_topic"], "private-topic")
            self.assertEqual(alerts._alerts_cfg(self.base)["ntfy_topic"], "private-topic")
            self.assertEqual(alert_settings.get_settings(), self.values)
        self.assertEqual(effective["webhook_url"], self.base["experiment"]["alerts"]["webhook_url"])
        self.assertEqual(self.base, before)

    def test_missing_private_file_leaves_other_channels_and_configuration_intact(self):
        self.assertEqual(alert_settings.effective_alerts(self.base), self.base["experiment"]["alerts"])
        self.assertFalse(self.path.parent.exists())

    def test_full_phone_link_accepts_a_custom_server_prefix(self):
        values = {**self.values, "ntfy_topic": "https://phone.example.invalid/ntfy/study_topic-1/"}
        normalized = alert_settings.normalize_settings(values)
        self.assertEqual(normalized["ntfy_server"], "https://phone.example.invalid/ntfy")
        self.assertEqual(normalized["ntfy_topic"], "study_topic-1")
        self.assertEqual(alert_settings.normalize_settings({**self.values, "advance_notice_minutes": " 0 "})["advance_notice_minutes"], 0)

    def test_invalid_topics_servers_and_field_types_are_rejected_without_echoing_values(self):
        invalid = [
            {"ntfy_topic": "has a space"}, {"ntfy_topic": "bad/path"},
            {"ntfy_topic": "a" * 65}, {"ntfy_topic": "https://phone.example.invalid/"},
            {"ntfy_topic": "https://phone.example.invalid/topic?private=value"},
            {"ntfy_topic": "https://phone.example.invalid/a%2Fb"},
            {"ntfy_server": "ftp://private.example.invalid"},
            {"ntfy_server": "https://private-user:private-pass@phone.example.invalid"},
            {"ntfy_server": "https://phone.example.invalid/#private-fragment"},
            {"ntfy_server": "https://phone.example.invalid:99999"},
            {"ntfy_server": "https://bad host.example.invalid"},
            {"ntfy_topic": 123}, {"ntfy_server": True}, {"enabled": "true"},
            {"macos_notification": 1}, {"provider": "unsupported-private-provider"},
            {"advance_notice_minutes": -1}, {"advance_notice_minutes": 1441},
            {"advance_notice_minutes": True}, {"advance_notice_minutes": "2.5"},
            {"unsupported_private_key": "private-value"},
        ]
        for changes in invalid:
            with self.subTest(changes=changes), self.assertRaises(ValueError) as failure:
                alert_settings.normalize_settings({**self.values, **changes})
            self.assertNotIn("private-pass", str(failure.exception))
            self.assertNotIn("private-value", str(failure.exception))

    def test_enabled_ntfy_requires_topic_but_disabled_alerts_can_clear_it(self):
        with self.assertRaises(ValueError):
            alert_settings.normalize_settings({**self.values, "ntfy_topic": ""})
        self.assertEqual(alert_settings.normalize_settings({**self.values, "enabled": False, "ntfy_topic": ""})["ntfy_topic"], "")

    def test_enabled_telegram_requires_token_and_chat_id(self):
        telegram = {
            **self.values, "provider": "telegram", "ntfy_topic": "",
            "telegram_bot_token": "123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw",
            "telegram_chat_id": "987654321",
        }
        self.assertEqual(alert_settings.normalize_settings(telegram)["provider"], "telegram")
        with self.assertRaises(ValueError):
            alert_settings.normalize_settings({**telegram, "telegram_bot_token": ""})
        with self.assertRaises(ValueError):
            alert_settings.normalize_settings({**telegram, "telegram_chat_id": ""})
        with self.assertRaises(ValueError):
            alert_settings.normalize_settings({**telegram, "telegram_bot_token": "not-a-token"})

    def test_save_is_private_and_does_not_change_shared_config_environment_or_other_files(self):
        env_file = self.root / ".env"
        env_file.write_text("NTFY_TOPIC=old-topic\nAPI_VALUE=fixture-value\n", encoding="utf-8")
        before_environment = dict(os.environ)
        saved = alert_settings.save_settings(self.values)
        self.assertEqual(saved, self.values)
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8")), self.values)
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.path.parent.stat().st_mode), 0o700)
        self.assertEqual((self.root / "config.yaml").read_text(encoding="utf-8"), self.config_text)
        self.assertEqual(env_file.read_text(encoding="utf-8"), "NTFY_TOPIC=old-topic\nAPI_VALUE=fixture-value\n")
        self.assertEqual(dict(os.environ), before_environment)

    def test_atomic_replace_failure_preserves_saved_preferences_and_removes_temporary_file(self):
        alert_settings.save_settings(self.values)
        before = self.path.read_bytes()
        with patch.object(alert_settings.os, "replace", side_effect=PermissionError("private destination denied")):
            with self.assertRaisesRegex(RuntimeError, "could not be saved"):
                alert_settings.save_settings({**self.values, "ntfy_topic": "changed-topic"})
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def test_malformed_private_file_reports_sanitized_error_without_changing_it(self):
        self.path.parent.mkdir()
        for content in ("{private-secret", '["private-secret"]', '{"ntfy_topic":"private/secret"}', '{"unknown":"private-secret"}'):
            with self.subTest(content=content):
                self.path.write_text(content, encoding="utf-8")
                with self.assertRaises(RuntimeError) as failure:
                    alert_settings.effective_alerts(self.base)
                self.assertNotIn("private-secret", str(failure.exception))
                self.assertEqual(self.path.read_text(encoding="utf-8"), content)

    def test_loading_and_saving_do_not_send_notifications(self):
        with patch.object(alerts, "_send_ntfy") as phone, patch.object(alerts, "_send_macos") as mac:
            alert_settings.get_settings()
            alert_settings.save_settings(self.values)
            alert_settings.get_settings()
        phone.assert_not_called()
        mac.assert_not_called()

    def test_explicit_phone_test_uses_unsaved_values_and_sanitizes_delivery_failure(self):
        with patch.object(alerts, "_send_ntfy") as phone:
            result = alert_settings.test_phone_alert(self.values)
        self.assertIn("Check your phone", result)
        self.assertEqual(phone.call_args.args[0], self.values)
        self.assertFalse(self.path.exists())
        with patch.object(alerts, "_send_ntfy", side_effect=OSError("https://private-server/private-topic")):
            with self.assertRaises(RuntimeError) as failure:
                alert_settings.test_phone_alert(self.values)
        self.assertIn("OSError", str(failure.exception))
        self.assertNotIn("private-server", str(failure.exception))
        self.assertNotIn("private-topic", str(failure.exception))
        with patch.object(alerts, "_send_ntfy") as phone:
            with self.assertRaises(ValueError):
                alert_settings.test_phone_alert({**self.values, "enabled": False})
            with self.assertRaises(ValueError):
                alert_settings.test_phone_alert({**self.values, "provider": "none"})
        phone.assert_not_called()

    def test_explicit_telegram_test_uses_telegram_sender(self):
        telegram = {
            **self.values, "provider": "telegram", "ntfy_topic": "",
            "telegram_bot_token": "123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw",
            "telegram_chat_id": "987654321",
        }
        with patch.object(alerts, "_send_telegram") as phone, patch.object(alerts, "_send_ntfy") as ntfy:
            result = alert_settings.test_phone_alert(telegram)
        self.assertIn("Check your phone", result)
        phone.assert_called_once()
        ntfy.assert_not_called()


if __name__ == "__main__":
    unittest.main()
