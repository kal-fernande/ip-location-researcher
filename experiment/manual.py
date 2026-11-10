from __future__ import annotations
import json
import uuid
from dataclasses import asdict
from datetime import datetime
from zoneinfo import ZoneInfo

from .config import load_config, resolve_path
from .vpn import connect_vpn, disconnect_vpn, raise_if_aborted, vpn_session
from .storage import append_result, existing_keys
from .runlog import add_entry
from .links import sources_from_text

# A person uses the web app in their own browser; this script never touches the app.
# It switches the VPN, shows each question, and records the answer the person pastes in.

CHECKLIST = """\
Before you start, in your own browser:
  - log in to {provider} as usual (the same account for every location);
  - turn off memory and search of past chats, so earlier chats cannot reveal the location;
  - keep the browser's language and your computer's timezone unchanged for the whole run.
For every question, start a NEW chat."""

END_MARK = "."

def _read_answer() -> str:
    """Read a pasted multi-line answer, ended by a line with only '.' (or Ctrl-D)."""
    lines = []
    while True:
        try:
            line = input()
        except EOFError:
            break
        if line.strip() == END_MARK:
            break
        lines.append(line)
    return "\n".join(lines).strip()

def _ask(prompt: str, label: str) -> str:
    print(f"\n--- {label} ---")
    print("Paste this into a NEW chat:\n")
    print(f"    {prompt}\n")
    print(f"Then paste the full answer here and end with a line containing only '{END_MARK}'.")
    print("Leave it empty to record the question as skipped.")
    return _read_answer()

def _connect(cfg, name, loc_cfg):
    print(f"\n=== VPN -> {name} ===")
    state = connect_vpn(cfg, name, loc_cfg)
    print(f"exit_ip={state.exit_ip} detected={state.detected_country_code} verified={state.verified}")
    return state

def _start(cfg, provider: str) -> str:
    print(CHECKLIST.format(provider=provider))
    model = input("\nModel shown in the app (Enter if none): ").strip()
    input("Press Enter when ready; the VPN switches next.")
    return model

def collect_manual_check(provider: str):
    """Ask diagnostic and day 1 study questions by hand at each location. Writes one JSON
    file to results/checks/ and returns its path and whether it passed."""
    from .checks import load_check_prompts

    cfg = load_config()
    prompts = load_check_prompts(cfg)
    tz = ZoneInfo(cfg["experiment"]["timezone"])
    started = datetime.now(tz)
    model = _start(cfg, provider)
    locations = []

    try:
        with vpn_session(cfg):
            for name, loc_cfg in cfg["locations"].items():
                raise_if_aborted()
                state = _connect(cfg, name, loc_cfg)
                vpn = asdict(state)
                vpn.pop("raw")
                entry = {"location": name, "vpn": vpn, "manual": []}
                if state.verified:
                    for check_prompt in prompts:
                        raise_if_aborted()
                        prompt_id, prompt = check_prompt["prompt_id"], check_prompt["prompt"]
                        answer = _ask(prompt, f"{name} | {prompt_id}")
                        entry["manual"].append({
                            "provider": provider, **check_prompt,
                            "response": answer, "model_observed": model or None,
                            "sources": sources_from_text(answer),
                            "status": "ok" if answer else "skipped",
                            "timestamp": datetime.now(tz).isoformat(),
                        })
                locations.append(entry)
    finally:
        disconnect_vpn(cfg)

    ok = bool(locations) and all(
        e["vpn"]["verified"] and e["manual"] and all(m["status"] == "ok" for m in e["manual"])
        for e in locations
    )
    record = {
        "started_at": started.isoformat(),
        "finished_at": datetime.now(tz).isoformat(),
        "command": f"collect-manual --provider {provider}",
        "checks": ["vpn", "manual"],
        "collector": "manual",
        "ok": ok,
        "locations": locations,
    }
    out_dir = resolve_path(cfg["results"].get("checks_dir", "results/checks"))
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{started:%Y-%m-%d_%H%M%S}_manual-{provider}.json"
    path.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")

    add_entry(cfg, started, f"`collect-manual --provider {provider}` (vpn, manual)", "PASS" if ok else "FAIL", path,
              anchor=path.stem)
    print(f"\n{'PASS' if ok else 'FAIL'} — written to {path}")
    return path, ok

def collect_manual_day(provider: str, day: int):
    """Ask one study day's question by hand, in each language at each location.
    Appends rows with collector 'manual' to the active study's results.csv."""
    from .runner import _question_for_day, load_schedule
    from .study import apply_active_study

    cfg = apply_active_study()
    tz = ZoneInfo(cfg["experiment"]["timezone"])
    started = datetime.now(tz)
    question = _question_for_day(day)
    csv_path = resolve_path(cfg["results"]["csv_path"])
    seen = existing_keys(csv_path)

    event = next((e for e in load_schedule(cfg=cfg) if int(e["day"]) == int(day)), None)
    location_order = event["location_order"] if event else list(cfg["locations"].keys())

    model = _start(cfg, provider)
    rows, aborted = 0, None
    try:
        with vpn_session(cfg):
            for name in location_order:
                raise_if_aborted()
                state = _connect(cfg, name, cfg["locations"][name])
                if not state.verified:
                    raise RuntimeError(f"VPN verification failed for {name}: got {state.detected_country_code}")
                for language in cfg["languages"].keys():
                    raise_if_aborted()
                    key = (str(day), question["question_id"], "manual", provider, language, name)
                    if key in seen:
                        print("SKIP existing:", key)
                        continue
                    prompt = question[language]
                    answer = _ask(prompt, f"{name} | {language}")
                    append_result(csv_path, {
                        "observation_id": str(uuid.uuid4()),
                        "day": day,
                        "question_id": question["question_id"],
                        "collector": "manual",
                        "provider": provider,
                        "language": language,
                        "vpn_location": name,
                        "timestamp": datetime.now(tz).isoformat(),
                        "exit_ip": state.exit_ip,
                        "detected_country_code": state.detected_country_code,
                        "vpn_verified": state.verified,
                        "model_requested": "",
                        "model_observed": model,
                        "prompt": prompt,
                        "response": answer,
                        "status": "ok" if answer else "skipped",
                        "error": "",
                        "latency_ms": "",  # the app's latency is not observed
                    })
                    rows += 1
    except Exception as e:
        aborted = e
        raise
    finally:
        disconnect_vpn(cfg)
        outcome = f"{rows} rows"
        if aborted is not None:
            outcome = f"ABORTED ({type(aborted).__name__}): " + outcome
        add_entry(cfg, started, f"`collect-manual --provider {provider} --day {day}`", outcome, csv_path,
                  anchor=f"day-{day}")
