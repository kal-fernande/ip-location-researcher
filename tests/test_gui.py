"""Form validation and truthful outcome messages without opening desktop windows."""
from __future__ import annotations

import json
import queue
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from experiment import gui, gui_support
from experiment.gui import CHECKS, TOOLS, build_arguments, completion_message


def tool(command):
    return next(action for action in TOOLS if action.args and action.args[0] == command)


class GuiCommandTests(unittest.TestCase):
    def test_new_study_accepts_a_display_name_without_changing_it(self):
        action = tool("start-study")
        name = "Follow-up study א; $(literal)"
        args = build_arguments(action, {"--name": name, "--start-date": "2026-11-10", "--notes": "Separate study"})
        self.assertEqual(args, ["start-study", "--name", name, "--start-date", "2026-11-10", "--notes", "Separate study"])
        self.assertNotIn("--force", args)

    def test_default_check_does_not_send_questions(self):
        self.assertEqual(build_arguments(CHECKS[0], {}), ["check-browser"])

    def test_collection_defaults_to_browser_and_preserves_recovery_choice(self):
        args = build_arguments(tool("run-day"), {"--day": "8"})
        self.assertEqual(args, ["run-day", "--day", "8", "--mode", "browser"])
        args = build_arguments(tool("run-day"), {
            "--day": "8", "--mode": "Browser and API", "--recovery": "Retry missing answers",
        })
        self.assertIn("both", args)
        self.assertEqual(args[-2:], ["--recovery", "retry"])
        self.assertNotIn("--force", args)

    def test_invalid_dates_and_nonpositive_days_are_rejected_before_collection(self):
        for value in ("0", "-1", "a day", "1.2"):
            with self.subTest(day=value), self.assertRaises(ValueError):
                build_arguments(tool("run-day"), {"--day": value})
        for value in ("tomorrow", "2026-02-30"):
            with self.subTest(date=value), self.assertRaises(ValueError):
                build_arguments(tool("start-study"), {"--start-date": value})

    def test_evidence_validation_requires_an_existing_file_and_passes_paths_literally(self):
        action = tool("validate-evidence")
        with self.assertRaises(ValueError):
            build_arguments(action, {})
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "a saved run $(literal).json"
            with self.assertRaises(ValueError):
                build_arguments(action, {"run_json": str(path)})
            path.write_text("{}", encoding="utf-8")
            self.assertEqual(build_arguments(action, {"run_json": str(path)}), ["validate-evidence", str(path)])


