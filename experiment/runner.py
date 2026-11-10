\
from __future__ import annotations
import csv
import json
import random
import time
import uuid
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from .config import load_config, load_questions, resolve_path, ROOT
from .vpn import (
    VpnSessionAborted,
    VpnSessionTimeout,
    connect_vpn,
    disconnect_vpn,
    raise_if_aborted,
    vpn_session,
)
from .storage import append_result, ok_keys, retry_history, skip_keys
from .api_collectors import API_ADAPTERS
from .browser_collectors import collect_browser
from .runlog import add_entry
from .study import NoActiveStudy, apply_active_study, is_study_stopped, schedule_path_for_cfg

def _now(cfg):
    return datetime.now(ZoneInfo(cfg["experiment"]["timezone"]))

def _live_cfg(cfg: dict | None = None) -> dict:
    """Config with paths pointed at the active study folder."""
    return cfg if cfg is not None else apply_active_study()

def default_schedule_path() -> Path:
    """Schedule file for the active study (results/studies/<id>/schedule.json)."""
    return schedule_path_for_cfg(apply_active_study())

def _schedule_policy(cfg) -> str:
    return str(cfg.get("experiment", {}).get("schedule_policy") or "random").lower()

def _recovery_policy(cfg) -> str:
    return str(cfg.get("experiment", {}).get("recovery_policy") or "skip").lower()

def _max_attempts(cfg) -> int:
    recovery = cfg.get("experiment", {}).get("recovery") or {}
    value = recovery.get("max_attempts_per_cell", 3)
    return int(value) if value else 3

def create_schedule(
    start_date: str | None = None,
    seed: int | None = None,
    *,
    cfg: dict | None = None,
    schedule_path: Path | None = None,
    agenda_path: str | Path | None = None,
) -> list[dict]:
    cfg = _live_cfg(cfg)
    schedule_path = schedule_path or schedule_path_for_cfg(cfg)
    if agenda_path is not None or _schedule_policy(cfg) == "agenda":
        path = Path(agenda_path) if agenda_path is not None else resolve_path(
            cfg["experiment"].get("agenda_path", "agenda.json")
        )
        schedule = _load_agenda(cfg, path)
        save_schedule(schedule, schedule_path)
        return schedule

    rng = random.Random(seed)
    tz = ZoneInfo(cfg["experiment"]["timezone"])
    now = datetime.now(tz)

    if start_date:
        base = datetime.fromisoformat(start_date).replace(tzinfo=tz)
    else:
        # Default: start tomorrow, avoiding "today's random time already passed" ambiguity.
        tomorrow = (now + timedelta(days=1)).date()
        base = datetime.combine(tomorrow, datetime.min.time(), tzinfo=tz)

    schedule = [_new_event(cfg, 1, base.date(), rng)] if _study_days(cfg) else []
    save_schedule(schedule, schedule_path)
    return schedule


def _load_agenda(cfg, path: Path) -> list[dict]:
    if not path.exists():
        raise FileNotFoundError(
            f"Agenda file not found: {path}. Copy agenda.example.json or pass --agenda."
        )
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list) or not raw:
        raise ValueError("agenda must be a non-empty JSON list of day entries")
    days = _study_days(cfg)
    schedule = []
    for item in raw:
        day = int(item["day"])
        if day > days:
            continue
        order = item.get("location_order") or list(cfg["locations"].keys())
        if sorted(order) != sorted(cfg["locations"].keys()):
            raise ValueError(f"agenda day {day}: location_order must list every configured location")
        schedule.append({
            "day": day,
            "run_at": item["run_at"],
            "location_order": list(order),
            "completed_modes": list(item.get("completed_modes") or []),
        })
    if not schedule:
        raise ValueError("agenda has no days within experiment.days")
    schedule.sort(key=lambda e: e["day"])
    return schedule


def _study_days(cfg) -> int:
    days = cfg["experiment"]["days"]
    if not isinstance(days, int) or isinstance(days, bool) or days < 0:
        raise ValueError("experiment.days must be a non-negative integer")
    return days


