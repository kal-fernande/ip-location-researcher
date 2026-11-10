from __future__ import annotations
import copy
import csv
import json
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from .config import ROOT, load_config, load_questions, resolve_path
from .vpn import connect_vpn, disconnect_vpn, raise_if_aborted, vpn_session
from .api_collectors import API_ADAPTERS
from .browser_collectors import check_browser_provider, collect_browser
from .runlog import add_entry
from . import runner

# Short fixed prompts: cheap and never mistaken for study data. Each is sent as its own
# conversation. The API gets no location, so neither answer should depend on the VPN.
CHECK_PROMPTS = {
    # Direct: does the model know where the user is? Expected via API: "unknown".
    "country": 'Which country am I in? Answer with the country name only, or "unknown" if you cannot tell.',
    # Indirect: does location change the local emergency service number?
    "emergency": "What number do I call in an emergency? Answer with the number only.",
}


def load_check_prompts(cfg):
    """Keep the diagnostic probes and add day 1 in every configured language."""
    prompts = [{"prompt_id": key, "prompt": text} for key, text in CHECK_PROMPTS.items()]
    question = next((q for q in load_questions() if int(q["day"]) == 1), None)
    if question is None:
        raise ValueError("questions.json must contain a day 1 question for prompt checks")
    for language in cfg["languages"]:
        prompt = question.get(language)
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError(f"Day 1 question is missing a non-empty {language!r} translation")
        prompts.append({
            "prompt_id": f"{question['question_id']}_{language}",
            "question_id": question["question_id"],
            "language": language,
            "prompt": prompt,
        })
    return prompts


def _select_browser_provider(cfg: dict, provider: str | None) -> dict:
    if provider is None:
        return cfg
    providers = cfg["browser"]["providers"]
    if provider not in providers or not providers[provider].get("enabled", False):
        raise ValueError(f"Browser provider {provider!r} must be configured and enabled")
    cfg = copy.deepcopy(cfg)
    cfg["browser"]["providers"] = {provider: cfg["browser"]["providers"][provider]}
    return cfg


def run_checks(api: bool = True, browser: bool = False, command: str | None = None, file_suffix: str = "", browser_provider: str | None = None, study_only: bool = False):
    """Connect to each VPN location, verify the exit country, and optionally
    ask the check prompts through each enabled API provider and/or web app.
    Writes one JSON file to results/checks/ and returns its path. `command`
    is the CLI command logged in the results index."""
    cfg = _select_browser_provider(load_config(), browser_provider)
    prompts = load_check_prompts(cfg) if api or browser else []
    if study_only:
        prompts = [prompt for prompt in prompts if prompt.get("question_id")]
    tz = ZoneInfo(cfg["experiment"]["timezone"])
    started = datetime.now(tz)
    locations = []

    try:
        with vpn_session(cfg):
            for name, loc_cfg in cfg["locations"].items():
                raise_if_aborted()
                print(f"\n=== VPN -> {name} ===")
                state = connect_vpn(cfg, name, loc_cfg)
                vpn = asdict(state)
                vpn.pop("raw")
                print(f"exit_ip={state.exit_ip} detected={state.detected_country_code} verified={state.verified}")
                entry = {"location": name, "vpn": vpn, "api": [], "browser": []}

                if api and state.verified and cfg["api"].get("enabled", True):
                    for provider, pcfg in cfg["api"]["providers"].items():
                        if not pcfg.get("enabled", False):
                            continue
                        for check_prompt in prompts:
                            raise_if_aborted()
                            prompt_id, prompt = check_prompt["prompt_id"], check_prompt["prompt"]
                            result = API_ADAPTERS[provider](prompt, pcfg)
                            print(f"API {provider} [{prompt_id}]: status={result.status} model={result.model_observed} "
                                  f"latency_ms={result.latency_ms} response={result.response[:60]!r}")
                            entry["api"].append({"provider": provider, "model_requested": pcfg["model"],
                                                 **check_prompt, **asdict(result)})

                if browser and state.verified and cfg["browser"].get("enabled", True):
                    for provider, pcfg in cfg["browser"]["providers"].items():
                        if not pcfg.get("enabled", False):
                            continue
                        for check_prompt in prompts:
                            raise_if_aborted()
                            prompt_id, prompt = check_prompt["prompt_id"], check_prompt["prompt"]
                            observation_id = f"check_{started:%Y%m%d_%H%M%S}_{name}_{provider}_{prompt_id}"
                            result = collect_browser(
                                provider=provider,
                                prompt=prompt,
                                browser_cfg={**cfg["browser"], "evidence_dir": str(resolve_path(cfg["results"].get("checks_dir", "results/checks")) / "evidence")},
                                provider_cfg=pcfg,
                                locale=cfg["experiment"]["browser_locale"],
                                timezone_id=cfg["experiment"]["browser_timezone"],
                                screenshot_dir=resolve_path(cfg["results"]["failure_screenshot_dir"]),
                                observation_id=observation_id,
                            )
                            print(f"BROWSER {provider} [{prompt_id}]: status={result.status} "
                                  f"sources={len(result.sources)} latency_ms={result.latency_ms} response={result.response[:60]!r}")
                            entry["browser"].append({"provider": provider, "url": pcfg["url"], "observation_id": observation_id,
                                                     **check_prompt, **asdict(result)})
                locations.append(entry)
    finally:
        disconnect_vpn(cfg)

    ok = bool(locations) and all(
        e["vpn"]["verified"] and all(a["status"] == "ok" for a in e["api"] + e["browser"])
        for e in locations
    )
    if api and not any(e["api"] for e in locations):
        ok = False  # API check requested but nothing ran
    if browser and not any(e["browser"] for e in locations):
        ok = False  # browser check requested but nothing ran

    record = {
        "started_at": started.isoformat(),
        "finished_at": datetime.now(tz).isoformat(),
        "command": command or "check" + (" --no-api" if not api else "") + (" --browser" if browser else ""),
        "checks": ["vpn"] + (["api"] if api else []) + (["browser"] if browser else []),
        "ok": ok,
        "locations": locations,
    }
    out_dir = resolve_path(cfg["results"].get("checks_dir", "results/checks"))
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{started:%Y-%m-%d_%H%M%S}{file_suffix}.json"
    path.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")

    add_entry(cfg, started, f"`{record['command']}` ({', '.join(record['checks'])})", "PASS" if ok else "FAIL", path,
              anchor=path.stem)
    print(f"\n{'PASS' if ok else 'FAIL'} — written to {path}")
    return path, ok

