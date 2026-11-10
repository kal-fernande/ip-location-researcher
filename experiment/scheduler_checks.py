"""Inspect macOS scheduling without collecting or advancing the study."""
from __future__ import annotations

from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import subprocess
import sys
import tempfile
import time
from zoneinfo import ZoneInfo

from scripts.install_scheduler import LABEL, agent_config
from .config import ROOT, load_config, load_questions, resolve_path
from .runner import _load_agenda, _study_days, _window
from .runlog import add_entry
from .study import apply_active_study


def _schedule_file(cfg) -> Path:
    return resolve_path(cfg["results"]["schedule_path"])


def _read_schedule(cfg):
    # Do not use load_schedule: it creates a schedule if one is missing.
    path = _schedule_file(cfg)
    if not path.is_file():
        raise ValueError(f"No schedule at {path}; run: python main.py start-study")
    schedule = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(schedule, list):
        raise ValueError("schedule.json must be a list")
    if _study_days(cfg) and not schedule:
        raise ValueError("No scheduled days; create the schedule before installing automation")
    days = set()
    for event in schedule:
        day = event["day"]
        if not isinstance(day, int) or isinstance(day, bool) or day < 1 or day in days:
            raise ValueError("Schedule day numbers must be unique positive integers")
        days.add(day)
        at = datetime.fromisoformat(event["run_at"])
        if at.utcoffset() is None:
            raise ValueError(f"Day {day} run_at needs a timezone offset")
        if sorted(event["location_order"]) != sorted(cfg["locations"]):
            raise ValueError(f"Day {day} location_order must match the configured locations")
        modes = event.get("completed_modes", [])
        if not isinstance(modes, list) or any(m not in ("api", "browser", "both") for m in modes):
            raise ValueError(f"Day {day} has invalid completed_modes")
    return schedule


def _configuration(cfg):
    _study_days(cfg)
    _window(cfg)
    ZoneInfo(cfg["experiment"]["timezone"])
    if cfg["experiment"].get("schedule_policy", "random") not in ("random", "agenda"):
        raise ValueError("Unknown schedule_policy")
    if cfg["experiment"].get("schedule_policy") == "agenda":
        _load_agenda(cfg, resolve_path(cfg["experiment"].get("agenda_path", "agenda.json")))
    if cfg["experiment"].get("recovery_policy", "skip") not in ("skip", "retry"):
        raise ValueError("Unknown recovery_policy")
    if cfg["experiment"].get("recovery_policy") == "retry":
        _window(cfg, "retry_window")
        attempts = cfg["experiment"].get("recovery", {}).get("max_attempts_per_cell", 3)
        if not isinstance(attempts, int) or isinstance(attempts, bool) or attempts < 1:
            raise ValueError("max_attempts_per_cell must be a positive integer")
    if not cfg.get("locations") or not cfg.get("languages"):
        raise ValueError("Locations and languages must be configured")
    if not cfg["browser"].get("enabled", True) or not any(
        p.get("enabled") for p in cfg["browser"]["providers"].values()
    ):
        raise ValueError("The installed browser scheduler needs an enabled browser provider")
    questions = load_questions()
    if not questions or not any(int(q["day"]) == 1 for q in questions):
        raise ValueError("questions.json needs day 1")
    for question in questions:
        for language in cfg["languages"]:
            if not isinstance(question.get(language), str) or not question[language].strip():
                raise ValueError(f"Question {question.get('question_id')} lacks {language}")
    _read_schedule(cfg)
    return "Configuration, translations, and saved schedule are valid; schedule not changed"


def _paths(cfg):
    expected = agent_config()
    python = Path(expected["ProgramArguments"][0])
    if not os.access(python, os.X_OK) or not (ROOT / "main.py").is_file():
        raise ValueError("Missing project Python or main.py; run source setup.sh")
    if not shutil.which("mullvad", path=expected["EnvironmentVariables"]["PATH"]):
        raise ValueError("Mullvad is not available on the LaunchAgent PATH")
    # Probe actual write access with temporary files; never alter study data.
    directories = {ROOT, ROOT / "private/scheduler", _schedule_file(cfg).parent}
    for key, fallback in (("csv_path", "results/results.csv"), ("index_path", "results/README.md"),
                          ("details_path", "results/results.md")):
        target = resolve_path(cfg["results"].get(key, fallback))
        if target.exists() and not os.access(target, os.W_OK):
            raise ValueError(f"Cannot write {target}")
        directories.add(target.parent)
    for key, fallback in (("runs_dir", "results/runs"), ("checks_dir", "results/checks"),
                          ("failure_screenshot_dir", "results/failures")):
        directories.add(resolve_path(cfg["results"].get(key, fallback)))
    for directory in directories:
        while not directory.exists():
            directory = directory.parent
        with tempfile.TemporaryFile(dir=directory) as stream:
            stream.write(b"scheduler preflight")
            stream.flush()
    return "Project Python, Mullvad PATH, and output write access verified in this process"


def _installed_agent():
    if sys.platform != "darwin":
        raise ValueError("LaunchAgent verification requires macOS; use cron/systemd on other systems")
    path = Path.home() / "Library/LaunchAgents" / (LABEL + ".plist")
    with path.open("rb") as stream:
        installed = plistlib.load(stream)
    expected = agent_config()
    mismatches = sorted(key for key in set(expected) | set(installed)
                        if installed.get(key) != expected.get(key))
    if mismatches:
        raise ValueError("Installed plist differs from installer: " + ", ".join(mismatches))
    return str(path) + " matches the project installer"


