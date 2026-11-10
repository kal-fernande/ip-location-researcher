# Results

Log of every check and data-collection run, newest first. Each row is added automatically. When links to the appropriate report: [study results](results.md) or [checks](checks/results.md). The [archived pilot CSV](pilot-historical-data/results.csv) is historical data and is not updated by collection. Run gives the `python main.py` command and, in brackets, what it covered. Each command name links to the line in `main.py` that runs it.

- [`check`](../main.py#L59): connects the VPN to each location, verifies the exit country, then asks the two check questions (country, emergency number) through each enabled API provider. `--no-api` skips the API; `--browser` also asks in each web app. Writes `checks/<time>.json`.
- [`check-browser`](../main.py#L62): connects the VPN to each location, then opens each enabled web app and looks for a prompt box. Sends no prompt. Writes `checks/<time>_browser.json`.
- [`check-browser-prompt`](../main.py#L65): connects the VPN to each location, then asks the two check questions in each enabled web app, without the API. Writes `checks/<time>_browser-prompt.json`.
- [`check-browser-study`](../main.py#L68): sandboxed one-day browser study smoke test (`run-due` + CSV/runs + skip + next-day draw). Writes `checks/<time>_browser-study.json` and artifacts under `checks/study-smoke/` ([folder README](checks/study-smoke/README.md)). Does not touch the active study under `studies/`.
- [`collect-manual`](../main.py#L71): connects the VPN to each location; a person asks the questions in a web app by hand and pastes the answers. Without `--day`, the two check questions, written to `checks/<time>_manual-<provider>.json`; with `--day N`, that day's question, appended to the active study CSV. See [`docs/manual-collection.md`](../docs/manual-collection.md).
- [`run-day`](../main.py#L51): collects study data for one day into the active study folder (`studies/<id>/results.csv` and `runs/`). `run-due` and `daemon` run it on schedule and log it as `run-day`. Create a study with `start-study`.

Checks never write live study data. See [`docs/checks.md`](../docs/checks.md) and [`docs/testing.md`](../docs/testing.md). CSV columns: [`docs/results-csv.md`](../docs/results-csv.md). Studies: [`studies/README.md`](studies/README.md).

| When | Run | Outcome | File |
|---|---|---|---|
| [2026-11-10 13:08](checks/results.md#2026-11-10_130850_browser) | `check-browser` (vpn, browser) | PASS | [browser.json](checks/2026-11-10_130850_browser.json) |
| [2026-11-03 13:08](checks/results.md#2026-11-03_130840_browser) | `check-browser` (vpn, browser) | PASS | [browser.json](checks/2026-11-03_130840_browser.json) |
| [2026-11-03 13:07](checks/results.md#2026-11-03_130742_browser) | `check-browser` (vpn, browser) | PASS | [browser.json](checks/2026-11-03_130742_browser.json) |