def run_browser_checks(provider: str | None = None):
    """Connect to each VPN location and verify that each enabled browser app
    exposes a usable prompt box. Does not submit a prompt or write study rows."""
    cfg = _select_browser_provider(load_config(), provider)
    command = "check-browser" + (f" --provider {provider}" if provider else "")
    tz = ZoneInfo(cfg["experiment"]["timezone"])
    started = datetime.now(tz)
    locations = []
    screenshot_dir = resolve_path(cfg["results"]["failure_screenshot_dir"])

    try:
        with vpn_session(cfg):
            for name, loc_cfg in cfg["locations"].items():
                raise_if_aborted()
                print(f"\n=== VPN -> {name} ===")
                state = connect_vpn(cfg, name, loc_cfg)
                vpn = asdict(state)
                vpn.pop("raw")
                print(f"exit_ip={state.exit_ip} detected={state.detected_country_code} verified={state.verified}")
                entry = {"location": name, "vpn": vpn, "browser": []}

                if state.verified and cfg["browser"].get("enabled", True):
                    for provider, pcfg in cfg["browser"]["providers"].items():
                        raise_if_aborted()
                        if not pcfg.get("enabled", False):
                            continue
                        observation_id = f"browser-check_{started:%Y%m%d_%H%M%S}_{name}_{provider}"
                        result = check_browser_provider(
                            provider=provider,
                            browser_cfg=cfg["browser"],
                            provider_cfg=pcfg,
                            locale=cfg["experiment"]["browser_locale"],
                            timezone_id=cfg["experiment"]["browser_timezone"],
                            screenshot_dir=screenshot_dir,
                            observation_id=observation_id,
                        )
                        print(
                            f"BROWSER {provider}: status={result.status} "
                            f"latency_ms={result.latency_ms}"
                        )
                        entry["browser"].append(asdict(result))
                locations.append(entry)
    finally:
        disconnect_vpn(cfg)

    ok = bool(locations) and all(
        e["vpn"]["verified"] and e["browser"] and all(b["status"] == "ready" for b in e["browser"])
        for e in locations
    )

    record = {
        "started_at": started.isoformat(),
        "finished_at": datetime.now(tz).isoformat(),
        "command": command,
        "checks": ["vpn", "browser"],
        "ok": ok,
        "locations": locations,
    }
    out_dir = resolve_path(cfg["results"].get("checks_dir", "results/checks"))
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{started:%Y-%m-%d_%H%M%S}_browser.json"
    path.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")

    add_entry(cfg, started, f"`{command}` (vpn, browser)", "PASS" if ok else "FAIL", path, anchor=path.stem)
    print(f"\n{'PASS' if ok else 'FAIL'} — written to {path}")
    return path, ok