def _launchctl(*args):
    result = subprocess.run(["launchctl", *args], capture_output=True, text=True, timeout=10)
    if result.returncode:
        raise RuntimeError("launchctl failed; agent may be unloaded or inaccessible in this session: "
                           + result.stderr.strip()[:300])
    return result.stdout


def _status(label=LABEL):
    output = _launchctl("print", f"gui/{os.getuid()}/{label}")
    def field(name):
        match = re.search(r"^\s*" + re.escape(name) + r" = (.+)$", output, re.MULTILINE)
        return match.group(1).strip() if match else None
    state = field("state")
    if state is None:
        raise ValueError("Unrecognized launchctl output; cannot verify status")
    code = field("last exit code")
    status = {"state": state, "runs": int(field("runs") or 0),
              "last_exit_code": int(code) if code and re.fullmatch(r"-?\d+", code) else None}
    if field("pended nondemand spawn"):
        status["pending_reason"] = field("pended nondemand spawn")
    return status


def _loaded():
    status = _status()
    if status["state"] == "not running" and status["last_exit_code"] != 0:
        raise ValueError(f"Agent is loaded but last exit code is {status['last_exit_code']}; "
                         "inspect private/scheduler/stderr.log or run --probe")
    return status


def _probe(cfg):
    # A kickstart invokes the real run-due. Refuse when it might collect,
    # create a pending day, or advance the schedule.
    schedule = _read_schedule(cfg)
    now = datetime.now(ZoneInfo(cfg["experiment"]["timezone"]))
    days = _study_days(cfg)
    pending = [e for e in schedule if e["day"] <= days and "browser" not in e.get("completed_modes", [])]
    if not pending and days:
        raise ValueError("Probe requires a future pending browser day; it could otherwise draw the next day")
    if any(datetime.fromisoformat(e["run_at"]) <= now + timedelta(minutes=2) for e in pending):
        raise ValueError("Probe refused: a browser run is due or less than two minutes away")
    before = _status()
    if before["state"] != "not running":
        raise ValueError("Agent is already running; probe will not interrupt it")
    schedule_path = _schedule_file(cfg)
    schedule_before = schedule_path.read_bytes()
    csv_path = resolve_path(cfg["results"]["csv_path"])
    csv_before = csv_path.read_bytes() if csv_path.exists() else None
    _launchctl("kickstart", f"gui/{os.getuid()}/{LABEL}")
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        after = _status()
        if after["runs"] > before["runs"] and after["state"] == "not running":
            if after["last_exit_code"] != 0:
                raise ValueError(f"Fresh background launch exited {after['last_exit_code']}; inspect stderr.log")
            if schedule_path.read_bytes() != schedule_before or (
                csv_path.read_bytes() if csv_path.exists() else None
            ) != csv_before:
                raise ValueError("Live schedule or CSV changed during probe; inspect before continuing")
            return {**after, "live_schedule_unchanged": True, "live_csv_unchanged": True}
        time.sleep(0.25)
    raise TimeoutError("Background launch did not finish within 20 seconds; inspect launchctl status/logs")


def run_scheduler_check(*, probe=False):
    cfg = apply_active_study()
    started = datetime.now(ZoneInfo(cfg["experiment"]["timezone"]))
    diagnostics = []
    def step(name, action):
        try:
            detail = action()
            diagnostics.append({"name": name, "status": "ok", "detail": detail})
        except Exception as error:
            diagnostics.append({"name": name, "status": "error",
                                "detail": f"{type(error).__name__}: {error}"})
        print(f"{diagnostics[-1]['status'].upper()} {name}: {diagnostics[-1]['detail']}")
    step("configuration_and_schedule", lambda: _configuration(cfg))
    step("paths_and_output_access", lambda: _paths(cfg))
    step("installed_launchagent", _installed_agent)
    if diagnostics[-1]["status"] == "ok":
        step("launchd_status", _loaded)
    if probe:
        # An old nonzero exit is precisely what a fresh probe can resolve.
        prerequisites = diagnostics[:3]
        if all(d["status"] == "ok" for d in prerequisites):
            step("background_probe", lambda: _probe(cfg))
            if diagnostics[-1]["status"] == "ok":
                for d in diagnostics:
                    if d["name"] == "launchd_status":
                        d["previous_detail"] = d["detail"]
                        d.update(status="ok", detail=diagnostics[-1]["detail"])
        else:
            diagnostics.append({"name": "background_probe", "status": "error",
                                "detail": "Probe not attempted because prerequisites failed"})
    ok = all(d["status"] == "ok" for d in diagnostics)
    record = {"started_at": started.isoformat(),
              "finished_at": datetime.now(started.tzinfo).isoformat(),
              "command": "check-scheduler" + (" --probe" if probe else ""),
              "checks": ["scheduler", "launchagent"], "ok": ok,
              "diagnostics": diagnostics, "locations": []}
    directory = resolve_path(cfg["results"].get("checks_dir", "results/checks"))
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{started:%Y-%m-%d_%H%M%S_%f}_scheduler.json"
    path.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    add_entry(cfg, started, f"`{record['command']}` (scheduler, launchagent)",
              "PASS" if ok else "FAIL", path, anchor=path.stem)
    print(f"{'PASS' if ok else 'FAIL'} — written to {path}")
    return path, ok
