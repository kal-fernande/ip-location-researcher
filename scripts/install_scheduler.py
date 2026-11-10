"""Generate, or install, the macOS browser-only daily scheduler."""
import argparse
import os
from pathlib import Path
import plistlib
import re
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parent.parent
LABEL = "local.ip-location-experiment"


def agent_config():
    return {
        "Label": LABEL,
        "ProgramArguments": [
            str(ROOT / ".venv/bin/python"), "-u", str(ROOT / "main.py"),
            "run-due", "--mode", "browser",
        ],
        "WorkingDirectory": str(ROOT),
        "EnvironmentVariables": {
            "PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
        },
        "RunAtLoad": True,
        # Calendar triggers remain effective when macOS defers interval jobs.
        # An explicit entry for every minute also catches up after wake.
        "StartCalendarInterval": [{"Minute": minute} for minute in range(60)],
        "ProcessType": "Background",
        "LimitLoadToSessionType": "Aqua",
        "StandardOutPath": str(ROOT / "private/scheduler/stdout.log"),
        "StandardErrorPath": str(ROOT / "private/scheduler/stderr.log"),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--install", action="store_true", help="Install and load the LaunchAgent")
    parser.add_argument("--replace", action="store_true", help="Replace this project's installed agent when idle")
    args = parser.parse_args()
    if args.replace and not args.install:
        parser.error("Use --replace together with --install")
    if not args.install:
        sys.stdout.buffer.write(plistlib.dumps(agent_config()))
        return
    if sys.platform != "darwin":
        parser.error("Installation requires macOS")
    sys.path.insert(0, str(ROOT))
    try:
        from experiment.config import resolve_path
        from experiment.study import NoActiveStudy, apply_active_study
        study_schedule = resolve_path(apply_active_study()["results"]["schedule_path"])
    except NoActiveStudy:
        parser.error("No active study; run: python main.py start-study")
    except Exception as error:
        parser.error(f"Could not resolve active study schedule: {error}")
    if not study_schedule.is_file():
        parser.error(f"Active study has no schedule at {study_schedule}; run: python main.py start-study")
    if not os.access(ROOT / ".venv/bin/python", os.X_OK):
        parser.error("The project virtual environment is missing; source setup.sh first")
    if not shutil.which("mullvad", path=agent_config()["EnvironmentVariables"]["PATH"]):
        parser.error("Mullvad CLI is missing from the scheduler PATH")

    destination = Path.home() / "Library/LaunchAgents" / (LABEL + ".plist")
    if destination.exists() and not args.replace:
        parser.error("Scheduler already installed; unload and remove it before reinstalling")
    destination.parent.mkdir(parents=True, exist_ok=True)
    (ROOT / "private/scheduler").mkdir(parents=True, exist_ok=True)
    target = "gui/" + str(os.getuid())
    previous = destination.read_bytes() if destination.exists() else None
    was_loaded = False
    if previous is not None:
        if plistlib.loads(previous).get("Label") != LABEL:
            parser.error("Existing file belongs to a different agent")
        status = subprocess.run(["launchctl", "print", target + "/" + LABEL], capture_output=True, text=True)
        # Make the backup before touching the installed job.
        (ROOT / "private/scheduler/previous-launchagent.plist").write_bytes(previous)
        if status.returncode == 0:
            was_loaded = True
            state = re.search(r"^\s*state = (.+)$", status.stdout, re.MULTILINE)
            if not state or state.group(1).strip() != "not running":
                parser.error("Scheduler is currently running; let collection finish before replacing it")
            subprocess.run(["launchctl", "bootout", target + "/" + LABEL], check=True)
    try:
        destination.write_bytes(plistlib.dumps(agent_config()))
        subprocess.run(["plutil", "-lint", str(destination)], check=True)
        subprocess.run(["launchctl", "bootstrap", target, str(destination)], check=True)
    except Exception as install_error:
        subprocess.run(["launchctl", "bootout", target + "/" + LABEL], capture_output=True, check=False)
        if previous is not None:
            destination.write_bytes(previous)
            if was_loaded:
                restored = subprocess.run(["launchctl", "bootstrap", target, str(destination)], check=False)
                if restored.returncode:
                    raise RuntimeError("Installation failed and the previous agent could not be reloaded; "
                                       "its plist was restored. Inspect launchctl status and reinstall.") from install_error
        else:
            destination.unlink(missing_ok=True)
        raise
    print("Installed " + str(destination))
    print("Browser-only schedule checked at each minute boundary; logs: private/scheduler/")


if __name__ == "__main__":
    main()
