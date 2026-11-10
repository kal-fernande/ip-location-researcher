"""Read-only study summaries and subprocess support for the desktop interface."""
from __future__ import annotations

import codecs
import fcntl
import json
import os
import queue
import shutil
import signal
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent


def load_config():
    # The GUI may use a separate Python with modern Tk and only stdlib modules.
    from .config import load_config as project_load_config

    return project_load_config()


def find_desktop_python() -> str | None:
    """Find an existing Python 3.10+ with Tk 8.6+; never install or modify an environment."""
    candidates = [shutil.which(f"python3.{version}") for version in range(15, 9, -1)]
    candidates.append(shutil.which("python3"))
    # uv's managed Python installations may not be on Finder's PATH.
    candidates.extend(str(path) for path in sorted(
        (Path.home() / ".local/share/uv/python").glob("cpython-*-macos-*/bin/python3"), reverse=True,
    ))
    seen = {str(Path(sys.executable).resolve())}
    for candidate in candidates:
        if not candidate or str(Path(candidate).resolve()) in seen:
            continue
        seen.add(str(Path(candidate).resolve()))
        try:
            result = subprocess.run([
                candidate, "-c",
                "import sys, tkinter; sys.exit(0 if sys.version_info >= (3, 10) and tkinter.TkVersion >= 8.6 else 1)",
            ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            continue
        if result.returncode == 0:
            return candidate
    return None


def _cli_python() -> str:
    return os.environ.get("IP_LOCATION_CLI_PYTHON") or sys.executable


def _path(value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def alert_settings_request(action: str, values: dict | None = None) -> dict:
    """Exchange private alert settings through stdin, without logging secrets."""
    if action not in {"get", "save", "test"}:
        raise ValueError("Unknown phone settings action.")
    try:
        response = subprocess.run(
            [_cli_python(), "-u", "-m", "experiment.gui_worker", "--gui-alerts", action],
            input=json.dumps(values) if values is not None else "",
            cwd=str(ROOT), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=20,
        )
        if response.returncode:
            raise RuntimeError("The phone settings helper could not run. Try reopening the app.")
        payload = json.loads(response.stdout)
        if not isinstance(payload, dict) or not isinstance(payload.get("ok"), bool):
            raise ValueError("Invalid settings response")
        if payload["ok"]:
            if action == "test":
                if not isinstance(payload.get("message"), str):
                    raise ValueError("Missing test response")
            else:
                settings = payload.get("settings")
                keys = {
                    "enabled", "macos_notification", "provider", "ntfy_topic", "ntfy_server",
                    "telegram_bot_token", "telegram_chat_id", "advance_notice_minutes",
                }
                if not isinstance(settings, dict) or not keys <= settings.keys():
                    raise ValueError("Incomplete phone settings response")
                if (not isinstance(settings["enabled"], bool) or not isinstance(settings["macos_notification"], bool)
                        or not isinstance(settings["provider"], str)
                        or settings["provider"] not in {"none", "ntfy", "telegram", "webhook"}
                        or not isinstance(settings["ntfy_topic"], str) or not isinstance(settings["ntfy_server"], str)
                        or not isinstance(settings["telegram_bot_token"], str)
                        or not isinstance(settings["telegram_chat_id"], str)
                        or type(settings["advance_notice_minutes"]) is not int):
                    raise ValueError("Invalid phone settings response")
    except subprocess.TimeoutExpired:
        raise RuntimeError("Phone settings did not respond in time. Please try again.") from None
    except (OSError, ValueError):
        raise RuntimeError("Could not read the phone settings response. Please try again.") from None
    if not payload["ok"]:
        message = payload.get("error")
        raise RuntimeError(message if isinstance(message, str) and message else "Could not update phone settings.")
    return payload


def _collector_text() -> str:
    """Check the existing lock without creating it or trusting a stale PID."""
    lock_path = ROOT / "private" / "scheduler" / "collection.lock"
    try:
        with lock_path.open("r", encoding="utf-8") as handle:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_SH | fcntl.LOCK_NB)
            except BlockingIOError:
                return "Collection is running"
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except FileNotFoundError:
        pass
    except OSError:
        return "Collection status unavailable"
    return "No collection running"


def _dashboard_defaults() -> dict:
    return {
        "study_id": "",
        "study_name": "",
        "root": None,
        "csv_path": None,
        "xlsx_path": None,
        "next_event": None,
        "next_run_due": False,
        "next_run_text": "No run scheduled",
        "schedule_text": "",
        "collector_text": _collector_text(),
        "error": "",
        "days": 0,
        "providers": [],
        "timezone": "",
        "retry_days": [],
        "retry_error": "",
    }


def _delegated_dashboard(python: str) -> dict:
    result = _dashboard_defaults()
    try:
        response = subprocess.run(
            [python, "-u", "-m", "experiment.gui_worker", "--gui-dashboard"],
            cwd=str(ROOT), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=5,
        )
        if response.returncode:
            detail = response.stderr.strip() or f"project Python exited with code {response.returncode}"
            raise RuntimeError(detail[:1000])
        payload = json.loads(response.stdout)
        if not isinstance(payload, dict) or not result.keys() <= payload.keys():
            raise ValueError("Project Python returned an incomplete dashboard")
        if not isinstance(payload["providers"], list) or not isinstance(payload["error"], str):
            raise ValueError("Project Python returned an invalid dashboard")
        return payload
    except subprocess.TimeoutExpired:
        result["error"] = "Could not read the study setup: project Python did not respond within 5 seconds."
    except Exception as error:
        result["error"] = f"Could not read the study setup using project Python: {error}"
    result["next_run_text"] = "Next run unavailable"
    return result


def read_dashboard(*, now: datetime | None = None) -> dict:
    """Read current browser-study state without creating or updating any files."""
    python = _cli_python()
    if now is None and Path(python).resolve() != Path(sys.executable).resolve():
        return _delegated_dashboard(python)
    result = _dashboard_defaults()
    try:
        from .study import validate_study_id

        cfg = load_config()
        if not isinstance(cfg, dict):
            raise ValueError("config.yaml must contain a configuration mapping")
        experiment = cfg["experiment"]
        days = experiment["days"]
        if not isinstance(days, int) or isinstance(days, bool) or days < 0:
            raise ValueError("The study length must be a non-negative number of days")
        timezone = ZoneInfo(experiment["timezone"])
        result.update(days=days, timezone=str(timezone))
        browser = cfg.get("browser") or {}
        if browser.get("enabled", True):
            result["providers"] = [
                name for name, provider in (browser.get("providers") or {}).items()
                if provider.get("enabled", False)
            ]
        studies = _path((cfg.get("results") or {}).get("studies_dir", "results/studies"))
        pointer = studies / "ACTIVE"
        if not pointer.is_file():
            result["error"] = "No active study. Use Start a new study to create one."
            return result
        lines = pointer.read_text(encoding="utf-8").strip().splitlines()
        study_id = validate_study_id(lines[0].strip() if lines else "")
        root = studies / study_id
        result.update(
            study_id=study_id, root=root,
            csv_path=root / "results.csv", xlsx_path=root / "results.xlsx",
        )
        try:
            manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
            if isinstance(manifest.get("name"), str):
                result["study_name"] = manifest["name"]
        except (OSError, ValueError, AttributeError):
            pass
        schedule_path = root / "schedule.json"
        if not schedule_path.is_file():
            result["error"] = "The active study has no saved schedule. Check the study setup."
            return result
        schedule = json.loads(schedule_path.read_text(encoding="utf-8"))
        if not isinstance(schedule, list):
            raise ValueError("The saved schedule must be a list of study days")
        pending = []
        completed = set()
        for event in schedule:
            if not isinstance(event, dict):
                raise ValueError("The saved schedule contains an invalid study day")
            day = event.get("day")
            if not isinstance(day, int) or isinstance(day, bool) or day < 1:
                raise ValueError("Every scheduled study day must be a positive whole number")
            modes = event.get("completed_modes") or []
            if not isinstance(modes, list) or any(not isinstance(mode, str) for mode in modes):
                raise ValueError("The saved schedule contains invalid completion information")
            run_at = datetime.fromisoformat(event["run_at"])
            if run_at.tzinfo is None or run_at.utcoffset() is None:
                raise ValueError("Every scheduled run time must include its timezone")
            if day > days:
                continue
            if "browser" in modes or "both" in modes:
                completed.add(day)
            else:
                pending.append((run_at, event))
        result["schedule_text"] = f"{len(completed)} of {days} browser study days finished"
        if result["collector_text"] == "Collection is running":
            result["retry_error"] = "A collection is still running. Wait for it to finish before retrying."
        else:
            try:
                from .manual_retry import retry_options
                from .study import apply_study

                bound_cfg = {**cfg, "results": {**cfg.get("results", {}), "studies_dir": str(studies)}}
                result["retry_days"] = retry_options(cfg=apply_study(bound_cfg, study_id), now=now, include_complete=True)
            except Exception as error:
                result["retry_error"] = f"Could not check missed answers: {error}"
        if not pending:
            result["next_run_text"] = (
                "Study schedule finished" if len(completed) >= days
                else "No pending browser run in the saved schedule"
            )
            return result
        run_at, event = min(pending, key=lambda item: item[0])
        if now is None:
            now = datetime.now(timezone)
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("The current time must include its timezone")
        due = run_at <= now
        local = run_at.astimezone(timezone)
        clock = f"{local.hour % 12 or 12}:{local.minute:02d} {'AM' if local.hour < 12 else 'PM'}"
        result.update(
            next_event=dict(event),
            next_run_due=due,
            next_run_text=(
                f"Day {event['day']} · {local:%A, %B} {local.day}, {local.year} at "
                f"{clock} {local:%Z} ({timezone}) · {'Due now' if due else 'Upcoming'}"
            ),
        )
    except Exception as error:
        result["error"] = f"Could not read the study setup: {error}"
        result["next_event"] = None
        result["next_run_due"] = False
        result["next_run_text"] = "Next run unavailable"
    return result


class TaskRunner:
    """Run one CLI task with streaming output while the GUI remains responsive."""

    def __init__(self):
        self.events: queue.Queue = queue.Queue()
        self._state_lock = threading.Lock()
        self._process: subprocess.Popen | None = None
        self._running = False
        self._cancelled = False
        self._stop_sent = False
        self._input_pending = False

    @property
    def running(self) -> bool:
        with self._state_lock:
            return self._running

    @property
    def cancelled(self) -> bool:
        with self._state_lock:
            return self._cancelled

    def start(self, args: list[str], *, script: str = "main.py") -> None:
        if not isinstance(args, list) or any(not isinstance(arg, str) for arg in args):
            raise TypeError("Task arguments must be a list of strings")
        with self._state_lock:
            if self._running:
                raise RuntimeError("A task is already running. Let it finish or stop it first.")
            self._running = True
            self._cancelled = False
            self._stop_sent = False
            self._input_pending = False
            self._process = None
        worker = threading.Thread(
            target=self._run, args=(list(args), script), name="gui-cli-task", daemon=True,
        )
        worker.start()

    def _signal_stop_locked(self) -> None:
        if self._process is None or self._stop_sent:
            return
        try:
            self._process.send_signal(signal.SIGINT)
            self._stop_sent = True
        except ProcessLookupError:
            pass

    def stop(self) -> None:
        """Interrupt this runner's child only, including a child still starting."""
        with self._state_lock:
            if not self._running:
                return
            self._cancelled = True
            self._signal_stop_locked()

    def send_input(self, text: str) -> None:
        """Queue one input delivery without blocking the GUI on a full pipe."""
        with self._state_lock:
            process = self._process
            if process is None or process.poll() is not None or process.stdin is None:
                raise RuntimeError("There is no running task waiting for input.")
            if self._input_pending:
                raise RuntimeError("The previous input is still being delivered. Please wait before sending more.")
            self._input_pending = True
        writer = threading.Thread(
            target=self._write_input, args=(process, text), name="gui-cli-input", daemon=True,
        )
        try:
            writer.start()
        except Exception:
            with self._state_lock:
                if self._process is process:
                    self._input_pending = False
            raise

    def _write_input(self, process: subprocess.Popen, text: str) -> None:
        event = ("input_sent", "Input delivered.")
        try:
            remaining = memoryview(text.encode("utf-8"))
            while remaining:
                written = process.stdin.write(remaining)
                if not written:
                    raise BrokenPipeError("The task stopped accepting input")
                remaining = remaining[written:]
            process.stdin.flush()
        except (BrokenPipeError, OSError, ValueError) as error:
            event = ("input_error", f"The task stopped accepting input: {error}")
        finally:
            with self._state_lock:
                if self._process is process:
                    self._input_pending = False
                    self.events.put(event)

    def _run(self, args: list[str], script: str) -> None:
        process = None
        returncode = 1
        try:
            script_path = _path(script)
            python = _cli_python()
            if script_path == ROOT / "main.py":
                command = [python, "-u", "-m", "experiment.gui_worker", *args]
            else:
                command = [python, "-u", str(script_path), *args]
            environment = os.environ.copy()
            paths = [
                str(Path(python).parent), "/opt/homebrew/bin", "/usr/local/bin",
                "/usr/bin", "/bin", "/usr/sbin", "/sbin",
                *environment.get("PATH", "").split(os.pathsep),
            ]
            environment["PATH"] = os.pathsep.join(dict.fromkeys(path for path in paths if path))
            environment["PYTHONUNBUFFERED"] = "1"
            environment["PYTHONIOENCODING"] = "utf-8"
            process = subprocess.Popen(
                command, cwd=str(ROOT), env=environment,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                bufsize=0, shell=False,
            )
            with self._state_lock:
                self._process = process
                if self._cancelled:
                    self._signal_stop_locked()
            decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
            while True:
                chunk = process.stdout.read(4096)
                if not chunk:
                    break
                output = decoder.decode(chunk)
                if output:
                    self.events.put(("output", output))
            tail = decoder.decode(b"", final=True)
            if tail:
                self.events.put(("output", tail))
            returncode = process.wait()
        except Exception as error:
            self.events.put(("error", f"Could not run the task: {error}"))
            # Preserve ownership until the child exits, including when output
            # reading fails. Its signal handlers can save results and disconnect.
            if process is not None:
                with self._state_lock:
                    self._signal_stop_locked()
                returncode = process.wait()
                if returncode == 0:
                    returncode = 1
        finally:
            if process is not None:
                for stream in (process.stdin, process.stdout):
                    if stream is not None:
                        stream.close()
            with self._state_lock:
                self._process = None
                self._running = False
                self._input_pending = False
                self.events.put(("done", returncode))
