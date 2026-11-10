"""Unit tests for per-study folders and the ACTIVE pointer."""
from __future__ import annotations

import copy
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from experiment import study as study_mod
from experiment.study import (
    ActiveStudyExists,
    NoActiveStudy,
    apply_active_study,
    apply_study,
    read_active_study_id,
    start_study,
    stop_study,
    validate_study_id,
)


class StudyHelpersTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.cfg = {
            "experiment": {
                "timezone": "America/Los_Angeles",
                "days": 2,
                "schedule_policy": "random",
                "schedule_window": {"start_hour": 9, "end_hour": 17},
            },
            "locations": {"US": {}, "IL": {}},
            "results": {"studies_dir": str(self.root / "studies")},
        }
        from experiment import run_lock
        lock = patch.object(run_lock, "LOCK_PATH", self.root / "private/collection.lock")
        lock.start()
        self.addCleanup(lock.stop)
        for target, value in (
            ("ROOT", self.root),
            ("load_config", lambda: copy.deepcopy(self.cfg)),
        ):
            mocked = patch.object(study_mod, target, value)
            mocked.start()
            self.addCleanup(mocked.stop)
        # create_schedule uses runner.load_config and resolve_path against ROOT
        from experiment import runner as runner_mod
        from experiment import config as config_mod
        for mod in (runner_mod, config_mod):
            mocked = patch.object(mod, "ROOT", self.root)
            mocked.start()
            self.addCleanup(mocked.stop)
        mocked = patch.object(runner_mod, "load_config", lambda: copy.deepcopy(self.cfg))
        mocked.start()
        self.addCleanup(mocked.stop)
        # resolve_path is used with absolute study paths we set; also patch study.resolve_path
        def resolve(path_str: str) -> Path:
            p = Path(path_str)
            return p if p.is_absolute() else self.root / p
        for mod in (study_mod, runner_mod):
            mocked = patch.object(mod, "resolve_path", resolve)
            mocked.start()
            self.addCleanup(mocked.stop)

    def test_validate_study_id(self):
        self.assertEqual(validate_study_id("2026-10-19_browser"), "2026-10-19_browser")
        with self.assertRaises(ValueError):
            validate_study_id("../evil")

    def test_apply_study_paths(self):
        original = copy.deepcopy(self.cfg)
        self.cfg["results"]["analysis_dir"] = "results/analysis"
        cfg = apply_study(self.cfg, "demo")
        root = self.root / "studies" / "demo"
        self.assertEqual(cfg["results"]["study_id"], "demo")
        for key, relative in {
            "csv_path": "results.csv", "schedule_path": "schedule.json",
            "runs_dir": "runs", "failure_screenshot_dir": "failures",
            "index_path": "README.md", "details_path": "results.md",
            "analysis_dir": "analysis",
        }.items():
            self.assertEqual(Path(cfg["results"][key]), root / relative)
        self.assertEqual(self.cfg["results"]["analysis_dir"], "results/analysis")
        self.assertEqual(self.cfg["experiment"], original["experiment"])

    def test_no_active_study(self):
        with self.assertRaises(NoActiveStudy):
            read_active_study_id(self.cfg)

    def test_empty_active_pointer_is_no_active_study(self):
        pointer = self.root / "studies" / "ACTIVE"
        pointer.parent.mkdir()
        pointer.write_text(" \n\n", encoding="utf-8")
        with self.assertRaises(NoActiveStudy):
            read_active_study_id(self.cfg)

    def test_start_study_creates_folder_and_active(self):
        study_id, root = start_study("pilot_a", cfg=self.cfg, start_date="2026-10-21")
        self.assertEqual(study_id, "pilot_a")
        self.assertTrue((root / "manifest.json").is_file())
        self.assertTrue((root / "schedule.json").is_file())
        self.assertTrue((root / "runs").is_dir())
        self.assertEqual(read_active_study_id(self.cfg), "pilot_a")
        active = apply_active_study(self.cfg)
        self.assertEqual(Path(active["results"]["csv_path"]), root / "results.csv")
        schedule = json.loads((root / "schedule.json").read_text(encoding="utf-8"))
        self.assertEqual(schedule[0]["day"], 1)
        self.assertIn("2026-10-21", schedule[0]["run_at"])

    def test_display_name_is_saved_with_an_explicit_id(self):
        _, root = start_study("pilot_a", name="Pilot study א", cfg=self.cfg)
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["study_id"], "pilot_a")
        self.assertEqual(manifest["name"], "Pilot study א")

    def test_same_display_name_gets_separate_folders_even_at_same_time(self):
        now = datetime.fromisoformat("2026-11-09T09:00:00.123456-08:00")
        with patch.object(study_mod, "datetime") as clock:
            clock.now.return_value = now
            first_id, first_root = start_study(name="Café / pilot study", cfg=self.cfg)
            first_manifest = (first_root / "manifest.json").read_bytes()
            second_id, second_root = start_study(name="Café / pilot study", cfg=self.cfg, force=True)
        self.assertEqual(first_id, "2026-11-09_090000_123456_cafe-pilot-study")
        self.assertEqual(second_id, first_id + "_2")
        self.assertNotEqual(first_root, second_root)
        self.assertEqual((first_root / "manifest.json").read_bytes(), first_manifest)
        self.assertTrue((first_root / "schedule.json").is_file())
        self.assertTrue((second_root / "schedule.json").is_file())
        self.assertEqual(read_active_study_id(self.cfg), second_id)

    def test_blank_name_keeps_timestamp_browser_id_and_avoids_collisions(self):
        now = datetime.fromisoformat("2026-11-09T09:00:00-08:00")
        with patch.object(study_mod, "datetime") as clock:
            clock.now.return_value = now
            first_id, _ = start_study(cfg=self.cfg)
            second_id, _ = start_study(cfg=self.cfg, force=True)
        self.assertEqual(first_id, "2026-11-09_090000_browser")
        self.assertEqual(second_id, first_id + "_2")

    def test_non_ascii_name_has_a_safe_folder_id(self):
        study_id, root = start_study(name="מחקר א", cfg=self.cfg)
        self.assertEqual(validate_study_id(study_id), study_id)
        self.assertTrue(study_id.endswith("_study"))
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["name"], "מחקר א")

    def test_start_study_refuses_when_active_exists(self):
        start_study("pilot_a", cfg=self.cfg, start_date="2026-10-21")
        with self.assertRaises(ActiveStudyExists):
            start_study("pilot_b", cfg=self.cfg, start_date="2026-10-22")
        self.assertEqual(read_active_study_id(self.cfg), "pilot_a")
        self.assertFalse((self.root / "studies" / "pilot_b").exists())

    def test_start_study_force_replaces_active(self):
        start_study("pilot_a", cfg=self.cfg, start_date="2026-10-21")
        study_id, root = start_study(
            "pilot_b", cfg=self.cfg, start_date="2026-10-22", force=True,
        )
        self.assertEqual(study_id, "pilot_b")
        self.assertEqual(read_active_study_id(self.cfg), "pilot_b")
        self.assertTrue((self.root / "studies" / "pilot_a" / "schedule.json").is_file())
        self.assertTrue((root / "schedule.json").is_file())

    def _existing_study_snapshot(self):
        _, root = start_study("pilot_a", cfg=self.cfg)
        (root / "results.csv").write_text("saved local answers\n", encoding="utf-8")
        (root / "runs" / "saved.json").write_text('{"saved": true}\n', encoding="utf-8")
        return {
            path.relative_to(self.root): path.read_bytes()
            for path in self.root.rglob("*") if path.is_file()
        }

    def _assert_previous_study_unchanged(self, snapshot):
        self.assertEqual(read_active_study_id(self.cfg), "pilot_a")
        self.assertEqual({
            path.relative_to(self.root): path.read_bytes()
            for path in self.root.rglob("*") if path.is_file()
        }, snapshot)
        self.assertEqual(
            {path.name for path in (self.root / "studies").iterdir()},
            {"ACTIVE", "pilot_a"},
        )

    def test_invalid_date_preserves_active_study_and_all_previous_data(self):
        snapshot = self._existing_study_snapshot()
        with self.assertRaises(ValueError):
            start_study("pilot_b", cfg=self.cfg, start_date="2026-02-30", force=True)
        self._assert_previous_study_unchanged(snapshot)

    def test_missing_agenda_preserves_active_study_and_all_previous_data(self):
        snapshot = self._existing_study_snapshot()
        with self.assertRaises(FileNotFoundError):
            start_study("pilot_b", cfg=self.cfg, agenda_path=self.root / "missing.json", force=True)
        self._assert_previous_study_unchanged(snapshot)

    def test_file_write_failures_preserve_active_study_and_all_previous_data(self):
        snapshot = self._existing_study_snapshot()
        write_text = Path.write_text
        for filename in ("manifest.json", "schedule.json.tmp"):
            def fail_write(path, *args, **kwargs):
                if path.name == filename and path.parent.name == "pilot_b":
                    raise OSError("Study folder cannot be written")
                return write_text(path, *args, **kwargs)
            with self.subTest(filename=filename), patch.object(Path, "write_text", fail_write):
                with self.assertRaisesRegex(OSError, "cannot be written"):
                    start_study("pilot_b", cfg=self.cfg, force=True)
            self._assert_previous_study_unchanged(snapshot)

    def test_active_write_failure_preserves_previous_pointer_and_data(self):
        snapshot = self._existing_study_snapshot()
        replace = Path.replace
        def fail_replace(path, target):
            if Path(target).name == "ACTIVE":
                raise OSError("Cannot update active study")
            return replace(path, target)
        with patch.object(Path, "replace", fail_replace):
            with self.assertRaisesRegex(OSError, "Cannot update active study"):
                start_study("pilot_b", cfg=self.cfg, force=True)
        self._assert_previous_study_unchanged(snapshot)

    def test_failed_first_creation_has_no_active_pointer_or_study_folder(self):
        for options, error in (
            ({"start_date": "not-a-date"}, ValueError),
            ({"agenda_path": self.root / "missing.json"}, FileNotFoundError),
        ):
            with self.subTest(options=options), self.assertRaises(error):
                start_study("failed", cfg=self.cfg, **options)
            with self.assertRaises(NoActiveStudy):
                read_active_study_id(self.cfg)
            self.assertEqual(list((self.root / "studies").iterdir()), [])

    def test_duplicate_explicit_id_preserves_previous_study(self):
        snapshot = self._existing_study_snapshot()
        with self.assertRaises(FileExistsError):
            start_study("pilot_a", name="Another name", cfg=self.cfg, force=True)
        self._assert_previous_study_unchanged(snapshot)


    def test_stop_keeps_saved_files_and_allows_a_separate_new_study(self):
        _, root = start_study("pilot_a", cfg=self.cfg)
        for name in ("results.csv", "results.xlsx", "runs/saved.json", "failures/captcha.png"):
            (root / name).write_bytes(b"saved study data")
        before = {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}

        result = stop_study(study_id="pilot_a", cfg=self.cfg)

        self.assertEqual(result["study_id"], "pilot_a")
        self.assertFalse(result["collector_alive"])
        self.assertTrue((root / study_mod.STOPPED_NAME).is_file())
        with self.assertRaises(NoActiveStudy):
            apply_active_study(self.cfg)
        _, new_root = start_study("pilot_b", cfg=self.cfg)
        self.assertNotEqual(new_root, root)
        self.assertEqual(read_active_study_id(self.cfg), "pilot_b")
        self.assertEqual(before, {p: (root / p).read_bytes() for p in before})

    def test_repeated_stop_without_an_active_study_does_nothing(self):
        from experiment import vpn
        _, root = start_study("pilot_a", cfg=self.cfg)
        stop_study(cfg=self.cfg)
        marker = (root / study_mod.STOPPED_NAME).read_bytes()
        with patch.object(vpn, "vpn_off") as cleanup:
            self.assertIsNone(stop_study(cfg=self.cfg))
            cleanup.assert_not_called()
        self.assertEqual((root / study_mod.STOPPED_NAME).read_bytes(), marker)

    def test_stale_stop_request_does_not_touch_a_new_active_study(self):
        start_study("pilot_a", cfg=self.cfg)
        _, new_root = start_study("pilot_b", cfg=self.cfg, force=True)
        with self.assertRaisesRegex(ValueError, "active study changed"):
            stop_study(study_id="pilot_a", cfg=self.cfg)
        self.assertEqual(read_active_study_id(self.cfg), "pilot_b")
        self.assertFalse((new_root / study_mod.STOPPED_NAME).exists())

    def test_stop_blocks_future_runs_before_cleaning_up_a_busy_collector(self):
        from experiment import run_lock, vpn
        _, root = start_study("pilot_a", cfg=self.cfg)

        def cleanup(**kwargs):
            self.assertTrue((root / study_mod.STOPPED_NAME).exists())
            with self.assertRaises(NoActiveStudy):
                read_active_study_id(self.cfg)
            return {"collector_alive": False}

        with patch.object(run_lock, "collection_running", side_effect=[True, False]), \
                patch.object(vpn, "vpn_off", side_effect=cleanup) as stop:
            result = stop_study(cfg=self.cfg)
        stop.assert_called_once_with(cfg=self.cfg)
        self.assertFalse(result["collector_alive"])

    def test_failed_cleanup_keeps_study_stopped_and_reports_the_problem(self):
        from experiment import run_lock, vpn
        _, root = start_study("pilot_a", cfg=self.cfg)
        with patch.object(run_lock, "collection_running", return_value=True), \
                patch.object(vpn, "vpn_off", side_effect=OSError("test disconnect failed")):
            result = stop_study(cfg=self.cfg)
        self.assertTrue(result["collector_alive"])
        self.assertIn("cleanup needs attention", result["error"])
        self.assertTrue((root / study_mod.STOPPED_NAME).exists())
        with self.assertRaises(NoActiveStudy):
            read_active_study_id(self.cfg)

    def test_stopped_study_never_collects_or_sends_advance_reminders(self):
        from experiment import runner
        _, root = start_study("pilot_a", cfg=self.cfg)
        cached = apply_active_study(self.cfg)
        before = (root / "schedule.json").read_bytes()
        stop_study(cfg=self.cfg)
        with patch.object(runner, "run_day") as collect, \
                patch.object(runner, "_maybe_notify_upcoming_runs") as notify:
            self.assertEqual(runner.run_due("browser", cfg=cached), [])
        collect.assert_not_called()
        notify.assert_not_called()
        self.assertEqual((root / "schedule.json").read_bytes(), before)
        with self.assertRaisesRegex(ValueError, "study has been stopped"):
            runner.run_day(1, "browser", force=True, cfg=cached)

    def test_scheduler_rechecks_stop_after_waiting_for_collection_lock(self):
        from experiment import run_lock, runner
        _, root = start_study("pilot_a", cfg=self.cfg)
        cached = apply_active_study(self.cfg)
        before = (root / "schedule.json").read_bytes()
        with patch.object(run_lock, "collection_lock") as lock, \
                patch.object(runner, "_maybe_notify_upcoming_runs"), \
                patch.object(runner, "run_day") as collect:
            lock.return_value.__enter__.side_effect = lambda: stop_study(cfg=self.cfg)
            self.assertEqual(runner.run_due("browser", cfg=cached), [])
        collect.assert_not_called()
        self.assertEqual((root / "schedule.json").read_bytes(), before)

    def test_scheduler_without_an_active_study_is_a_quiet_no_op(self):
        from experiment import runner
        with redirect_stdout(io.StringIO()), patch.object(runner, "run_day") as collect:
            self.assertEqual(runner.run_due("browser"), [])
            runner.daemon("browser")
        collect.assert_not_called()


