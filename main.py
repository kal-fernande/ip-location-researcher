import argparse
import os
import subprocess
import sys
from pathlib import Path

from experiment.runner import create_schedule, run_day, run_due, daemon
from experiment.analyze import analyze
from experiment.checks import run_browser_checks, run_browser_study_check, run_checks
from experiment.manual import collect_manual_check, collect_manual_day
from experiment.study import ActiveStudyExists, apply_active_study, start_study, stop_study, study_status

def main():
    p = argparse.ArgumentParser(description="IP-location chatbot experiment")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("gui", help="Open the researcher desktop console")

    start = sub.add_parser(
        "start-study",
        help="Create results/studies/<id>/, set it ACTIVE, and schedule day 1 there",
    )
    start.add_argument("--id", help="Study id (default: timestamp_browser)")
    start.add_argument("--name", default="", help="Display name (also used in the generated folder name)")
    start.add_argument("--start-date", help="YYYY-MM-DD; default is tomorrow")
    start.add_argument("--seed", type=int, help="Optional reproducible random seed")
    start.add_argument("--agenda", help="Load fixed times from this agenda JSON")
    start.add_argument("--notes", default="", help="Optional note stored in manifest.json")
    start.add_argument(
        "--force",
        action="store_true",
        help="Replace the current ACTIVE study (previous folder stays on disk)",
    )

    sub.add_parser("study-status", help="Show the active study id, paths, and next run_at")
    stop = sub.add_parser("stop-study", help="Stop the active study and keep its saved results")
    stop.add_argument("--study-id", help="Refuse if the active study changed since choosing to stop")
    sub.add_parser(
        "vpn-off",
        help="Disconnect Mullvad now; soft-stop a running collector without abandoning the study",
    )

    s = sub.add_parser("schedule", help="Reschedule day 1 for the active study (or load an agenda)")
    s.add_argument("--start-date", help="YYYY-MM-DD; default is tomorrow")
    s.add_argument("--seed", type=int, help="Optional reproducible random seed")
    s.add_argument("--agenda", help="Load fixed times from this agenda JSON (see agenda.example.json)")

    r = sub.add_parser("run-day", help="Run one day immediately")
    r.add_argument("--day", type=int, required=True)
    r.add_argument("--mode", choices=["api", "browser", "both"], default="both")
    r.add_argument("--force", action="store_true")
    r.add_argument("--recovery", choices=["skip", "retry"],
                   help="Override experiment.recovery_policy for this run")

    retry = sub.add_parser("retry-day", help="Retry only missing/failed browser answers after a scheduled day became due")
    retry.add_argument("--day", type=int, required=True)
    retry.add_argument("--study-id", help="Refuse if the active study changed since choosing the day")

    d = sub.add_parser("run-due", help="Run any due scheduled day once")
    d.add_argument("--mode", choices=["api", "browser", "both"], default="both")
    d.add_argument("--recovery", choices=["skip", "retry"],
                   help="Override experiment.recovery_policy (skip gaps vs retry later)")

    dm = sub.add_parser("daemon", help="Stay running and execute scheduled days")
    dm.add_argument("--mode", choices=["api", "browser", "both"], default="both")
    dm.add_argument("--poll-seconds", type=int, default=30)
    dm.add_argument("--recovery", choices=["skip", "retry"],
                   help="Override experiment.recovery_policy")

    sub.add_parser("analyze", help="Compare location and API/browser effects")
    sub.add_parser("refresh-links", help="Recover saved source URLs into the CSV without collecting new answers")
    evidence = sub.add_parser("validate-evidence", help="Validate a saved browser check/run and its local evidence without new searches")
    evidence.add_argument("run_json", help="Path to a saved check or run JSON")
    evidence.add_argument("--csv", help="Compare this CSV and its Excel mirror; study runs infer their sibling CSV")

    c = sub.add_parser("check", help="Check VPN switching and API calls through the VPN")
    c.add_argument("--no-api", action="store_true", help="Skip the API calls")
    c.add_argument("--browser", action="store_true", help="Also ask the check questions in each web app")

    browser_ready = sub.add_parser("check-browser", help="Check VPN switching and browser app readiness")
    browser_ready.add_argument("--provider", help="Check only this enabled browser provider, e.g. google_search")
    browser_prompt = sub.add_parser("check-browser-prompt", help="Check VPN switching and ask the check questions in each web app")
    browser_prompt.add_argument("--provider", help="Check only this enabled browser provider, e.g. google_search")
    browser_prompt.add_argument("--study-only", action="store_true",
                                help="Ask only day 1's study question in each language; skip country/emergency diagnostics")
    scheduler = sub.add_parser("check-scheduler", help="Verify macOS LaunchAgent and scheduling setup without collection")
    scheduler.add_argument("--probe", action="store_true", help="Verify a fresh no-op background launch; refuses if collection is due soon")
    study = sub.add_parser(
        "check-browser-study",
        help="Sandboxed one-day browser study smoke test (run-due, CSV/runs, skip, next-day draw)",
    )
    study_start = study.add_mutually_exclusive_group()
    study_start.add_argument("--launchagent-delay", action="store_true",
                             help="macOS: let a temporary LaunchAgent start the full test after 60 seconds")
    study_start.add_argument("--launchagent-worker", help=argparse.SUPPRESS)

    m = sub.add_parser("collect-manual", help="Switch the VPN and record answers a person gets from a web app by hand")
    m.add_argument("--provider", default="claude", help="Web app the person uses (default: claude)")
    m.add_argument("--day", type=int, help="Study day to collect; without it, ask the two check questions")

    args = p.parse_args()
    if args.cmd == "gui":
        try:
            from experiment.gui import launch
        except ImportError as error:
            if error.name not in ("tkinter", "_tkinter"):
                raise
            # Homebrew Python often lacks _tkinter until setup.sh installs python-tk.
            # Reuse another Tk-capable Python for the window; keep collection in this venv.
            from experiment.gui_support import ROOT, find_desktop_python

            desktop_python = find_desktop_python()
            if not desktop_python:
                p.exit(
                    1,
                    "The desktop console needs Python with Tk 8.6+. "
                    "On macOS with Homebrew, run: source setup.sh\n",
                )
            environment = os.environ.copy()
            environment["IP_LOCATION_CLI_PYTHON"] = str(Path(sys.executable).absolute())
            raise SystemExit(subprocess.call(
                [desktop_python, "-u", "-m", "experiment.gui"],
                cwd=str(ROOT),
                env=environment,
            ))
        launch()
    elif args.cmd == "start-study":
        try:
            study_id, root = start_study(
                args.id,
                start_date=args.start_date,
                seed=args.seed,
                agenda_path=args.agenda,
                name=args.name,
                notes=args.notes,
                force=args.force,
            )
        except (ActiveStudyExists, ValueError, OSError) as e:
            print(e, file=sys.stderr)
            sys.exit(1)
        print(f"Active study: {study_id}")
        print(f"Folder: {root}")
        status = study_status()
        if status.get("next_event"):
            e = status["next_event"]
            print(f"Day {e['day']}: {e['run_at']} | order={e['location_order']}")
    elif args.cmd == "stop-study":
        try:
            result = stop_study(study_id=args.study_id)
        except (ValueError, OSError) as error:
            p.exit(1, f"{error}\n")
        if result is None:
            print("No active study. Nothing to stop.")
        else:
            print(f"Study stopped: {result['study_id']}")
            print(f"Saved results kept in: {result['root']}")
            if result["error"]:
                print(result["error"], file=sys.stderr)
            if result["collector_alive"]:
                print("A collection is still stopping. Wait for it to finish or use Disconnect VPN before creating a new study.", file=sys.stderr)
            if result["error"] or result["collector_alive"]:
                sys.exit(1)
            print("Future runs are cancelled. Use New study to set up a new run.")
    elif args.cmd == "study-status":
        status = study_status()
        print(f"study_id: {status['study_id']}")
        print(f"root: {status['root']}")
        print(f"csv: {status['csv_path']}")
        print(f"schedule: {status['schedule_path']}")
        if status.get("next_event"):
            e = status["next_event"]
            print(f"next: day {e['day']} at {e['run_at']} order={e['location_order']}")
        else:
            print("next: (none pending)")
    elif args.cmd == "vpn-off":
        from experiment.vpn import vpn_off
        result = vpn_off()
        print("VPN disconnect requested.")
        if result.get("collector_pid"):
            print(f"collector pid: {result['collector_pid']}")
            print(f"signaled: {', '.join(result['signaled']) or '(none)'}")
            print(f"collector still alive: {result['collector_alive']}")
            if result.get("force_killed"):
                print("collector was force-stopped after soft signals.")
        else:
            print("no collector lock pid found.")
        print("ACTIVE study, schedule, and CSV were not modified.")
    elif args.cmd == "schedule":
        cfg = apply_active_study()
        schedule = create_schedule(
            args.start_date, args.seed, agenda_path=args.agenda, cfg=cfg,
        )
        for e in schedule:
            print(f"Day {e['day']}: {e['run_at']} | order={e['location_order']}")
    elif args.cmd == "run-day":
        from experiment.run_lock import CollectionBusy, collection_lock
        cfg = apply_active_study()
        if args.recovery:
            cfg = {**cfg, "experiment": {**cfg["experiment"], "recovery_policy": args.recovery}}
        try:
            with collection_lock(blocking=False):
                run_day(args.day, args.mode, args.force, cfg=cfg)
        except CollectionBusy as e:
            print(e, file=sys.stderr)
            sys.exit(1)
    elif args.cmd == "retry-day":
        from experiment.manual_retry import retry_day
        from experiment.run_lock import CollectionBusy
        try:
            record = retry_day(args.day, study_id=args.study_id)
        except (CollectionBusy, ValueError) as error:
            print(error, file=sys.stderr)
            sys.exit(1)
        sys.exit(0 if record["ok"] else 1)
    elif args.cmd == "run-due":
        run_due(args.mode, recovery=args.recovery)
    elif args.cmd == "daemon":
        daemon(args.mode, args.poll_seconds, recovery=args.recovery)
    elif args.cmd == "analyze":
        analyze()
    elif args.cmd == "validate-evidence":
        from experiment.evidence import validate_run
        report = validate_run(Path(args.run_json), Path(args.csv) if args.csv else None)
        for item in report["results"]:
            print(f"{'PASS' if item['ok'] else 'FAIL'} {item['location']} {item['provider']} {item['prompt_id']}: "
                  + ("; ".join(item["errors"]) if item["errors"] else "answer, sources, and saved files match"))
        print(f"{'PASS' if report['ok'] else 'FAIL'} — {report['observations']} browser observations")
        sys.exit(0 if report["ok"] else 1)
    elif args.cmd == "refresh-links":
        from experiment.config import resolve_path
        from experiment.storage import refresh_source_urls
        cfg = apply_active_study()
        path = resolve_path(cfg["results"]["csv_path"])
        summary = refresh_source_urls(path, resolve_path(cfg["results"].get("runs_dir", "results/runs")))
        print(f"Updated {path}: {summary['rows']} observations, {summary['rows_with_links']} with links, {summary['links']} URLs")
    elif args.cmd == "check":
        _, ok = run_checks(api=not args.no_api, browser=args.browser)
        sys.exit(0 if ok else 1)
    elif args.cmd == "check-browser":
        _, ok = run_browser_checks(provider=args.provider)
        sys.exit(0 if ok else 1)
    elif args.cmd == "check-browser-prompt":
        command = "check-browser-prompt" + (f" --provider {args.provider}" if args.provider else "")
        if args.study_only:
            command += " --study-only"
        _, ok = run_checks(api=False, browser=True, command=command,
                           file_suffix="_browser-prompt", browser_provider=args.provider, study_only=args.study_only)
        sys.exit(0 if ok else 1)
    elif args.cmd == "check-browser-study":
        if args.launchagent_worker:
            from experiment.launchagent_study import run_worker
            sys.exit(run_worker(args.launchagent_worker))
        elif args.launchagent_delay:
            from experiment.launchagent_study import run_delayed_study_check
            _, ok = run_delayed_study_check()
        else:
            _, ok = run_browser_study_check()
        sys.exit(0 if ok else 1)
    elif args.cmd == "check-scheduler":
        from experiment.scheduler_checks import run_scheduler_check
        _, ok = run_scheduler_check(probe=args.probe)
        sys.exit(0 if ok else 1)
    elif args.cmd == "collect-manual":
        if args.day is None:
            _, ok = collect_manual_check(args.provider)
            sys.exit(0 if ok else 1)
        collect_manual_day(args.provider, args.day)

if __name__ == "__main__":
    main()
