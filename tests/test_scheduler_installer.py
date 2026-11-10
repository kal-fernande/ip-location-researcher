from pathlib import Path
import plistlib
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from scripts import install_scheduler as installer


class SchedulerInstallerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        python = self.root / ".venv/bin/python"
        python.parent.mkdir(parents=True)
        python.write_text("fixture")
        python.chmod(0o755)
        study = self.root / "studies" / "test_study"
        study.mkdir(parents=True)
        (study / "schedule.json").write_text("[]")
        (self.root / "studies" / "ACTIVE").write_text("test_study\n")
        for target, value in (("ROOT", self.root),):
            mocked = patch.object(installer, target, value)
            mocked.start()
            self.addCleanup(mocked.stop)
        for mocked in (patch.object(Path, "home", return_value=self.root),
                       patch("experiment.study.apply_active_study", return_value={"results": {"schedule_path": str(study / "schedule.json")}}),
                       patch.object(installer.sys, "platform", "darwin"),
                       patch.object(installer.shutil, "which", return_value="/usr/local/bin/mullvad"),
                       patch.object(installer.sys, "argv", ["install_scheduler.py", "--install", "--replace"])):
            mocked.start()
            self.addCleanup(mocked.stop)
        self.destination = self.root / "Library/LaunchAgents" / (installer.LABEL + ".plist")
        self.destination.parent.mkdir(parents=True)
        old = installer.agent_config()
        old.pop("StartCalendarInterval")
        old["StartInterval"] = 60
        self.previous = plistlib.dumps(old)
        self.destination.write_bytes(self.previous)

    def test_replacement_unloads_and_preserves_backup(self):
        def run(command, **kwargs):
            return subprocess.CompletedProcess(command, 0, "state = not running\n", "")
        with patch.object(installer.subprocess, "run", side_effect=run) as calls:
            installer.main()
        self.assertEqual(plistlib.loads(self.destination.read_bytes()), installer.agent_config())
        self.assertEqual((self.root / "private/scheduler/previous-launchagent.plist").read_bytes(), self.previous)
        self.assertEqual([call.args[0][1] for call in calls.call_args_list if call.args[0][0] == "launchctl"],
                         ["print", "bootout", "bootstrap"])

    def test_running_collection_is_not_interrupted(self):
        result = subprocess.CompletedProcess([], 0, "state = running\n", "")
        with patch.object(installer.subprocess, "run", return_value=result) as calls:
            with self.assertRaises(SystemExit):
                installer.main()
        self.assertEqual(calls.call_count, 1)
        self.assertEqual(self.destination.read_bytes(), self.previous)

    def test_failed_install_restores_previous_definition(self):
        def run(command, **kwargs):
            if command[:2] == ["plutil", "-lint"]:
                raise subprocess.CalledProcessError(1, command)
            return subprocess.CompletedProcess(command, 0, "state = not running\n", "")
        with patch.object(installer.subprocess, "run", side_effect=run):
            with self.assertRaises(subprocess.CalledProcessError):
                installer.main()
        self.assertEqual(self.destination.read_bytes(), self.previous)

    def test_backup_failure_leaves_loaded_job_untouched(self):
        original_write = Path.write_bytes
        def write(path, data):
            if path.name == "previous-launchagent.plist":
                raise PermissionError("Backup denied")
            return original_write(path, data)
        result = subprocess.CompletedProcess([], 0, "state = not running\n", "")
        with patch.object(installer.subprocess, "run", return_value=result) as calls, patch.object(Path, "write_bytes", write):
            with self.assertRaisesRegex(PermissionError, "Backup denied"):
                installer.main()
        self.assertEqual(calls.call_count, 1)
        self.assertEqual(self.destination.read_bytes(), self.previous)

    def test_bootstrap_failure_restores_and_reloads_old_job(self):
        attempts = []
        def run(command, **kwargs):
            if command[:2] == ["launchctl", "bootstrap"]:
                attempts.append(self.destination.read_bytes())
                if len(attempts) == 1:
                    raise subprocess.CalledProcessError(1, command)
            return subprocess.CompletedProcess(command, 0, "state = not running\n", "")
        with patch.object(installer.subprocess, "run", side_effect=run):
            with self.assertRaises(subprocess.CalledProcessError):
                installer.main()
        self.assertEqual(len(attempts), 2)
        self.assertEqual(attempts[1], self.previous)
        self.assertEqual(self.destination.read_bytes(), self.previous)


if __name__ == "__main__":
    unittest.main()
