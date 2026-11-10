from __future__ import annotations
import os
import re
from datetime import datetime
from pathlib import Path

from .config import resolve_path
from .details import details_path, write_details

# Strip leading run timestamps from index link labels (href keeps the full path).
_TIMESTAMP_PREFIX = re.compile(r"^\d{4}-\d{2}-\d{2}_\d{6}(?:_\d+)?_")


def _index_label(relative: str) -> str:
    name = Path(relative).name
    return _TIMESTAMP_PREFIX.sub("", name) or name

TABLE_HEAD = "| When | Run | Outcome | File |\n"
TABLE_RULE = "|---|---|---|---|\n"
HEADER = (
    "# Results\n\n"
    "Log of every check and data-collection run, newest first. Each row is added automatically. When links to the appropriate report: [study results](results.md) or [checks](checks/results.md). The [archived pilot CSV](pilot-historical-data/results.csv) is historical data and is not updated by collection. Run gives the `python main.py` command and, in brackets, what it covered. Each command name links to the line in `main.py` that runs it.\n"
    "\n"
    "- [`check`](../main.py#L59): connects the VPN to each location, verifies the exit country, then asks the two check questions (country, emergency number) through each enabled API provider. `--no-api` skips the API; `--browser` also asks in each web app. Writes `checks/<time>.json`.\n"
    "- [`check-browser`](../main.py#L62): connects the VPN to each location, then opens each enabled web app and looks for a prompt box. Sends no prompt. Writes `checks/<time>_browser.json`.\n"
    "- [`check-browser-prompt`](../main.py#L65): connects the VPN to each location, then asks the two check questions in each enabled web app, without the API. Writes `checks/<time>_browser-prompt.json`.\n"
    "- [`check-browser-study`](../main.py#L68): sandboxed one-day browser study smoke test (`run-due` + CSV/runs + skip + next-day draw). Writes `checks/<time>_browser-study.json` and artifacts under `checks/study-smoke/` ([folder README](checks/study-smoke/README.md)). Does not touch the active study under `studies/`.\n"
    "- [`collect-manual`](../main.py#L71): connects the VPN to each location; a person asks the questions in a web app by hand and pastes the answers. Without `--day`, the two check questions, written to `checks/<time>_manual-<provider>.json`; with `--day N`, that day's question, appended to the active study CSV. See [`docs/manual-collection.md`](../docs/manual-collection.md).\n"
    "- [`run-day`](../main.py#L51): collects study data for one day into the active study folder (`studies/<id>/results.csv` and `runs/`). `run-due` and `daemon` run it on schedule and log it as `run-day`. Create a study with `start-study`.\n"
    "\n"
    "Checks never write live study data. See [`docs/checks.md`](../docs/checks.md) and [`docs/testing.md`](../docs/testing.md). CSV columns: [`docs/results-csv.md`](../docs/results-csv.md). Studies: [`studies/README.md`](studies/README.md).\n"
    "\n"
    + TABLE_HEAD + TABLE_RULE
)

def index_path(cfg: dict) -> Path:
    return resolve_path(cfg["results"].get("index_path", "results/README.md"))

def add_entry(cfg: dict, when: datetime, run: str, outcome: str, file: Path, anchor: str | None = None,
              extra_files: list[Path] | None = None) -> None:
    """Insert one row at the top of the results index table, then rebuild
    the separate reports. With `anchor`, When links to the matching report."""
    path = index_path(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = path.read_text(encoding="utf-8") if path.exists() else HEADER
    link = Path(os.path.relpath(file, path.parent)).as_posix()
    stamp = f"{when:%Y-%m-%d %H:%M}"
    if anchor:
        details = Path(os.path.relpath(details_path(cfg, file), path.parent)).as_posix()
        stamp = f"[{stamp}]({details}#{anchor})"
    # Link text drops the datetime prefix; href keeps the path under results/.
    links = [f"[{_index_label(link)}]({link})"]
    for extra in extra_files or []:
        relative = Path(os.path.relpath(extra, path.parent)).as_posix()
        links.append(f"[{_index_label(relative)}]({relative})")
    row = f"| {stamp} | {run} | {outcome} | {', '.join(links)} |\n"
    if TABLE_RULE not in text:  # table deleted by hand: start a new one at the end
        text = text.rstrip("\n") + "\n\n" + TABLE_HEAD + TABLE_RULE
    head, sep, rest = text.partition(TABLE_RULE)
    path.write_text(head + sep + row + rest, encoding="utf-8")
    write_details(cfg)
