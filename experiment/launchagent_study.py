"""Run the study smoke test through an unforced macOS calendar launch."""
from __future__ import annotations

from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import plistlib
import signal
import sys
import time
from zoneinfo import ZoneInfo

from scripts.install_scheduler import LABEL, agent_config
from .config import ROOT, load_config, resolve_path
from .runlog import add_entry
from .scheduler_checks import _launchctl, _status

DELAY_SECONDS = 60
START_GRACE_SECONDS = 90
COLLECTION_TIMEOUT_SECONDS = 3600


def _save(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def _read(path):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def _snapshot(path):
    return path.read_bytes() if path.exists() else None


def run_worker(directory):
    """Internal CLI entry point. Only launchd starts this during a delayed check."""
    from .checks import run_browser_study_check
    cfg = load_config()
    sandbox = Path(directory).resolve()
    allowed = resolve_path(cfg["results"].get("checks_dir", "results/checks")) / "study-smoke"
    if not sandbox.is_relative_to(allowed.resolve()):
        raise ValueError("Worker directory must be inside checks_dir/study-smoke")
    manifest = _read(sandbox / "launch-request.json")
    if not manifest:
        raise ValueError("Missing delayed-check request")
    started = datetime.now(ZoneInfo(cfg["experiment"]["timezone"]))
    # A calendar tick can arrive less than 60 seconds after installation.
    # Wait for the next tick without locking out the real collection attempt.
    if started < datetime.fromisoformat(manifest["not_before"]):
        print("Early minute tick; collection waits until " + manifest["not_before"], flush=True)
        return 0
    # The calendar trigger can fire again after completion. Never repeat collection.
    try:
        with (sandbox / "worker.lock").open("x"):
            pass
    except FileExistsError:
        return 0
    _save(sandbox / "worker-started.json", {"started_at": started.isoformat(), "pid": os.getpid()})
    def cancelled(signum, frame):
        raise SystemExit("Temporary LaunchAgent test was cancelled")
    old_handler = signal.signal(signal.SIGTERM, cancelled)
    result = {"ok": False}
    try:
        path, ok = run_browser_study_check(sandbox=sandbox, log_index=False)
        result.update(ok=ok, summary=str(path))
    except BaseException as error:
        result["error"] = f"{type(error).__name__}: {error}"
        print(result["error"], flush=True)
    finally:
        signal.signal(signal.SIGTERM, old_handler)
        result["finished_at"] = datetime.now(started.tzinfo).isoformat()
        _save(sandbox / "worker-result.json", result)
    return 0 if result["ok"] else 1


def _guard_live_run(cfg):
    # Keep this test's VPN work away from an actual study invocation.
    schedule = _read(ROOT / "schedule.json") or []
    latest_safe = datetime.now(ZoneInfo(cfg["experiment"]["timezone"])) + timedelta(
        seconds=DELAY_SECONDS + START_GRACE_SECONDS + COLLECTION_TIMEOUT_SECONDS,
    )
    for event in schedule:
        if event["day"] <= cfg["experiment"]["days"] and "browser" not in event.get("completed_modes", []):
            if datetime.fromisoformat(event["run_at"]) <= latest_safe:
                raise ValueError("Live browser collection is due within the test's one-hour timeout; run this check later")
    try:
        status = _status()
    except RuntimeError:
        # The real agent need not be installed to test an isolated one.
        return
    if status["state"] != "not running":
        raise ValueError("The live LaunchAgent is running; do not overlap VPN collectors")


def _temporary_agent(sandbox, label, log_dir):
    agent = agent_config()
    agent.update({
        "Label": label,
        "ProgramArguments": agent["ProgramArguments"][:2] + [
            str(ROOT / "main.py"), "check-browser-study", "--launchagent-worker", str(sandbox),
        ],
        "RunAtLoad": False,
        "StandardOutPath": str(log_dir / "stdout.log"),
        "StandardErrorPath": str(log_dir / "stderr.log"),
    })
    return agent


def _wait_for_worker(sandbox, label, log_dir):
    deadline = time.monotonic() + DELAY_SECONDS + START_GRACE_SECONDS
    positions = 0
    last_notice = 0
    while True:
        now = time.monotonic()
        started = _read(sandbox / "worker-started.json")
        if started and not (sandbox / "start-observed.json").exists():
            _save(sandbox / "start-observed.json", started)
            deadline = now + COLLECTION_TIMEOUT_SECONDS
            print("macOS started the test automatically at " + started["started_at"], flush=True)
        output = log_dir / "stdout.log"
        if output.exists():
            contents = output.read_text(encoding="utf-8", errors="replace")
            if len(contents) > positions:
                print(contents[positions:], end="", flush=True)
                positions = len(contents)
        result = _read(sandbox / "worker-result.json")
        status = _status(label)
        if status["runs"] and status["state"] == "not running":
            if not result and (started or status["last_exit_code"] != 0):
                raise RuntimeError(f"Background worker exited {status['last_exit_code']}; inspect {log_dir / 'stderr.log'}")
            if result:
                return started, result, status
        if now >= deadline:
            reason = ("Automatic start was not observed within 150 seconds" if not started
                      else "Background collection exceeded the one-hour timeout")
            raise TimeoutError(f"{reason}; last launchd status: {status}")
        if not started and now - last_notice >= 15:
            print("Waiting for macOS's one-minute timer; no manual launch is used...", flush=True)
            last_notice = now
        time.sleep(1)


def run_delayed_study_check():
    cfg = load_config()
    started = datetime.now(ZoneInfo(cfg["experiment"]["timezone"]))
    stamp = f"{started:%Y-%m-%d_%H%M%S_%f}"
    checks_dir = resolve_path(cfg["results"].get("checks_dir", "results/checks"))
    sandbox = checks_dir / "study-smoke" / (stamp + "_launchagent")
    sandbox.mkdir(parents=True, exist_ok=True)
    label = LABEL + ".study-smoke." + stamp
    log_dir = ROOT / "private/scheduler" / ("study-smoke-" + stamp)
    log_dir.mkdir(parents=True, exist_ok=True)
    plist = log_dir / (label + ".plist")
    target = f"gui/{os.getuid()}/{label}"
    live_schedule = ROOT / "schedule.json"
    live_csv = resolve_path(cfg["results"]["csv_path"])
    before = (_snapshot(live_schedule), _snapshot(live_csv))
    errors = []
    bootstrap_attempted = False
    cleanup_ok = True
    actual_start = None
    child = None
    status = None
    try:
        if sys.platform != "darwin":
            raise ValueError("--launchagent-delay requires macOS")
        _guard_live_run(cfg)
        not_before = datetime.now(started.tzinfo) + timedelta(seconds=DELAY_SECONDS)
        _save(sandbox / "launch-request.json", {"label": label, "requested_at": started.isoformat(),
                                               "not_before": not_before.isoformat(), "delay_seconds": DELAY_SECONDS})
        plist.write_bytes(plistlib.dumps(_temporary_agent(sandbox, label, log_dir)))
        bootstrap_attempted = True
        _launchctl("bootstrap", f"gui/{os.getuid()}", str(plist))
        print(f"Temporary LaunchAgent loaded. Earliest automatic start: {not_before.isoformat()}", flush=True)
        print(f"Test outputs: {sandbox}\nPrivate logs: {log_dir}", flush=True)
        actual_start, child, status = _wait_for_worker(sandbox, label, log_dir)
        if datetime.fromisoformat(actual_start["started_at"]) < not_before:
            errors.append("Worker started before the requested delay")
        if not child["ok"]:
            errors.append(child.get("error", "Background study smoke test failed; inspect its check summary"))
        if status["last_exit_code"] != 0:
            errors.append(f"Background worker exited {status['last_exit_code']}")
    except BaseException as error:
        errors.append(f"{type(error).__name__}: {error}")
    finally:
        if bootstrap_attempted:
            if status is None:
                try:
                    status = _status(label)
                except Exception:
                    pass
            try:
                _launchctl("bootout", target)
                plist.unlink(missing_ok=True)
            except Exception as error:
                cleanup_ok = False
                errors.append(f"Cleanup failed: {error}. Unload manually with: launchctl bootout {target}")
    unchanged = (_snapshot(live_schedule), _snapshot(live_csv)) == before
    if not unchanged:
        errors.append("Live schedule or CSV changed during the test; inspect before continuing")
    try:
        summary = _read(sandbox / "check-summary.json") or {}
        actual_start = actual_start or _read(sandbox / "worker-started.json")
    except (OSError, ValueError) as error:
        summary = {}
        errors.append(f"Cannot read worker summary: {error}")
    steps = summary.get("steps", {})
    request = _read(sandbox / "launch-request.json") or {}
    steps.update({"sandbox": str(sandbox.relative_to(ROOT)), "launchagent": {
        "label": label, "delay_seconds": DELAY_SECONDS,
        "trigger": "StartCalendarInterval, every minute",
        "not_before": request.get("not_before"),
        "actual_start": actual_start, "exit_status": status,
        "cleanup_ok": cleanup_ok, "live_files_unchanged": unchanged,
        "private_logs": str(log_dir), "manual_launch": False,
    }})
    record = {"started_at": started.isoformat(), "finished_at": datetime.now(started.tzinfo).isoformat(),
              "command": "check-browser-study --launchagent-delay",
              "checks": ["launchagent", "timer", "vpn", "browser", "study-pipeline"],
              "ok": not errors and bool(child and child["ok"]), "steps": steps,
              "errors": errors, "locations": summary.get("locations", [])}
    path = checks_dir / f"{stamp}_launchagent-study.json"
    _save(path, record)
    _save(sandbox / "launchagent-summary.json", record)
    extra = [sandbox / "results.csv"] if (sandbox / "results.csv").exists() else []
    first_run = steps.get("first_run", {}).get("run_json")
    if first_run and (sandbox / "runs" / first_run).exists():
        extra.append(sandbox / "runs" / first_run)
    add_entry(cfg, started, "`check-browser-study --launchagent-delay` (launchagent, timer, browser, study-pipeline)",
              "PASS" if record["ok"] else "FAIL", path, anchor=path.stem, extra_files=extra)
    print(f"{'PASS' if record['ok'] else 'FAIL'} — written to {path}", flush=True)
    for error in errors:
        print("  - " + error, flush=True)
    return path, record["ok"]
