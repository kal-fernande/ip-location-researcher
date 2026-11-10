"""Researcher-started browser recovery after a scheduled run was missed."""
from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path

from . import runner
from .config import resolve_path
from .run_lock import collection_lock
from .storage import observation_key


def _aware_time(value: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError("Saved run times must be text with a timezone.")
    moment = datetime.fromisoformat(value)
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError("Saved run times must include a timezone.")
    return moment


def _schedule(cfg: dict) -> tuple[Path, list[dict]]:
    path = runner.schedule_path_for_cfg(cfg)
    if not path.is_file():
        raise ValueError("There is no saved study schedule to retry.")
    schedule = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(schedule, list):
        raise ValueError("The saved study schedule is invalid.")
    seen = set()
    for event in schedule:
        day = event.get("day") if isinstance(event, dict) else None
        if type(day) is not int or day < 1 or day in seen:
            raise ValueError("The saved schedule needs unique positive study days.")
        seen.add(day)
        _aware_time(event["run_at"])
        modes = event.get("completed_modes", [])
        if not isinstance(modes, list) or any(not isinstance(mode, str) for mode in modes):
            raise ValueError("The saved schedule has invalid completion information.")
        if sorted(event.get("location_order") or []) != sorted(cfg["locations"]):
            raise ValueError("The saved location order must list every study location.")
    return path, schedule


def _successful_keys(path: Path) -> set[tuple]:
    if not path.exists() or not path.stat().st_size:
        return set()
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle, strict=True)
        required = {"day", "question_id", "collector", "provider", "language", "vpn_location", "status"}
        if not required <= set(reader.fieldnames or []):
            raise ValueError("The study CSV is missing columns needed to safely retry answers.")
        successful = set()
        for row in reader:
            if None in row or any(not row[key] for key in required):
                raise ValueError("The study CSV has an incomplete row. Check it before retrying.")
            if row["status"] == "ok":
                successful.add(observation_key(row))
        return successful


def retry_options(*, cfg: dict | None = None, now: datetime | None = None, include_complete: bool = False) -> list[dict]:
    """Read due browser days; optionally include complete days for manual access."""
    cfg = runner._live_cfg(cfg)
    _, schedule = _schedule(cfg)
    now = now or runner._now(cfg)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("The current time must include a timezone.")
    successful = _successful_keys(resolve_path(cfg["results"]["csv_path"]))
    original_times = {event["day"]: _aware_time(event["run_at"]) for event in schedule}
    # Automatic retry may have moved run_at into the future. A saved attempt
    # retains the original due time, so that day can still be recovered now.
    runs_dir = resolve_path(cfg["results"].get("runs_dir", "results/runs"))
    for path in runs_dir.glob("*.json"):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            day = record.get("day")
            if day not in original_times or record.get("mode") not in {"browser", "both"}:
                continue
            planned = _aware_time(record["run_at"])
            started = _aware_time(record["started_at"])
            if planned <= started <= now:
                original_times[day] = min(original_times[day], planned)
        except (OSError, TypeError, ValueError, KeyError, AttributeError):
            continue
    options = []
    for event in schedule:
        day = event["day"]
        planned = original_times[day]
        if day > runner._study_days(cfg) or planned > now:
            continue
        expected = runner._expected_keys(cfg, day, "browser")
        missing = expected - successful
        if expected and (missing or include_complete):
            options.append({
                "day": day, "run_at": planned.isoformat(),
                "scheduled_run_at": event["run_at"],
                "missing": len(missing), "successful": len(expected & successful), "total": len(expected),
            })
    return sorted(options, key=lambda option: option["day"], reverse=True)


def retry_day(day: int, *, cfg: dict | None = None, study_id: str | None = None, log_index: bool = True) -> dict:
    """Retry failed/missing cells once, preserving successes and scheduled times."""
    if type(day) is not int or day < 1:
        raise ValueError("Choose a positive study day to retry.")
    with collection_lock(blocking=False):
        cfg = runner._live_cfg(cfg)
        if study_id is not None and cfg["results"].get("study_id") != study_id:
            raise ValueError("The active study changed. Refresh the window and choose the day again.")
        option = next((item for item in retry_options(cfg=cfg, include_complete=True) if item["day"] == day), None)
        if option is None:
            raise ValueError("This day has no due browser run. Refresh the window and choose a due study day.")
        if option["missing"] == 0:
            print(f"Day {day} is already complete. No answers were collected.", flush=True)
            return {"ok": True, "no_op": True, "day": day}
        path, schedule = _schedule(cfg)
        print(f"Retrying missed browser answers for day {day}: {option['missing']} of {option['total']} remaining.", flush=True)
        record = runner.run_day(
            day, "browser", cfg=cfg, schedule_path=path, log_index=log_index,
            manual_recovery={
                "original_run_at": option["run_at"], "scheduled_run_at": option["scheduled_run_at"],
                "missing_before": option["missing"], "successful_before": option["successful"],
            },
        )
        expected = runner._expected_keys(cfg, day, "browser")
        missing = expected - _successful_keys(resolve_path(cfg["results"]["csv_path"]))
        # A background scheduler may have recorded an advance notification while
        # collection was running; retain its latest event metadata.
        _, schedule = _schedule(cfg)
        event = next(event for event in schedule if event["day"] == day)
        event.setdefault("manual_recoveries", []).append({
            "started_at": record["started_at"], "finished_at": record["finished_at"],
            "missing_before": option["missing"], "missing_after": len(missing),
        })
        if not missing:
            if "browser" not in event.get("completed_modes", []):
                event.setdefault("completed_modes", []).append("browser")
            event.pop("retry_count", None)
        runner.save_schedule(schedule, path)
        if not missing:
            runner._schedule_next(schedule, cfg, "browser", path)
            print(f"Manual retry finished: day {day} now has all {len(expected)} browser answers.", flush=True)
        else:
            print(f"Manual retry finished with gaps: day {day} still has {len(missing)} missing or failed browser answers.", flush=True)
        return record