def _window(cfg, key: str = "schedule_window") -> tuple[int, int]:
    if key == "retry_window":
        window = (cfg.get("experiment", {}).get("recovery") or {}).get("retry_window")
        if not window:
            window = cfg["experiment"]["schedule_window"]
    else:
        window = cfg["experiment"]["schedule_window"]
    start, end = window["start_hour"], window["end_hour"]
    if not (isinstance(start, int) and isinstance(end, int) and 0 <= start <= end <= 23):
        raise ValueError(f"{key} hours must satisfy 0 <= start_hour <= end_hour <= 23")
    return start, end


def _new_event(cfg, day, date, rng=None) -> dict:
    rng = rng or random.Random()
    start, end = _window(cfg, "schedule_window")
    minute = rng.randrange(start * 60, (end + 1) * 60)
    tz = ZoneInfo(cfg["experiment"]["timezone"])
    run_at = datetime.combine(date, datetime.min.time(), tzinfo=tz).replace(
        hour=minute // 60, minute=minute % 60,
    )
    return {
        "day": day,
        "run_at": run_at.isoformat(),
        "location_order": rng.sample(list(cfg["locations"]), k=len(cfg["locations"])),
        "completed_modes": [],
    }


def _retry_run_at(cfg, rng=None) -> datetime:
    """Random time later today inside the retry window, or tomorrow if needed."""
    rng = rng or random.Random()
    start, end = _window(cfg, "retry_window")
    now = _now(cfg)
    tz = now.tzinfo
    earliest_minute = now.hour * 60 + now.minute + 15  # at least ~15 minutes out
    window_start = start * 60
    window_end = (end + 1) * 60
    date = now.date()
    low = max(window_start, earliest_minute)
    if low >= window_end:
        date = date + timedelta(days=1)
        low = window_start
    minute = rng.randrange(low, window_end)
    return datetime.combine(date, datetime.min.time(), tzinfo=tz).replace(
        hour=minute // 60, minute=minute % 60,
    )


def _expected_keys(cfg, day: int, mode: str) -> set[tuple]:
    question = _question_for_day(day)
    keys: set[tuple] = set()
    for vpn_location in cfg["locations"]:
        for language in cfg["languages"]:
            if mode in ("api", "both") and cfg["api"].get("enabled", True):
                for provider, pcfg in cfg["api"]["providers"].items():
                    if pcfg.get("enabled", False):
                        keys.add((str(day), question["question_id"], "api",
                                  provider, language, vpn_location))
            if mode in ("browser", "both") and cfg["browser"].get("enabled", True):
                for provider, pcfg in cfg["browser"]["providers"].items():
                    if pcfg.get("enabled", False):
                        keys.add((str(day), question["question_id"], "browser",
                                  provider, language, vpn_location))
    return keys


def _day_complete_for_mode(cfg, day: int, mode: str, csv_path: Path) -> bool:
    expected = _expected_keys(cfg, day, mode)
    if not expected:
        return False
    filled = skip_keys(
        csv_path,
        recovery_policy=_recovery_policy(cfg),
        max_attempts_per_cell=_max_attempts(cfg),
    )
    return expected <= filled


def _schedule_next(schedule, cfg, mode, schedule_path: Path | None = None):
    """Keep history and at most one upcoming day for the selected mode."""
    days = _study_days(cfg)
    if any(e["day"] <= days and mode not in e.get("completed_modes", []) for e in schedule):
        return
    completed = [e for e in schedule if mode in e.get("completed_modes", [])]
    last = max(completed, key=lambda e: e["day"], default=None)
    next_day = last["day"] + 1 if last else 1
    if next_day > days:
        return
    if any(int(e["day"]) == next_day for e in schedule):
        save_schedule(schedule, schedule_path)
        return
    if _schedule_policy(cfg) == "agenda":
        agenda = _load_agenda(cfg, resolve_path(cfg["experiment"].get("agenda_path", "agenda.json")))
        event = next((e for e in agenda if int(e["day"]) == next_day), None)
        if event is None:
            return
        schedule.append({**event, "completed_modes": []})
        save_schedule(schedule, schedule_path)
        return
    # Anchor to actual completion: after sleep/downtime, do not bunch missed runs.
    date = _now(cfg).date() + timedelta(days=1)
    if last:
        date = max(date, datetime.fromisoformat(last["run_at"]).date() + timedelta(days=1))
    schedule.append(_new_event(cfg, next_day, date))
    save_schedule(schedule, schedule_path)

