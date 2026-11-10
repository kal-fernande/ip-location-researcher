"""Desktop support tests without touching the active study, VPN, or browsers."""
from __future__ import annotations

import copy
import io
import json
import os
import queue
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from experiment import gui_support, gui_worker, run_lock


class DashboardTests(unittest.TestCase):
    def test_dashboard_shows_local_study_display_name(self):
        (self.study / "manifest.json").write_text(json.dumps({"name": "Follow-up study א"}), encoding="utf-8")
        state = gui_support.read_dashboard(now=self.now)
        self.assertEqual(state["study_name"], "Follow-up study א")
        self.assertEqual(state["study_id"], "demo")

    def test_retry_days_are_reported_from_the_active_study_without_writing(self):
        before = self.snapshot()
        options = [{"day": 1, "run_at": "2026-10-31T12:00:00-07:00", "missing": 2, "total": 12}]
        with patch("experiment.manual_retry.retry_options", return_value=options) as read:
            state = gui_support.read_dashboard(now=self.now)
        self.assertEqual(state["retry_days"], options)
        self.assertEqual(state["retry_error"], "")
        self.assertEqual(read.call_args.kwargs["cfg"]["results"]["csv_path"], str(self.study / "results.csv"))
        self.assertEqual(self.snapshot(), before)

    def test_retry_availability_waits_for_background_collection_to_finish(self):
        with patch.object(gui_support, "_collector_text", return_value="Collection is running"), \
                patch("experiment.manual_retry.retry_options") as read:
            state = gui_support.read_dashboard(now=self.now)
        self.assertEqual(state["retry_days"], [])
        self.assertIn("still running", state["retry_error"])
        read.assert_not_called()

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.study = self.root / "results/studies/demo"
        self.study.mkdir(parents=True)
        (self.study.parent / "ACTIVE").write_text("demo\n", encoding="utf-8")
        self.cfg = {
            "experiment": {"days": 3, "timezone": "America/Los_Angeles"},
            "results": {"studies_dir": "results/studies"},
            "browser": {"providers": {"chatgpt": {"enabled": True}, "claude": {"enabled": False}}},
        }
        for target, value in (
            ("ROOT", self.root), ("load_config", lambda: copy.deepcopy(self.cfg)),
        ):
            mocked = patch.object(gui_support, target, value)
            mocked.start()
            self.addCleanup(mocked.stop)
        self.now = datetime(2026, 11, 1, 18, 0, tzinfo=timezone.utc)
        self.save_schedule([
            {"day": 1, "run_at": "2026-10-31T12:00:00-07:00", "completed_modes": ["browser"]},
            {"day": 2, "run_at": "2026-11-01T11:30:00-08:00", "completed_modes": []},
        ])

    def save_schedule(self, schedule):
        (self.study / "schedule.json").write_text(json.dumps(schedule), encoding="utf-8")

    def snapshot(self):
        return {
            str(path.relative_to(self.root)): (path.read_bytes(), path.stat().st_mtime_ns)
            for path in self.root.rglob("*") if path.is_file()
        }

    def test_refresh_is_read_only_and_shows_saved_time_in_study_timezone(self):
        before = self.snapshot()
        state = gui_support.read_dashboard(now=self.now)
        self.assertEqual(state["error"], "")
        self.assertEqual(state["study_id"], "demo")
        self.assertEqual(state["csv_path"], self.study / "results.csv")
        self.assertEqual(state["xlsx_path"], self.study / "results.xlsx")
        self.assertEqual(state["providers"], ["chatgpt"])
        self.assertEqual(state["next_event"]["day"], 2)
        self.assertFalse(state["next_run_due"])
        self.assertIn("11:30 AM PST (America/Los_Angeles)", state["next_run_text"])
        self.assertIn("Upcoming", state["next_run_text"])
        self.assertEqual(state["schedule_text"], "1 of 3 browser study days finished")
        self.assertEqual(self.snapshot(), before)
        self.assertFalse((self.root / "private").exists())

    def test_due_time_uses_timezone_aware_comparison(self):
        self.save_schedule([
            {"day": 2, "run_at": "2026-11-01T17:59:00+00:00", "completed_modes": []},
        ])
        state = gui_support.read_dashboard(now=self.now)
        self.assertTrue(state["next_run_due"])
        self.assertIn("9:59 AM PST", state["next_run_text"])
        self.assertIn("Due now", state["next_run_text"])

    def test_pending_browser_event_ignores_other_modes_and_days_outside_study(self):
        self.save_schedule([
            {"day": 4, "run_at": "2026-10-29T12:00:00-07:00", "completed_modes": []},
            {"day": 1, "run_at": "2026-10-30T12:00:00-07:00", "completed_modes": ["both"]},
            {"day": 2, "run_at": "2026-10-31T12:00:00-07:00", "completed_modes": ["api"]},
            {"day": 3, "run_at": "2026-11-02T12:00:00-08:00", "completed_modes": []},
        ])
        state = gui_support.read_dashboard(now=self.now)
        self.assertEqual(state["next_event"]["day"], 2)
        self.assertEqual(state["schedule_text"], "1 of 3 browser study days finished")

    def test_missing_active_or_schedule_never_creates_replacements(self):
        (self.study / "schedule.json").unlink()
        before = self.snapshot()
        state = gui_support.read_dashboard(now=self.now)
        self.assertIn("no saved schedule", state["error"])
        self.assertEqual(self.snapshot(), before)
        (self.study.parent / "ACTIVE").unlink()
        before = self.snapshot()
        state = gui_support.read_dashboard(now=self.now)
        self.assertIn("No active study", state["error"])
        self.assertIsNone(state["csv_path"])
        self.assertEqual(self.snapshot(), before)

    def test_malformed_state_produces_a_readable_error(self):
        for raw in (
            "not json", "{}", '[{"day": 1, "run_at": "2026-11-01T12:00:00"}]',
            '[{"day": true, "run_at": "2026-11-01T12:00:00Z"}]',
            '[{"day": 1, "run_at": "2026-11-01T12:00:00Z", "completed_modes": "browser"}]',
        ):
            with self.subTest(raw=raw):
                (self.study / "schedule.json").write_text(raw, encoding="utf-8")
                state = gui_support.read_dashboard(now=self.now)
                self.assertTrue(state["error"])
                self.assertIsNone(state["next_event"])
                self.assertFalse(state["next_run_due"])
        self.cfg["experiment"]["timezone"] = "Not/A/Timezone"
        self.assertTrue(gui_support.read_dashboard(now=self.now)["error"])
        self.cfg["experiment"]["timezone"] = "America/Los_Angeles"
        (self.study.parent / "ACTIVE").write_text("\n", encoding="utf-8")
        self.assertTrue(gui_support.read_dashboard(now=self.now)["error"])

    def test_finished_schedule_and_disabled_browser(self):
        self.cfg["experiment"]["days"] = 1
        self.cfg["browser"]["enabled"] = False
        state = gui_support.read_dashboard(now=self.now)
        self.assertIsNone(state["next_event"])
        self.assertEqual(state["next_run_text"], "Study schedule finished")
        self.assertEqual(state["providers"], [])

    def test_collector_status_uses_flock_instead_of_stale_pid(self):
        lock = self.root / "private/scheduler/collection.lock"
        lock.parent.mkdir(parents=True)
        lock.write_text(str(os.getpid()), encoding="utf-8")
        before = self.snapshot()
        self.assertEqual(gui_support.read_dashboard(now=self.now)["collector_text"], "No collection running")
        with patch.object(run_lock, "LOCK_PATH", lock):
            with run_lock.collection_lock():
                during = self.snapshot()
                self.assertEqual(gui_support.read_dashboard(now=self.now)["collector_text"], "Collection is running")
                self.assertEqual(self.snapshot(), during)
        self.assertEqual(set(self.snapshot()), set(before))

    def test_alternate_gui_python_delegates_read_only_dashboard_to_project_python(self):
        expected = gui_support.read_dashboard(now=self.now)
        payload = json.dumps(expected, default=str)
        response = subprocess.CompletedProcess([], 0, stdout=payload, stderr="")
        before = self.snapshot()
        with patch.dict(os.environ, {"IP_LOCATION_CLI_PYTHON": "/project/environment/bin/python"}):
            with patch.object(gui_support.subprocess, "run", return_value=response) as run:
                state = gui_support.read_dashboard()
        self.assertEqual(state["study_id"], "demo")
        self.assertEqual(state["csv_path"], str(self.study / "results.csv"))
        self.assertEqual(state["next_event"], expected["next_event"])
        self.assertEqual(run.call_args.args[0], [
            "/project/environment/bin/python", "-u", "-m", "experiment.gui_worker", "--gui-dashboard",
        ])
        self.assertEqual(run.call_args.kwargs["cwd"], str(self.root))
        self.assertEqual(self.snapshot(), before)

    def test_delegated_dashboard_errors_leave_a_usable_read_only_default(self):
        before = self.snapshot()
        responses = (
            subprocess.TimeoutExpired(["python"], 5),
            OSError("project runtime unavailable"),
            subprocess.CompletedProcess([], 1, stdout="", stderr="project failed"),
            subprocess.CompletedProcess([], 0, stdout="not json", stderr=""),
            subprocess.CompletedProcess([], 0, stdout="{}", stderr=""),
        )
        with patch.dict(os.environ, {"IP_LOCATION_CLI_PYTHON": "/project/environment/bin/python"}):
            for response in responses:
                with self.subTest(response=response):
                    kwargs = {"side_effect": response} if isinstance(response, Exception) else {"return_value": response}
                    with patch.object(gui_support.subprocess, "run", **kwargs):
                        state = gui_support.read_dashboard()
                    self.assertTrue(state["error"])
                    self.assertEqual(state["next_run_text"], "Next run unavailable")
                    self.assertIsNone(state["next_event"])
                    self.assertEqual(state["providers"], [])
        self.assertEqual(self.snapshot(), before)


class TaskRunnerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        mocked = patch.object(gui_support, "ROOT", self.root)
        mocked.start()
        self.addCleanup(mocked.stop)
        self.runner = gui_support.TaskRunner()
        self.addCleanup(self.stop_runner)

    def stop_runner(self):
        self.runner.stop()
        deadline = time.monotonic() + 5
        while self.runner.running and time.monotonic() < deadline:
            time.sleep(0.01)

    def script(self, body):
        path = self.root / "fixture.py"
        path.write_text(body, encoding="utf-8")
        return str(path)

    def await_events(self, predicate, timeout=5):
        events = []
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                events.append(self.runner.events.get(timeout=0.05))
            except queue.Empty:
                continue
            if predicate(events):
                return events
        self.fail(f"Timed out waiting for task events: {events}")

    def test_streams_unterminated_prompt_accepts_unicode_input_and_prevents_overlap(self):
        path = self.script(
            "import os, sys\n"
            "print('Ready: ', end='', flush=True)\n"
            "answer = input()\n"
            "print('Answer=' + answer)\n"
            "print('CWD=' + os.getcwd())\n"
            "print('PATH=' + os.environ['PATH'])\n"
            "print('Argument=' + sys.argv[1])\n"
        )
        argument = "literal $(do-not-run); `do-not-run`"
        self.runner.start([argument], script=path)
        events = self.await_events(lambda items: "Ready: " in "".join(str(v) for k, v in items if k == "output"))
        self.assertTrue(self.runner.running)
        with self.assertRaises(RuntimeError):
            self.runner.start([], script=path)
        self.runner.send_input("שלום 🌍\n")
        events += self.await_events(lambda items: any(k == "done" for k, _ in items))
        output = "".join(v for k, v in events if k == "output")
        self.assertIn("Answer=שלום 🌍", output)
        self.assertIn("CWD=" + str(self.root.resolve()), output)
        self.assertIn("/opt/homebrew/bin", output)
        self.assertIn("/usr/local/bin", output)
        self.assertIn("Argument=" + argument, output)
        self.assertIn(("done", 0), events)
        self.assertFalse(self.runner.running)
        self.assertFalse(self.runner.cancelled)

    def test_stop_signals_child_and_tracks_cancellation_even_if_it_exits_zero(self):
        path = self.script(
            "import signal, sys, time\n"
            "def stop(sig, frame):\n"
            "    print('Interrupted cleanly', flush=True)\n"
            "    sys.exit(0)\n"
            "signal.signal(signal.SIGINT, stop)\n"
            "print('Waiting', flush=True)\n"
            "while True: time.sleep(0.1)\n"
        )
        self.runner.start([], script=path)
        self.await_events(lambda items: any(k == "output" and "Waiting" in v for k, v in items))
        self.runner.stop()
        self.runner.stop()
        events = self.await_events(lambda items: any(k == "done" for k, _ in items))
        self.assertIn(("done", 0), events)
        self.assertTrue(self.runner.cancelled)
        self.assertIn("Interrupted cleanly", "".join(v for k, v in events if k == "output"))

    def test_stop_during_launch_still_interrupts_the_child(self):
        path = self.script("import time\nprint('Started', flush=True)\ntime.sleep(30)\n")
        launched = threading.Event()
        release = threading.Event()
        real_popen = subprocess.Popen

        def delayed_popen(*args, **kwargs):
            process = real_popen(*args, **kwargs)
            launched.set()
            release.wait(5)
            return process

        with patch.object(gui_support.subprocess, "Popen", delayed_popen):
            self.runner.start([], script=path)
            self.assertTrue(launched.wait(5))
            self.runner.stop()
            release.set()
            events = self.await_events(lambda items: any(k == "done" for k, _ in items))
        self.assertTrue(self.runner.cancelled)
        self.assertFalse(self.runner.running)
        self.assertTrue(any(k == "done" and v != 0 for k, v in events))

    def test_launch_error_is_reported_and_runner_can_be_reused(self):
        with patch.object(gui_support.subprocess, "Popen", side_effect=OSError("fixture launch failure")):
            self.runner.start([], script="fixture.py")
            events = self.await_events(lambda items: any(k == "done" for k, _ in items))
        self.assertTrue(any(k == "error" and "fixture launch failure" in v for k, v in events))
        self.assertIn(("done", 1), events)
        self.assertFalse(self.runner.running)
        with self.assertRaises(RuntimeError):
            self.runner.send_input("ignored\n")
        path = self.script("print('Recovered')\n")
        self.runner.start([], script=path)
        self.assertIn(("done", 0), self.await_events(lambda items: any(k == "done" for k, _ in items)))

    def test_large_input_is_delivered_in_background_to_a_delayed_reader(self):
        path = self.script(
            "import sys, time\n"
            "print('Ready', flush=True)\n"
            "time.sleep(0.4)\n"
            "answer = sys.stdin.buffer.readline()\n"
            "print('Bytes=' + str(len(answer)), flush=True)\n"
        )
        self.runner.start([], script=path)
        self.await_events(lambda items: any(k == "output" and "Ready" in v for k, v in items))
        answer = "x" * (1024 * 1024) + "\n"
        started = time.monotonic()
        self.runner.send_input(answer)
        self.assertLess(time.monotonic() - started, 0.2)
        with self.assertRaises(RuntimeError):
            self.runner.send_input("duplicate\n")
        events = self.await_events(lambda items: any(k == "done" for k, _ in items))
        self.assertIn(("input_sent", "Input delivered."), events)
        self.assertIn(("done", 0), events)
        self.assertIn(f"Bytes={len(answer)}", "".join(v for k, v in events if k == "output"))

    def test_stop_remains_responsive_while_a_large_input_write_is_blocked(self):
        path = self.script(
            "import signal, sys, time\n"
            "signal.signal(signal.SIGINT, lambda sig, frame: sys.exit(0))\n"
            "print('Ready', flush=True)\n"
            "time.sleep(30)\n"
        )
        self.runner.start([], script=path)
        self.await_events(lambda items: any(k == "output" and "Ready" in v for k, v in items))
        self.runner.send_input("x" * (1024 * 1024) + "\n")
        started = time.monotonic()
        self.runner.stop()
        self.assertLess(time.monotonic() - started, 0.2)
        events = self.await_events(lambda items: any(k == "done" for k, _ in items))
        self.assertIn(("done", 0), events)
        self.assertTrue(self.runner.cancelled)
        self.assertFalse(self.runner.running)

    def test_output_failure_interrupts_child_and_waits_for_its_cleanup(self):
        path = self.script(
            "import signal, sys, time\n"
            "def stop(sig, frame):\n"
            "    time.sleep(0.5)\n"
            "    sys.exit(0)\n"
            "signal.signal(signal.SIGINT, stop)\n"
            "print('Ready', flush=True)\n"
            "time.sleep(30)\n"
        )
        real_popen = subprocess.Popen
        children = []

        class FailingReader:
            def __init__(self, stream):
                self.stream = stream
                self.first = True

            def read(self, size):
                if self.first:
                    self.first = False
                    return self.stream.read(size)
                raise OSError("fixture output read failure")

            def close(self):
                self.stream.close()

        def failing_popen(*args, **kwargs):
            child = real_popen(*args, **kwargs)
            child.stdout = FailingReader(child.stdout)
            children.append(child)
            return child

        with patch.object(gui_support.subprocess, "Popen", failing_popen):
            self.runner.start([], script=path)
            events = self.await_events(lambda items: any(k == "error" for k, _ in items))
            self.assertTrue(self.runner.running)
            self.assertIsNone(children[0].poll())
            events += self.await_events(lambda items: any(k == "done" for k, _ in items))
        self.assertIn(("done", 1), events)
        self.assertEqual(children[0].poll(), 0)
        self.assertFalse(self.runner.running)

    def test_tasks_use_project_cli_python_when_gui_has_a_separate_runtime(self):
        child = unittest.mock.Mock()
        child.stdout = io.BytesIO()
        child.stdin = io.BytesIO()
        child.wait.return_value = 0
        with patch.dict(os.environ, {"IP_LOCATION_CLI_PYTHON": "/project/environment/bin/python"}):
            with patch.object(gui_support.subprocess, "Popen", return_value=child) as popen:
                self.runner.start(["study-status"])
                self.assertIn(("done", 0), self.await_events(lambda items: any(k == "done" for k, _ in items)))
        self.assertEqual(popen.call_args.args[0], [
            "/project/environment/bin/python", "-u", "-m", "experiment.gui_worker", "study-status",
        ])
        self.assertEqual(popen.call_args.kwargs["env"]["PATH"].split(os.pathsep)[0], "/project/environment/bin")


