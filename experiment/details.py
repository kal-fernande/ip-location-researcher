from __future__ import annotations
import csv
import json
import os
from collections import defaultdict
from pathlib import Path

from .config import ROOT, load_config, resolve_path
from .links import observation_sources

# Rebuild study and check reports from their own source files.

ANSWER_CHARS = 300  # study answers are long; the full text is in the linked file

def _cell(text, limit: int | None = None) -> str:
    text = " ".join(str(text or "").split())
    if limit and len(text) > limit:
        text = text[:limit].rstrip() + " …"
    return text.replace("|", "\\|")

def _first_line(error) -> str:
    return _cell(str(error or "").splitlines()[0] if error else "", 160)


def _source_links(observation: dict) -> str:
    links = []
    for source in observation_sources(observation):
        label = _cell(source["title"] or source["url"], 100).replace("[", "\\[").replace("]", "\\]")
        links.append(f"[{label}](<{source['url']}>)")
    return ", ".join(links)

def check_command(record: dict, stem: str) -> str:
    """The command that wrote a check file. Older files do not store it."""
    if record.get("command"):
        return record["command"]
    if stem.endswith("_browser-study"):
        return "check-browser-study"
    if stem.endswith("_browser-prompt"):
        return "check-browser-prompt"
    if stem.endswith("_browser"):
        return "check-browser"
    if "_manual-" in stem:
        return f"collect-manual --provider {stem.split('_manual-')[1]}"
    checks = record.get("checks", [])
    return "check" + ("" if "api" in checks else " --no-api") + (" --browser" if "browser" in checks else "")

def _vpn_line(vpn: dict) -> str:
    line = (f"VPN: exit IP `{vpn.get('exit_ip')}`, detected `{vpn.get('detected_country_code')}`, "
            f"verified {'yes' if vpn.get('verified') else 'no'}")
    if vpn.get("error"):
        line += f". Error: {_first_line(vpn['error'])}"
    return line + "."

def _observation_rows(entry: dict, out_dir: Path | None = None) -> list[str]:
    rows = []
    for kind in ("api", "browser", "manual"):
        for o in entry.get(kind, []):
            notes = []
            if o.get("manual_intervention"):
                notes.append("CAPTCHA solved by a person")
            if o.get("model_observed"):
                notes.append(f"model `{o['model_observed']}`")
            if o.get("error"):
                notes.append(_first_line(o["error"]))
            sources = _source_links(o)
            if sources:
                notes.append("Sources: " + sources)
            evidence = (o.get("metadata") or {}).get("evidence") or {}
            if evidence.get("review_html"):
                review = resolve_path(evidence["review_html"])
                relative = Path(os.path.relpath(review, out_dir or ROOT)).as_posix()
                notes.append(f"[Saved page]({relative}) (evidence {evidence.get('status', 'unknown')})")
            question = o.get("prompt_id") or _cell(o.get("prompt"), 40) or ("prompt box" if kind == "browser" else "")
            attempts = o.get("attempts", 1) if kind == "browser" else ""
            rows.append(f"| {kind} | {o.get('provider', '')} | {question} | {o.get('status', '')} | {attempts} "
                        f"| {_cell(o.get('response'))} | {'; '.join(notes)} |")
    return rows

def _check_section(path: Path, out_dir: Path) -> tuple[str, str]:
    record = json.loads(path.read_text(encoding="utf-8"))
    started = record["started_at"]
    link = Path(os.path.relpath(path, out_dir)).as_posix()
    lines = [
        f'<a id="{path.stem}"></a>',
        f"## {started[:10]} {started[11:16]} · `{check_command(record, path.stem)}` · {'PASS' if record.get('ok') else 'FAIL'}",
        "",
        f"File: [{link}]({link})",
        "",
    ]
    steps = record.get("steps") or {}
    if record.get("errors"):
        lines += ["Failures:", ""]
        lines += [f"- {_cell(error)}" for error in record["errors"]]
        lines.append("")
    if record.get("diagnostics"):
        lines += ["| Diagnostic | Status | Detail |", "|---|---|---|"]
        for diagnostic in record["diagnostics"]:
            lines.append(f"| {_cell(diagnostic['name'])} | {_cell(diagnostic['status'])} | {_cell(diagnostic['detail'])} |")
        lines.append("")
    sandbox = steps.get("sandbox")
    if sandbox:
        # Stored repo-relative (results/checks/study-smoke/...); report is in results/checks/.
        sandbox_path = Path(sandbox) if Path(sandbox).is_absolute() else ROOT / sandbox
        try:
            sandbox_rel = Path(os.path.relpath(sandbox_path, out_dir)).as_posix()
        except ValueError:
            sandbox_rel = str(sandbox)
        outcome_bits = [f"[sandbox]({sandbox_rel}/)"]
        if (sandbox_path / "results.csv").exists():
            outcome_bits.append(f"[results.csv]({sandbox_rel}/results.csv)")
        run_name = (steps.get("first_run") or {}).get("run_json")
        if run_name and (sandbox_path / "runs" / run_name).exists():
            outcome_bits.append(f"[run JSON]({sandbox_rel}/runs/{run_name})")
        lines += [f"Outcomes: {', '.join(outcome_bits)}.", ""]
    for entry in record.get("locations", []):
        lines += [f"### {entry['location']}", "", _vpn_line(entry.get("vpn", {})), ""]
        rows = _observation_rows(entry, out_dir)
        if rows:
            lines += ["| Collector | App | Question | Status | Attempts | Answer | Notes |",
                      "|---|---|---|---|---|---|---|", *rows, ""]
        else:
            lines += ["Nothing was asked at this location.", ""]
    if not record.get("locations") and path.stem.endswith(("_browser-study", "_launchagent-study")):
        lines += ["No chatbot answers embedded in this summary; open the Outcomes links above.", ""]
    return started, "\n".join(lines)

