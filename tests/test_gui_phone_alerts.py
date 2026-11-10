"""Settings IPC and explicit test delivery without a desktop or network access."""
from __future__ import annotations

import contextlib
import io
import json
import queue
import subprocess
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from experiment import gui_phone_alerts, gui_support, gui_worker


SETTINGS = {
    "enabled": True, "macos_notification": True, "provider": "ntfy",
    "ntfy_topic": "example-test-topic", "ntfy_server": "https://ntfy.example",
    "telegram_bot_token": "", "telegram_chat_id": "",
    "advance_notice_minutes": 10,
}


class SettingsBridgeTests(unittest.TestCase):
    def test_private_settings_are_sent_via_stdin_in_project_environment(self):
        response = SimpleNamespace(returncode=0, stdout=json.dumps({"ok": True, "settings": SETTINGS}))
        with patch.object(gui_support, "_cli_python", return_value="/project/.venv/bin/python"), \
                patch.object(gui_support.subprocess, "run", return_value=response) as run:
            received = gui_support.alert_settings_request("save", SETTINGS)
        command = run.call_args.args[0]
        self.assertEqual(command, ["/project/.venv/bin/python", "-u", "-m", "experiment.gui_worker", "--gui-alerts", "save"])
        self.assertNotIn(SETTINGS["ntfy_topic"], " ".join(command))
        self.assertEqual(json.loads(run.call_args.kwargs["input"]), SETTINGS)
        self.assertEqual(received["settings"], SETTINGS)

    def test_invalid_helper_response_does_not_leak_its_output(self):
        for response in (
            SimpleNamespace(returncode=1, stdout="", stderr="private-topic-in-traceback"),
            SimpleNamespace(returncode=0, stdout="private-topic-in-invalid-json"),
        ):
            with self.subTest(response=response), patch.object(gui_support.subprocess, "run", return_value=response):
                with self.assertRaises(RuntimeError) as raised:
                    gui_support.alert_settings_request("get")
                self.assertNotIn("private-topic", str(raised.exception))

    def test_timeout_is_reported_without_the_private_input(self):
        with patch.object(gui_support.subprocess, "run", side_effect=subprocess.TimeoutExpired(["helper"], 20)):
            with self.assertRaisesRegex(RuntimeError, "did not respond in time"):
                gui_support.alert_settings_request("save", SETTINGS)

    def test_incomplete_success_response_is_reported_instead_of_crashing_dialog(self):
        for action, payload in (("get", {"ok": True}), ("save", {"ok": True, "settings": {}}), ("test", {"ok": True})):
            response = SimpleNamespace(returncode=0, stdout=json.dumps(payload))
            with self.subTest(action=action), patch.object(gui_support.subprocess, "run", return_value=response):
                with self.assertRaisesRegex(RuntimeError, "Could not read"):
                    gui_support.alert_settings_request(action, SETTINGS)

    def test_save_worker_never_enters_collection_or_sends_a_notification(self):
        output = io.StringIO()
        with patch("experiment.alert_settings.save_settings", return_value=SETTINGS) as save, \
                patch("experiment.alert_settings.test_phone_alert") as send, \
                patch.object(gui_worker, "_invoke_cli") as invoke, \
                patch.object(gui_worker.sys, "stdin", io.StringIO(json.dumps(SETTINGS))), \
                contextlib.redirect_stdout(output):
            gui_worker.main(["--gui-alerts", "save"])
        save.assert_called_once_with(SETTINGS)
        send.assert_not_called()
        invoke.assert_not_called()
        self.assertTrue(json.loads(output.getvalue())["ok"])

    def test_bad_json_is_rejected_without_echoing_it(self):
        output = io.StringIO()
        with patch.object(gui_worker.sys, "stdin", io.StringIO("bad-private-topic")), contextlib.redirect_stdout(output):
            gui_worker.main(["--gui-alerts", "save"])
        self.assertFalse(json.loads(output.getvalue())["ok"])
        self.assertNotIn("bad-private-topic", output.getvalue())


class DialogOperationTests(unittest.TestCase):
    def run_operation(self, *, send_test=False, fail_test=False):
        calls = []

        def request(action, values):
            calls.append(action)
            if action == "test" and fail_test:
                raise RuntimeError("The phone service could not be reached.")
            return {"settings": SETTINGS, "message": "Phone test sent."}

        dialog = SimpleNamespace(busy=False, events=queue.Queue(), update_controls=Mock(), status=Mock())
        events = []
        with patch.object(gui_phone_alerts, "alert_settings_request", side_effect=request):
            gui_phone_alerts.PhoneAlertsDialog.begin(dialog, "save", SETTINGS, send_test)
            while not events or events[-1][0] != "idle":
                events.append(dialog.events.get(timeout=3))
        return calls, events

    def test_save_only_does_not_send_a_test(self):
        calls, events = self.run_operation()
        self.assertEqual(calls, ["save"])
        self.assertIn(("saved", SETTINGS), events)

    def test_test_button_saves_then_sends_only_when_explicitly_requested(self):
        calls, events = self.run_operation(send_test=True)
        self.assertEqual(calls, ["save", "test"])
        self.assertIn(("tested", "Phone test sent."), events)

    def test_test_failure_distinguishes_saved_preferences_from_delivery_failure(self):
        calls, events = self.run_operation(send_test=True, fail_test=True)
        self.assertEqual(calls, ["save", "test"])
        self.assertIn(("saved", SETTINGS), events)
        self.assertTrue(any(event == "error" and message.startswith("Settings saved, but") for event, message in events))


if __name__ == "__main__":
    unittest.main()