class GuiWorkerTests(unittest.TestCase):
    def test_dashboard_worker_does_not_enter_cli_or_collection(self):
        with patch.object(gui_support, "read_dashboard", return_value={"root": Path("/study")}):
            with patch.object(gui_worker, "_invoke_cli") as invoke, patch.object(gui_worker, "collection_lock") as lock:
                with patch("sys.stdout", new_callable=io.StringIO) as output:
                    gui_worker.main(["--gui-dashboard"])
        self.assertEqual(json.loads(output.getvalue()), {"root": "/study"})
        invoke.assert_not_called()
        lock.assert_not_called()

    def test_gui_support_imports_without_site_packages(self):
        result = subprocess.run(
            [sys.executable, "-S", "-c", (
                "import sys; import experiment.gui_support; "
                "assert 'experiment.config' not in sys.modules; "
                "assert 'experiment.study' not in sys.modules"
            )],
            cwd=str(Path(gui_worker.__file__).resolve().parent.parent),
            capture_output=True, text=True, timeout=5,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_setup_and_manual_checks_refuse_a_busy_collection_lock(self):
        with tempfile.TemporaryDirectory() as temporary:
            lock_path = Path(temporary) / "collection.lock"
            with patch.object(run_lock, "LOCK_PATH", lock_path):
                with run_lock.collection_lock():
                    for command in gui_worker.LOCKED_COMMANDS:
                        with self.subTest(command=command), patch.object(gui_worker, "_invoke_cli") as invoke:
                            with patch("sys.stderr"), self.assertRaises(SystemExit) as stopped:
                                gui_worker.main([command])
                            self.assertEqual(stopped.exception.code, 1)
                            invoke.assert_not_called()

    def test_worker_takes_lock_for_checks_and_leaves_existing_run_locking_to_cli(self):
        with tempfile.TemporaryDirectory() as temporary:
            lock_path = Path(temporary) / "collection.lock"
            with patch.object(run_lock, "LOCK_PATH", lock_path):
                def check_locked(args):
                    with self.assertRaises(run_lock.CollectionBusy):
                        with run_lock.collection_lock():
                            pass
                with patch.object(gui_worker, "_invoke_cli", side_effect=check_locked):
                    gui_worker.main(["check-browser"])
                def check_unlocked(args):
                    with run_lock.collection_lock():
                        pass
                with patch.object(gui_worker, "_invoke_cli", side_effect=check_unlocked):
                    gui_worker.main(["run-due", "--mode", "browser"])


if __name__ == "__main__":
    unittest.main()