class StudyCliTests(unittest.TestCase):
    def test_cli_passes_display_name_and_explicit_id_to_creation(self):
        import main as cli
        with patch.object(cli.sys, "argv", [
            "main.py", "start-study", "--id", "pilot_a", "--name", "Pilot study א",
        ]), patch.object(cli, "start_study", return_value=("pilot_a", Path("/local/studies/pilot_a"))) as create, \
                patch.object(cli, "study_status", return_value={}), redirect_stdout(io.StringIO()):
            cli.main()
        self.assertEqual(create.call_args.args, ("pilot_a",))
        self.assertEqual(create.call_args.kwargs["name"], "Pilot study א")

    def test_stop_cli_uses_the_selected_study_and_reports_saved_files(self):
        import main as cli
        output = io.StringIO()
        with patch.object(cli.sys, "argv", ["main.py", "stop-study", "--study-id", "pilot_a"]), \
                patch.object(cli, "stop_study", return_value={
                    "study_id": "pilot_a", "root": "/local/studies/pilot_a",
                    "collector_alive": False, "error": "",
                }) as stop, redirect_stdout(output):
            cli.main()
        stop.assert_called_once_with(study_id="pilot_a")
        self.assertIn("Saved results kept in: /local/studies/pilot_a", output.getvalue())
        self.assertIn("Future runs are cancelled", output.getvalue())


if __name__ == "__main__":
    unittest.main()