def _csv_sections(path: Path, out_dir: Path, anchor_prefix: str, title: str) -> list[tuple[str, str]]:
    """One section per study day in a results CSV."""
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as f:
        by_day = defaultdict(list)
        for row in csv.DictReader(f):
            by_day[row["day"]].append(row)
    link = Path(os.path.relpath(path, out_dir)).as_posix()
    sections = []
    for day, rows in by_day.items():
        started = min(r["timestamp"] for r in rows)
        failed = sum(1 for r in rows if r["status"] != "ok")
        lines = [
            f'<a id="{anchor_prefix}day-{day}"></a>',
            f"## {started[:10]} {started[11:16]} · {title} day {day} · {len(rows)} rows, {failed} failed",
            "",
            f"File: [{link}]({link}). Question {rows[0]['question_id']}; answers are shortened here.",
            "",
            "| Location | Exit IP | Collector | App | Language | Status | Answer | Sources |",
            "|---|---|---|---|---|---|---|---|",
        ]
        for r in sorted(rows, key=lambda r: r["timestamp"]):
            answer = _cell(r["response"], ANSWER_CHARS) or _first_line(r["error"])
            lines.append(f"| {r['vpn_location']} | `{r['exit_ip']}` | {r['collector']} | {r['provider']} "
                         f"| {r['language']} | {r['status']} | {answer} | {_source_links(r)} |")
        sections.append((started, "\n".join(lines) + "\n"))
    return sections

def details_path(cfg: dict, source: Path | None = None) -> Path:
    study = resolve_path(cfg["results"].get("details_path", "results/results.md"))
    if source is not None:
        checks_dir = resolve_path(cfg["results"].get("checks_dir", "results/checks"))
        if source.parent == checks_dir:
            return checks_dir / "results.md"
    return study


def _write_report(cfg, out, title, description, sections, empty):
    out.parent.mkdir(parents=True, exist_ok=True)
    index = resolve_path(cfg["results"].get("index_path", "results/README.md"))
    link = Path(os.path.relpath(index, out.parent)).as_posix()
    header = (f"# {title}\n\n{description}\n\n"
              f"Generated from saved results; do not edit by hand. Run index: [README.md]({link}).\n\n")
    sections.sort(key=lambda section: section[0], reverse=True)
    out.write_text(header + ("\n".join(s for _, s in sections) if sections else empty + "\n"), encoding="utf-8")


def write_details(cfg: dict | None = None) -> Path:
    cfg = cfg or load_config()
    out = details_path(cfg)
    checks_dir = resolve_path(cfg["results"].get("checks_dir", "results/checks"))
    runs_dir = resolve_path(cfg["results"].get("runs_dir", "results/runs"))
    csv_path = resolve_path(cfg["results"]["csv_path"])
    study_sections = [_check_section(p, out.parent) for p in sorted(runs_dir.glob("*.json"))]
    study_sections += _csv_sections(csv_path, out.parent, "", "`run-day`")
    _write_report(cfg, out, "Study results",
                  "Scheduled and manual study collection only. Full answers are in the linked CSV and run JSON files. "
                  "Setup check answers are in checks/results.md. Archived pilot data is excluded.",
                  study_sections, "No study runs have been collected yet.")

    check_report = checks_dir / "results.md"
    check_sections = [_check_section(p, checks_dir) for p in sorted(checks_dir.glob("*.json"))]
    _write_report(cfg, check_report, "Check results",
                  "Setup checks only, including historical checks and study-question smoke tests. "
                  "These answers are separate from scheduled study data. Attempts counts browser attempts; "
                  "CAPTCHA interventions and errors appear in Notes.",
                  check_sections, "No checks have been recorded yet.")

    return out

if __name__ == "__main__":
    print(write_details())