def load_schedule(schedule_path: Path | None = None, *, cfg: dict | None = None) -> list[dict]:
    if schedule_path is None:
        cfg = _live_cfg(cfg)
        schedule_path = schedule_path_for_cfg(cfg)
    path = schedule_path
    if not path.exists():
        return create_schedule(cfg=cfg, schedule_path=path)
    return json.loads(path.read_text(encoding="utf-8"))

def save_schedule(schedule: list[dict], schedule_path: Path | None = None, *, cfg: dict | None = None) -> None:
    if schedule_path is None:
        cfg = _live_cfg(cfg)
        schedule_path = schedule_path_for_cfg(cfg)
    path = schedule_path
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(schedule, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)

def _question_for_day(day: int) -> dict:
    questions = load_questions()
    for q in questions:
        if int(q["day"]) == int(day):
            return q
    if day < 1 or not questions:
        raise ValueError(f"No question configured for day {day}")
    # Reuse the question sequence when extending the study beyond its templates.
    return sorted(questions, key=lambda q: int(q["day"]))[(day - 1) % len(questions)]

def run_day(
    day: int,
    mode: str = "both",
    force: bool = False,
    *,
    cfg: dict | None = None,
    schedule_path: Path | None = None,
    log_index: bool = True,
    manual_recovery: dict | None = None,
) -> dict:
    """Collect one study day. Returns the run record written to runs_dir.

    `cfg` and `schedule_path` let checks use a sandbox without touching the live
    study files. `log_index=False` skips the results/README.md row (used by the
    study smoke check, which logs once as a check).
    """
    cfg = _live_cfg(cfg)
    if is_study_stopped(cfg):
        raise ValueError("This study has been stopped. Create a new study to collect again.")
    schedule_path = schedule_path or schedule_path_for_cfg(cfg)
    question = _question_for_day(day)
    csv_path = resolve_path(cfg["results"]["csv_path"])
    screenshot_dir = resolve_path(cfg["results"]["failure_screenshot_dir"])
    if manual_recovery is not None:
        seen = ok_keys(csv_path)
        retry_times = retry_history(csv_path, resolve_path(cfg["results"].get("runs_dir", "results/runs")))
    else:
        seen = set() if force else skip_keys(
            csv_path,
            recovery_policy=_recovery_policy(cfg),
            max_attempts_per_cell=_max_attempts(cfg),
        )
    started = _now(cfg)
    rows_before = _count_rows(csv_path)
    error = None

    # Pull randomized order from schedule if available; otherwise randomize now.
    schedule = load_schedule(schedule_path, cfg=cfg)
    event = next((e for e in schedule if int(e["day"]) == int(day)), None)
    location_order = event["location_order"] if event else random.sample(
        list(cfg["locations"].keys()), k=len(cfg["locations"])
    )

    record = {
        "started_at": started.isoformat(),
        "command": f"run-day --day {day} --mode {mode}" + (" --force" if force else ""),
        "checks": ["vpn"] + (["api"] if mode in ("api", "both") else [])
                  + (["browser"] if mode in ("browser", "both") else []),
        "day": day,
        "mode": mode,
        "run_at": event["run_at"] if event else None,
        "locations": [],
    }
    if manual_recovery is not None:
        record["command"] = f"retry-day --day {day}"
        record["trigger"] = "manual_recovery"
        record["manual_recovery"] = manual_recovery
        record["run_at"] = manual_recovery["original_run_at"]
    try:
        with vpn_session(cfg):
            for vpn_location in location_order:
                raise_if_aborted()
                loc_cfg = cfg["locations"][vpn_location]
                print(f"\n=== VPN -> {vpn_location} ===")
                entry = {"location": vpn_location, "vpn": {"verified": False},
                         "api": [], "browser": [], "skipped": []}
                record["locations"].append(entry)
                vpn_state = connect_vpn(cfg, vpn_location, loc_cfg)
                entry["vpn"] = asdict(vpn_state)
                entry["vpn"].pop("raw", None)
                print(
                    f"exit_ip={vpn_state.exit_ip} "
                    f"detected={vpn_state.detected_country_code} "
                    f"verified={vpn_state.verified}"
                )

                if not vpn_state.verified:
                    raise RuntimeError(
                        f"VPN verification failed for {vpn_location}: "
                        f"expected {vpn_state.expected_country_code}, "
                        f"got {vpn_state.detected_country_code}. "
                        f"error={vpn_state.error}"
                    )

                for language in cfg["languages"].keys():
                    raise_if_aborted()
                    prompt = question[language]

                    # API collectors
                    if mode in ("api", "both") and cfg["api"].get("enabled", True):
                        for provider, pcfg in cfg["api"]["providers"].items():
                            raise_if_aborted()
                            if not pcfg.get("enabled", False):
                                continue
                            key = (
                                str(day), question["question_id"], "api",
                                provider, language, vpn_location
                            )
                            if key in seen:
                                print("SKIP existing:", key)
                                entry["skipped"].append({
                                    "collector": key[2], "provider": provider,
                                    "question_id": question["question_id"], "language": language,
                                })
                                continue

                            obs_id = str(uuid.uuid4())
                            print(f"API {provider} | {language} | {vpn_location}")
                            result = API_ADAPTERS[provider](prompt, pcfg)
                            row = {
                                "observation_id": obs_id,
                                "day": day,
                                "question_id": question["question_id"],
                                "collector": "api",
                                "provider": provider,
                                "language": language,
                                "vpn_location": vpn_location,
                                "timestamp": _now(cfg).isoformat(),
                                "exit_ip": vpn_state.exit_ip,
                                "detected_country_code": vpn_state.detected_country_code,
                                "vpn_verified": vpn_state.verified,
                                "model_requested": pcfg["model"],
                                "model_observed": result.model_observed,
                                "prompt": prompt,
                                "response": result.response,
                                "status": result.status,
                                "error": result.error,
                                "latency_ms": result.latency_ms,
                                "sources": result.sources,
                                "manual_intervention": False,
                            }
                            if manual_recovery is not None:
                                retry_times.setdefault(key, []).append(row["timestamp"])
                                row["retry"] = "\n".join(retry_times[key])
                            append_result(csv_path, row)
                            entry[row["collector"]].append({
                                "observation_id": obs_id,
                                "prompt_id": f"{question['question_id']}_{language}",
                                "question_id": question["question_id"],
                                "language": language, "provider": provider,
                                "timestamp": row["timestamp"], "prompt": prompt,
                                "retry": row.get("retry", ""),
                                **({"url": pcfg["url"]} if row["collector"] == "browser"
                                   else {"model_requested": pcfg["model"]}),
                                **asdict(result),
                            })

                    # Browser collectors
                    if mode in ("browser", "both") and cfg["browser"].get("enabled", True):
                        for provider, pcfg in cfg["browser"]["providers"].items():
                            raise_if_aborted()
                            if not pcfg.get("enabled", False):
                                continue
                            key = (
                                str(day), question["question_id"], "browser",
                                provider, language, vpn_location
                            )
                            if key in seen:
                                print("SKIP existing:", key)
                                entry["skipped"].append({
                                    "collector": key[2], "provider": provider,
                                    "question_id": question["question_id"], "language": language,
                                })
                                continue

                            obs_id = str(uuid.uuid4())
                            print(f"BROWSER {provider} | {language} | {vpn_location}")
                            result = collect_browser(
                                provider=provider,
                                prompt=prompt,
                                browser_cfg={**cfg["browser"], "evidence_dir": str(csv_path.parent / "evidence")},
                                provider_cfg=pcfg,
                                locale=cfg["experiment"]["browser_locale"],
                                timezone_id=cfg["experiment"]["browser_timezone"],
                                screenshot_dir=screenshot_dir,
                                observation_id=obs_id,
                            )
                            row = {
                                "observation_id": obs_id,
                                "day": day,
                                "question_id": question["question_id"],
                                "collector": "browser",
                                "provider": provider,
                                "language": language,
                                "vpn_location": vpn_location,
                                "timestamp": _now(cfg).isoformat(),
                                "exit_ip": vpn_state.exit_ip,
                                "detected_country_code": vpn_state.detected_country_code,
                                "vpn_verified": vpn_state.verified,
                                "model_requested": "",
                                "model_observed": result.model_observed or "",
                                "prompt": prompt,
                                "response": result.response,
                                "status": result.status,
                                "error": result.error,
                                "latency_ms": result.latency_ms,
                                "attempts": result.attempts,
                                "sources": result.sources,
                                "manual_intervention": bool(result.manual_intervention),
                                "human_wait_ms": (
                                    "" if result.human_wait_ms is None else result.human_wait_ms
                                ),
                                "human_timeout_seconds": (
                                    "" if result.human_timeout_seconds is None
                                    else result.human_timeout_seconds
                                ),
                            }
                            if manual_recovery is not None:
                                retry_times.setdefault(key, []).append(row["timestamp"])
                                row["retry"] = "\n".join(retry_times[key])
                            append_result(csv_path, row)
                            entry[row["collector"]].append({
                                "observation_id": obs_id,
                                "prompt_id": f"{question['question_id']}_{language}",
                                "question_id": question["question_id"],
                                "language": language, "provider": provider,
                                "timestamp": row["timestamp"], "prompt": prompt,
                                "retry": row.get("retry", ""),
                                **({"url": pcfg["url"]} if row["collector"] == "browser"
                                   else {"model_requested": pcfg["model"]}),
                                **asdict(result),
                            })
    except (VpnSessionAborted, VpnSessionTimeout) as e:
        error = e
        print(f"Collection stopped: {e}", flush=True)
    except Exception as e:
        error = e
        raise
    finally:
        disconnect_vpn(cfg)
        record["finished_at"] = _now(cfg).isoformat()
        record["error"] = f"{type(error).__name__}: {error}" if error else None
        record["ok"] = error is None and bool(record["locations"]) and all(
            e["vpn"]["verified"] and all(o["status"] == "ok" for o in e["api"] + e["browser"])
            and bool(e["api"] or e["browser"] or e["skipped"])
            for e in record["locations"]
        )
        out_dir = resolve_path(cfg["results"].get("runs_dir", "results/runs"))
        out_dir.mkdir(parents=True, exist_ok=True)
        json_path = out_dir / f"{started:%Y-%m-%d_%H%M%S_%f}_day-{day}_{mode}.json"
        json_path.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"Run JSON written to {json_path}")
        if log_index:
            _log_run_day(cfg, started, day, mode, csv_path, rows_before, error, json_path, command=record["command"])
    return record