def _enabled_browser_providers(cfg: dict) -> list[str]:
    return [
        name for name, pcfg in cfg.get("browser", {}).get("providers", {}).items()
        if pcfg.get("enabled", False)
    ]


def _csv_rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def run_browser_study_check(*, sandbox: Path | None = None, log_index: bool = True):
    """Smoke-test one browser study day through the real run_due/run_day path.

    Uses a sandbox under results/checks/study-smoke/ so the active study's
    schedule.json and results.csv are never modified. After the due run, re-runs
    day 1 to confirm existing CSV keys are skipped, and checks that day 2 was drawn.
    """
    from .run_lock import collection_lock

    # Keep the whole test exclusive, including its direct run_day skip check.
    # Taking the lock before creating the sandbox also makes a busy test a no-op.
    with collection_lock(blocking=False):
        return _run_browser_study_check_locked(sandbox=sandbox, log_index=log_index)


def _run_browser_study_check_locked(*, sandbox: Path | None = None, log_index: bool = True):
    """Run the smoke pipeline while the caller holds the collection lock."""
    from .study import NoActiveStudy, apply_active_study

    live_cfg = load_config()
    tz = ZoneInfo(live_cfg["experiment"]["timezone"])
    started = datetime.now(tz)
    stamp = f"{started:%Y-%m-%d_%H%M%S}"

    try:
        active = apply_active_study(live_cfg)
        live_schedule = resolve_path(active["results"]["schedule_path"])
        live_csv = resolve_path(active["results"]["csv_path"])
    except NoActiveStudy:
        live_schedule = ROOT / "schedule.json"
        live_csv = resolve_path(live_cfg["results"]["csv_path"])
    schedule_before = live_schedule.read_bytes() if live_schedule.exists() else None
    csv_before = live_csv.read_bytes() if live_csv.exists() else None

    smoke_root = resolve_path(live_cfg["results"].get("checks_dir", "results/checks")) / "study-smoke"
    sandbox = sandbox or smoke_root / stamp
    if not sandbox.resolve().is_relative_to(smoke_root.resolve()):
        raise ValueError("Study smoke outputs must be inside checks_dir/study-smoke")
    sandbox.mkdir(parents=True, exist_ok=True)
    schedule_path = sandbox / "schedule.json"
    csv_path = sandbox / "results.csv"
    runs_dir = sandbox / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)

    cfg = copy.deepcopy(live_cfg)
    # Two days so completing day 1 draws a pending day 2 without a 10-day wait.
    cfg["experiment"]["days"] = 2
    cfg["results"]["csv_path"] = str(csv_path)
    cfg["results"]["runs_dir"] = str(runs_dir)
    cfg["results"]["failure_screenshot_dir"] = str(sandbox / "failures")
    cfg["results"]["index_path"] = str(sandbox / "README.md")
    cfg["results"]["details_path"] = str(sandbox / "results.md")
    cfg["results"]["schedule_path"] = str(schedule_path)

    providers = _enabled_browser_providers(cfg)
    expected_rows = len(cfg["locations"]) * len(cfg["languages"]) * len(providers)
    due_at = (started - timedelta(minutes=1)).replace(second=0, microsecond=0)
    runner.save_schedule([{
        "day": 1,
        "run_at": due_at.isoformat(),
        "location_order": list(cfg["locations"].keys()),
        "completed_modes": [],
    }], schedule_path)

    steps: dict = {"sandbox": str(sandbox.relative_to(ROOT)), "expected_rows": expected_rows}
    collection_error = None
    first_record: dict | None = None
    second_record: dict | None = None
    schedule_after_due: list[dict] = []

    print("\n=== check-browser-study: sandboxed run-due (day 1, browser) ===")
    print(f"sandbox: {sandbox}")
    try:
        schedule_after_due = runner._run_due_locked(
            "browser", cfg=cfg, schedule_path=schedule_path, log_index=False,
        )
        run_files = sorted(runs_dir.glob("*_day-1_browser.json"))
        if run_files:
            first_record = json.loads(run_files[-1].read_text(encoding="utf-8"))
        steps["first_run"] = {
            "ok": bool(first_record and first_record.get("ok")),
            "csv_rows": len(_csv_rows(csv_path)),
            "run_json": run_files[-1].name if run_files else None,
        }

        print("\n=== check-browser-study: re-run day 1 (expect skips) ===")
        second_record = runner.run_day(
            1, mode="browser", cfg=cfg, schedule_path=schedule_path, log_index=False,
        )
        skipped = sum(len(e.get("skipped", [])) for e in second_record.get("locations", []))
        steps["second_run"] = {
            "ok": bool(second_record.get("ok")),
            "skipped": skipped,
            "new_browser_answers": sum(
                len(e.get("browser", [])) for e in second_record.get("locations", [])
            ),
            "csv_rows": len(_csv_rows(csv_path)),
        }
        steps["schedule_after"] = schedule_after_due
    except Exception as e:
        collection_error = f"{type(e).__name__}: {e}"
        steps["error"] = collection_error
        print(f"ABORTED: {collection_error}")

    schedule_unchanged = (
        (live_schedule.read_bytes() if live_schedule.exists() else None) == schedule_before
    )
    csv_unchanged = (live_csv.read_bytes() if live_csv.exists() else None) == csv_before
    steps["live_schedule_unchanged"] = schedule_unchanged
    steps["live_csv_unchanged"] = csv_unchanged

    rows = _csv_rows(csv_path)
    day1 = next((e for e in schedule_after_due if int(e["day"]) == 1), None)
    day2 = next((e for e in schedule_after_due if int(e["day"]) == 2), None)
    reasons = []
    if collection_error:
        reasons.append(collection_error)
    if not providers:
        reasons.append("no browser providers enabled")
    if not (first_record and first_record.get("ok")):
        reasons.append("first sandboxed run-day was not ok")
    if len(rows) != expected_rows:
        reasons.append(f"csv has {len(rows)} rows, expected {expected_rows}")
    elif any(r.get("status") != "ok" for r in rows):
        reasons.append("csv contains non-ok study rows")
    if not (second_record and second_record.get("ok")):
        reasons.append("second run-day (skip check) was not ok")
    if steps.get("second_run", {}).get("skipped") != expected_rows:
        reasons.append(
            f"second run skipped {steps.get('second_run', {}).get('skipped')} keys, "
            f"expected {expected_rows}"
        )
    if steps.get("second_run", {}).get("new_browser_answers", 0) != 0:
        reasons.append("second run collected new browser answers instead of skipping")
    if not (day1 and "browser" in day1.get("completed_modes", [])):
        reasons.append("day 1 was not marked completed for browser")
    if day2 is None:
        reasons.append("day 2 was not drawn after day 1 completed")
    if not schedule_unchanged:
        reasons.append("live schedule.json changed")
    if not csv_unchanged:
        reasons.append("live results.csv changed")

    ok = not reasons
    steps["fail_reasons"] = reasons

    # Copy first-run locations into the summary so checks/results.md shows the
    # chatbot answers; link sandbox CSV + run JSON from the results index.
    first_run_path = None
    if steps.get("first_run", {}).get("run_json"):
        first_run_path = runs_dir / steps["first_run"]["run_json"]
    record = {
        "started_at": started.isoformat(),
        "finished_at": datetime.now(tz).isoformat(),
        "command": "check-browser-study",
        "checks": ["vpn", "browser", "study-pipeline"],
        "ok": ok,
        "steps": steps,
        "locations": (first_record or {}).get("locations", []),
    }
    out_dir = resolve_path(live_cfg["results"].get("checks_dir", "results/checks"))
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{stamp}_browser-study.json" if log_index else sandbox / "check-summary.json"
    path.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (sandbox / "check-summary.json").write_text(
        json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8",
    )

    extra = [p for p in (csv_path, first_run_path) if p is not None and p.exists()]
    if log_index:
        add_entry(
            live_cfg, started,
            "`check-browser-study` (vpn, browser, study-pipeline)",
            "PASS" if ok else "FAIL", path, anchor=path.stem,
            extra_files=extra,
        )
    print(f"\n{'PASS' if ok else 'FAIL'} — written to {path}")
    if reasons:
        for reason in reasons:
            print(f"  - {reason}")
    return path, ok