class GuiOutcomeTests(unittest.TestCase):
    def test_study_stop_explains_preservation_and_new_study(self):
        message, tone = completion_message(["stop-study"], 0, "Study stopped: pilot_a\n", False)
        self.assertEqual(tone, "ok")
        self.assertIn("Saved results were kept", message)
        self.assertIn("New study", message)
        message, tone = completion_message(["stop-study"], 1, "Study stopped: pilot_a\ncleanup failed", False)
        self.assertEqual(tone, "notice")
        self.assertIn("cleanup needs attention", message)

    def test_successful_day_rerun_is_reported_as_a_clear_no_op(self):
        message, tone = completion_message(["retry-day", "--day", "1"], 0, "Day 1 is already complete. No answers were collected.\n", False)
        self.assertEqual(tone, "ok")
        self.assertIn("already complete", message)
        self.assertIn("No answers were collected", message)

    def test_manual_retry_gaps_explain_that_another_retry_is_available(self):
        message, tone = completion_message(["retry-day", "--day", "1"], 1, "Manual retry finished with gaps: day 1 still has 1 missing or failed browser answers.", False)
        self.assertEqual(tone, "notice")
        self.assertIn("retry that day again", message)

    def test_failed_saved_record_is_reported_even_after_zero_exit(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "run.json"
            path.write_text(json.dumps({"ok": False}), encoding="utf-8")
            message, tone = completion_message(["run-day"], 0, f"Run JSON written to {path}\n", False)
            self.assertEqual(tone, "notice")
            self.assertIn("missing or failed", message)

    def test_unreadable_record_does_not_claim_a_verified_run(self):
        message, tone = completion_message(["run-day"], 0, "Run JSON written to /no/such/run.json\n", False)
        self.assertEqual(tone, "notice")
        self.assertIn("Could not verify", message)

    def test_nothing_due_and_busy_are_not_reported_as_collection_success(self):
        message, tone = completion_message(["run-due"], 0, "", False)
        self.assertIn("No collection", message)
        self.assertEqual(tone, "notice")
        message, tone = completion_message(["run-due"], 0, "SKIP: Collection already running", False)
        self.assertIn("already running", message)
        self.assertEqual(tone, "notice")

    def test_cancelled_task_remains_stopped_even_if_exit_code_is_zero(self):
        message, tone = completion_message(["run-day"], 0, "", True)
        self.assertTrue(message.startswith("Stopped"))
        self.assertEqual(tone, "notice")


class StopStudyButtonTests(unittest.TestCase):
    def app(self, *, running=False, study_id="pilot_a"):
        return SimpleNamespace(
            disconnect_runner=SimpleNamespace(running=False, start=Mock()),
            runner=SimpleNamespace(running=running, stop=Mock()), closing=False,
            refresh=Mock(), root=object(), dashboard={"study_id": study_id, "study_name": "Pilot study"},
            append_output=Mock(), set_notice=Mock(), update_controls=Mock(),
        )

    def test_cancel_keeps_current_and_future_collection_untouched(self):
        app = self.app(running=True)
        with patch.object(gui.messagebox, "askokcancel", return_value=False):
            gui.ExperimentApp.stop_study(app)
        app.runner.stop.assert_not_called()
        app.disconnect_runner.start.assert_not_called()

    def test_confirm_stops_local_task_and_passes_selected_study_to_worker(self):
        app = self.app(running=True)
        with patch.object(gui.messagebox, "askokcancel", return_value=True):
            gui.ExperimentApp.stop_study(app)
        app.runner.stop.assert_called_once()
        app.disconnect_runner.start.assert_called_once_with(["stop-study", "--study-id", "pilot_a"])

    def test_no_active_study_does_not_stop_a_task(self):
        app = self.app(study_id="")
        with patch.object(gui.messagebox, "showinfo") as info:
            gui.ExperimentApp.stop_study(app)
        info.assert_called_once()
        app.disconnect_runner.start.assert_not_called()
        app.runner.stop.assert_not_called()


class DesktopFileTests(unittest.TestCase):
    def test_opens_active_file_literally_and_surfaces_missing_default_app(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "Study results $(literal).csv"
            path.write_text("day,response\n", encoding="utf-8")
            app = SimpleNamespace(refresh=lambda: None, dashboard={"csv_path": path}, desktop_events=queue.Queue())
            called = threading.Event()
            commands = []

            def open_file(command, **kwargs):
                commands.append(command)
                called.set()
                return SimpleNamespace(returncode=1, stderr="No application is set to open this file.")

            with patch.object(gui.sys, "platform", "darwin"), patch.object(gui.subprocess, "run", side_effect=open_file):
                gui.ExperimentApp.open_result(app, "csv_path")
                self.assertTrue(called.wait(2))
                error = app.desktop_events.get(timeout=2)
            self.assertEqual(commands, [["open", str(path)]])
            self.assertIn("No application", error)
            self.assertIn(str(path), error)


class DesktopRuntimeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        mocked = patch.object(gui_support.Path, "home", return_value=self.home)
        mocked.start()
        self.addCleanup(mocked.stop)

    def test_runtime_search_skips_unusable_candidates_without_opening_tk(self):
        candidates = {f"python3.{version}": f"/runtime/python3.{version}" for version in (15, 14, 13, 12)}

        def probe(command, **kwargs):
            if command[0].endswith("3.15"):
                raise gui_support.subprocess.TimeoutExpired(command, 5)
            if command[0].endswith("3.13"):
                raise OSError("Unavailable interpreter")
            return Mock(returncode=0 if command[0].endswith("3.12") else 1)

        with patch.object(gui_support.shutil, "which", side_effect=candidates.get), \
                patch.object(gui_support.subprocess, "run", side_effect=probe) as probes, \
                patch.object(gui.tk, "Tk") as create_window:
            self.assertEqual(gui.find_desktop_python(), "/runtime/python3.12")
        self.assertEqual(len(probes.call_args_list), 4)
        self.assertTrue(all(call.kwargs["timeout"] == 5 for call in probes.call_args_list))
        create_window.assert_not_called()

    def test_gui_module_imports_without_project_dependencies(self):
        # Homebrew python-tk puts _tkinter on the site path, so this probe keeps site.
        # The check is that opening the GUI module does not load collection modules.
        result = gui.subprocess.run(
            [gui.sys.executable, "-c", (
                "import sys; import experiment.gui; "
                "assert 'experiment.config' not in sys.modules; "
                "assert 'experiment.study' not in sys.modules"
            )],
            cwd=str(gui.ROOT), capture_output=True, text=True, timeout=5,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_runtime_search_skips_current_python_even_if_found_repeatedly(self):
        with patch.object(gui_support.shutil, "which", return_value=gui.sys.executable), \
                patch.object(gui_support.subprocess, "run") as probe:
            self.assertIsNone(gui.find_desktop_python())
        probe.assert_not_called()

    def test_runtime_search_finds_an_existing_uv_python_outside_path(self):
        python = self.home / ".local/share/uv/python/cpython-3.13.2-macos-aarch64-none/bin/python3"
        python.parent.mkdir(parents=True)
        python.write_text("fixture", encoding="utf-8")
        with patch.object(gui_support.shutil, "which", return_value=None), \
                patch.object(gui_support.subprocess, "run", return_value=Mock(returncode=0)) as probe:
            self.assertEqual(gui.find_desktop_python(), str(python))
        self.assertEqual(probe.call_args.args[0][0], str(python))

    def test_old_macos_tk_uses_existing_runtime_and_keeps_collection_python(self):
        original_environment = dict(gui.os.environ)
        with patch.object(gui.sys, "platform", "darwin"), \
                patch.object(gui.tk, "TkVersion", 8.5), \
                patch.object(gui.sys, "executable", "/project/.venv/bin/python"), \
                patch.object(gui, "find_desktop_python", return_value="/desktop/python3.13"), \
                patch.object(gui.subprocess, "call", return_value=0) as start, \
                patch.object(gui.tk, "Tk") as create_window:
            gui.launch()
        create_window.assert_not_called()
        self.assertEqual(start.call_args.args[0], ["/desktop/python3.13", "-u", "-m", "experiment.gui"])
        self.assertEqual(start.call_args.kwargs["cwd"], str(gui.ROOT))
        environment = start.call_args.kwargs["env"]
        self.assertEqual(environment["IP_LOCATION_CLI_PYTHON"], "/project/.venv/bin/python")
        self.assertEqual(dict(gui.os.environ), original_environment)
        self.assertIsNot(environment, gui.os.environ)

    def test_missing_tk_cli_fallback_preserves_symlinked_project_python(self):
        import main as cli

        base_python = self.home / "base/bin/python"
        base_python.parent.mkdir(parents=True)
        base_python.write_text("fixture", encoding="utf-8")
        project_python = self.home / ".venv/bin/python"
        project_python.parent.mkdir(parents=True)
        project_python.symlink_to(base_python)
        original_import = __import__

        def without_tk(name, globals=None, locals=None, fromlist=(), level=0):
            if name == "experiment.gui":
                raise ModuleNotFoundError("No module named '_tkinter'", name="_tkinter")
            return original_import(name, globals, locals, fromlist, level)

        with patch("builtins.__import__", side_effect=without_tk), \
                patch.object(cli.sys, "argv", ["main.py", "gui"]), \
                patch.object(cli.sys, "executable", str(project_python)), \
                patch.object(gui_support, "find_desktop_python", return_value="/desktop/python3.13"), \
                patch.object(cli.subprocess, "call", return_value=0) as start:
            with self.assertRaises(SystemExit) as stopped:
                cli.main()

        self.assertEqual(stopped.exception.code, 0)
        start.assert_called_once()
        self.assertEqual(start.call_args.args[0], ["/desktop/python3.13", "-u", "-m", "experiment.gui"])
        self.assertEqual(start.call_args.kwargs["cwd"], str(gui_support.ROOT))
        environment = start.call_args.kwargs["env"]
        self.assertEqual(environment["IP_LOCATION_CLI_PYTHON"], str(project_python))
        self.assertNotEqual(environment["IP_LOCATION_CLI_PYTHON"], str(base_python))
        self.assertIsNot(environment, cli.os.environ)

    def test_fallback_runtime_failure_preserves_exit_status(self):
        with patch.object(gui.sys, "platform", "darwin"), \
                patch.object(gui.tk, "TkVersion", 8.5), \
                patch.object(gui, "find_desktop_python", return_value="/desktop/python3.13"), \
                patch.object(gui.subprocess, "call", return_value=7):
            with self.assertRaises(SystemExit) as stopped:
                gui.launch()
        self.assertEqual(stopped.exception.code, 7)

    def test_old_macos_tk_without_fallback_reports_setup_help_before_tk_can_abort(self):
        with patch.object(gui.sys, "platform", "darwin"), \
                patch.object(gui.tk, "TkVersion", 8.5), \
                patch.object(gui, "find_desktop_python", return_value=None), \
                patch.object(gui.tk, "Tk") as create_window:
            with self.assertRaisesRegex(SystemExit, "source setup.sh"):
                gui.launch()
        create_window.assert_not_called()

    def test_supported_tk_opens_the_window_without_changing_runtime(self):
        window = Mock()
        with patch.object(gui.sys, "platform", "darwin"), \
                patch.object(gui.tk, "TkVersion", 8.6), \
                patch.object(gui, "find_desktop_python") as find_runtime, \
                patch.object(gui.tk, "Tk", return_value=window), \
                patch.object(gui, "ExperimentApp") as create_app:
            gui.launch()
        find_runtime.assert_not_called()
        create_app.assert_called_once_with(window)
        window.mainloop.assert_called_once_with()

    def test_tcl_error_reports_desktop_setup_help(self):
        with patch.object(gui.tk, "TkVersion", 8.6), \
                patch.object(gui.tk, "Tk", side_effect=gui.tk.TclError("No desktop session")), \
                patch.object(gui, "ExperimentApp") as create_app:
            with self.assertRaisesRegex(SystemExit, "desktop session and Python with Tk"):
                gui.launch()
        create_app.assert_not_called()


if __name__ == "__main__":
    unittest.main()