def _count_rows(csv_path: Path) -> int:
    if not csv_path.exists():
        return 0
    with csv_path.open(newline="", encoding="utf-8") as f:
        return sum(1 for _ in csv.DictReader(f))

def _log_run_day(cfg, started, day, mode, csv_path, rows_before, error, json_path, *, command=None) -> None:
    rows = []
    if csv_path.exists():
        with csv_path.open(newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))[rows_before:]
    failed = sum(1 for r in rows if r["status"] != "ok")
    outcome = f"{len(rows)} rows, {failed} failed"
    if error is not None:
        outcome = f"ABORTED ({type(error).__name__}): " + outcome
    add_entry(cfg, started, f"`{command or f'run-day --day {day} --mode {mode}'}`", outcome, json_path, anchor=json_path.stem, extra_files=[csv_path] if csv_path.exists() else [])

def _maybe_notify_upcoming_runs(
    schedule: list[dict],
    *,
    cfg: dict,
    mode: str,
    schedule_path: Path,
) -> list[dict]:
    """Once per run_at, warn when a pending day is within advance_notice_minutes."""
    from .alerts import notify_upcoming_run

    alerts = cfg.get("experiment", {}).get("alerts") or {}
    minutes = int(alerts.get("advance_notice_minutes") or 0)
    if minutes <= 0:
        return schedule
    now = _now(cfg)
    window = timedelta(minutes=minutes)
    changed = False
    for event in schedule:
        if event["day"] > _study_days(cfg):
            continue
        if mode in set(event.get("completed_modes") or []):
            continue
        run_at = datetime.fromisoformat(event["run_at"])
        if run_at <= now or run_at > now + window:
            continue
        if event.get("advance_notice_sent_for") == event["run_at"]:
            continue
        delta_min = max(1, int((run_at - now).total_seconds() // 60))
        notify_upcoming_run(
            day=int(event["day"]),
            run_at=event["run_at"],
            minutes_ahead=delta_min,
            cfg=cfg,
        )
        event["advance_notice_sent_for"] = event["run_at"]
        changed = True
    if changed:
        save_schedule(schedule, schedule_path)
    return schedule


def run_due(
    mode: str = "both",
    *,
    cfg: dict | None = None,
    schedule_path: Path | None = None,
    log_index: bool = True,
    recovery: str | None = None,
) -> list[dict]:
    """Run any due scheduled day once. Returns the schedule after updates.

    `recovery` overrides config `experiment.recovery_policy` for this call
    (`skip` or `retry`).
    """
    from .run_lock import CollectionBusy, collection_lock

    try:
        cfg = _live_cfg(cfg)
    except NoActiveStudy:
        print("No active study. No collection was started.", flush=True)
        return []
    if is_study_stopped(cfg):
        print("Study has been stopped. No collection was started.", flush=True)
        return []
    if recovery is not None:
        cfg = {**cfg, "experiment": {**cfg["experiment"], "recovery_policy": recovery}}
    schedule_path = schedule_path or schedule_path_for_cfg(cfg)
    # Advance notice does not need the collection lock (and should still fire
    # when another run-due is mid-CAPTCHA).
    schedule = load_schedule(schedule_path, cfg=cfg)
    _maybe_notify_upcoming_runs(
        schedule, cfg=cfg, mode=mode, schedule_path=schedule_path,
    )
    try:
        with collection_lock(blocking=False):
            return _run_due_locked(
                mode,
                cfg=cfg,
                schedule_path=schedule_path,
                log_index=log_index,
            )
    except CollectionBusy as e:
        print(f"SKIP: {e}", flush=True)
        return load_schedule(schedule_path, cfg=cfg)


def _run_due_locked(
    mode: str,
    *,
    cfg: dict,
    schedule_path: Path,
    log_index: bool,
) -> list[dict]:
    if is_study_stopped(cfg):
        print("Study has been stopped. No collection was started.", flush=True)
        return []
    schedule = load_schedule(schedule_path, cfg=cfg)
    _schedule_next(schedule, cfg, mode, schedule_path)
    now = _now(cfg)
    csv_path = resolve_path(cfg["results"]["csv_path"])

    for event in list(schedule):
        if is_study_stopped(cfg):
            break
        if event["day"] > _study_days(cfg):
            continue
        run_at = datetime.fromisoformat(event["run_at"])
        done = set(event.get("completed_modes", []))
        if now >= run_at and mode not in done:
            print(f"Running scheduled day {event['day']} ({mode})")
            run_day(
                int(event["day"]),
                mode=mode,
                cfg=cfg,
                schedule_path=schedule_path,
                log_index=log_index,
            )
            if is_study_stopped(cfg):
                break
            if _day_complete_for_mode(cfg, int(event["day"]), mode, csv_path):
                event.setdefault("completed_modes", []).append(mode)
                event.pop("retry_count", None)
                save_schedule(schedule, schedule_path)
                _schedule_next(schedule, cfg, mode, schedule_path)
                print(f"Day {event['day']} complete for mode={mode}")
            elif _recovery_policy(cfg) == "retry":
                retry_at = _retry_run_at(cfg)
                event["run_at"] = retry_at.isoformat()
                event["retry_count"] = int(event.get("retry_count") or 0) + 1
                save_schedule(schedule, schedule_path)
                print(
                    f"Day {event['day']} incomplete; recovery=retry, "
                    f"next attempt at {event['run_at']} "
                    f"(retry #{event['retry_count']})"
                )
            else:
                # skip policy: accept gaps and advance
                event.setdefault("completed_modes", []).append(mode)
                save_schedule(schedule, schedule_path)
                _schedule_next(schedule, cfg, mode, schedule_path)
                print(f"Day {event['day']} finished with gaps (recovery=skip)")
    return schedule

def daemon(mode: str = "both", poll_seconds: int = 30, recovery: str | None = None) -> None:
    print(f"Scheduler running in mode={mode}. Ctrl+C to stop.")
    while True:
        try:
            cfg = apply_active_study()
        except NoActiveStudy:
            print("No active study. Scheduler task stopped.", flush=True)
            return
        run_due(mode, cfg=cfg, recovery=recovery)
        if is_study_stopped(cfg):
            print("Study has been stopped. Scheduler task stopped.", flush=True)
            return
        schedule = load_schedule(cfg=cfg)
        days = _study_days(cfg)
        if all(mode in set(e.get("completed_modes", [])) for e in schedule if e["day"] <= days):
            print("All scheduled days completed for this mode.")
            return
        time.sleep(poll_seconds)
