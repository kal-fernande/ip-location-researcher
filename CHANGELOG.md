# Changelog

Notable changes included in `main`.
Each item is one sentence; links point at the code or doc that carries the change.

## Stop a study (2026-11-10)

- `Stop study…` in the desktop app cancels the active study's future runs, stops ongoing collection with VPN cleanup, and keeps its saved files so the researcher can create a separate new study ([`docs/gui.md`](docs/gui.md#stop-a-study-and-begin-again)).
- The matching `stop-study` command and a persistent stop marker prevent an old scheduler invocation from resuming the stopped study ([`experiment/study.py`](experiment/study.py), [`experiment/runner.py`](experiment/runner.py)).

## Telegram phone alerts (2026-11-09)

- Phone alerts can use Telegram (`provider: telegram`) with a bot token and chat id in Phone alerts…, `.env`, or config ([`experiment/alerts.py`](experiment/alerts.py), [`docs/gui.md`](docs/gui.md)).

## ntfy priority (2026-11-09)

- Phone pushes use ntfy `Priority: urgent` so iOS is more likely to show a banner ([`experiment/alerts.py`](experiment/alerts.py)).

## Desktop Tk setup (2026-11-09)

- [`setup.sh`](setup.sh) installs Homebrew `python-tk@X.Y` when the project Python lacks Tk 8.6+, so `python main.py gui` works after setup without a separate Tk step.
- When `_tkinter` is still missing, [`main.py`](main.py) reuses another Tk-capable Python for the window and keeps collection in the project venv ([`experiment/gui_support.py`](experiment/gui_support.py)).

## Study ended (2026-11-02)

- Automatic collection for [`2026-10-24_browser`](results/studies/2026-10-24_browser/) stopped: LaunchAgent removed, `results/studies/ACTIVE` cleared, day 10 left partial.
- First-pass write-up: [`docs/study-report-2026-10-24.md`](docs/study-report-2026-10-24.md); analysis tables under [`results/analysis/`](results/analysis/).
- The merge with the parallel copy of `2026-10-24_browser` keeps both observation sets in one CSV and stores the other schedule as `schedule-parallel-collector.json`.

## Browser evidence (2026-10-26)

- Every browser observation now saves the same raw HTML, MHTML archive, screenshot, exact-answer files, citation evidence, and offline review page under its study/check folder.
- `validate-evidence` checks saved-file hashes, answer/source consistency, citation support, control counts, and matching CSV/Excel rows without visiting live websites.
- The [full live check](results/checks/2026-10-26_121516_browser-prompt.json) passes all 12 provider/language/location cases, and all 12 review pages and original archives reopen successfully in offline Chromium.
- Raw evidence remains local and ignored by Git; its paths and extracted citation data remain in JSON results.

## ChatGPT citations (2026-10-26)

- Read declared citation/link components from the actual browser response, including responses whose connections remain open after rendering.
- Match the components to the displayed assistant message and preserve the first completed answer if citation extraction fails.
- The [four-case live check](results/checks/2026-10-26_115226_browser-prompt.json) passes with 8, 5, 9, and 4 source URLs; 106 unit tests and 22 local browser fixtures pass.

## Google Search milestone (2026-10-26)

- Google Search AI Overview joins ChatGPT and Gemini in the browser study, with [study-only checks](docs/google-search.md) and saved source URLs.
- The merge preserves CAPTCHA timing fields, Excel mirrors, and active-study folders; parallel collections that shared `2026-10-19_browser` are split so Kalyan's runs stay there and Alexander's runs live under [`2026-10-24_browser`](results/studies/2026-10-24_browser/) with separate `schedule.json` files.
- Generated CSV, Excel, and Markdown summaries in study folders follow the ignore policy for the earlier root-level summaries; run JSONs remain tracked.
- The merge passed 94 unit tests and 21 local browser fixtures; its live ChatGPT citation issue is addressed by the browser-response fix above.

## Program

- Live collection now lives under [`results/studies/<id>/`](results/studies/README.md) with an [`ACTIVE`](results/studies/ACTIVE) pointer, via [`experiment/study.py`](experiment/study.py) and the CLI commands [`python main.py start-study`](main.py) / [`python main.py study-status`](main.py).
- [`python main.py start-study`](main.py) refuses to replace an existing active study unless you pass `--force` ([`experiment/study.py`](experiment/study.py)).
- [`python main.py run-due`](main.py) and CLI [`run-day`](main.py) take [`private/scheduler/collection.lock`](experiment/run_lock.py) so a LaunchAgent tick cannot start a second collector during a CAPTCHA wait ([`experiment/run_lock.py`](experiment/run_lock.py), [`experiment/runner.py`](experiment/runner.py)).
- Browser CAPTCHA waits write [`human_wait_ms`](docs/results-csv.md) and [`human_timeout_seconds`](docs/results-csv.md) on each CSV row ([`experiment/browser_collectors.py`](experiment/browser_collectors.py), [`experiment/storage.py`](experiment/storage.py)).
- Each [`append_result`](experiment/storage.py) also rewrites a sibling [`results.xlsx`](docs/results-csv.md) so Windows Excel opens Hebrew cleanly.
- With `alerts.advance_notice_minutes` (default 10), [`run-due`](main.py) sends one upcoming-run ping before a scheduled day ([`experiment/alerts.py`](experiment/alerts.py), [`experiment/runner.py`](experiment/runner.py)).
- [`scripts/install_scheduler.py`](scripts/install_scheduler.py) and scheduler checks require an active study schedule under `results/studies/` before installing or probing the LaunchAgent.

## Documentation

- [`docs/scheduling.md`](docs/scheduling.md) now states who creates the schedule (`start-study` / `schedule`), how the LaunchAgent polls it, Full Disk Access paths, the collection lock, and CAPTCHA wait CSV fields.
- After `source setup.sh`, command examples use bare `python` (project venv) in [`docs/scheduling.md`](docs/scheduling.md), [`README.md`](README.md), [`docs/testing.md`](docs/testing.md), and [`docs/checks.md`](docs/checks.md).
- [`docs/results-csv.md`](docs/results-csv.md) documents the per-study CSV paths and the new human-wait columns.
- Technical Markdown uses one sentence per line and sparse bold ([`.cursor/rules/document-line-breaks.mdc`](.cursor/rules/document-line-breaks.mdc), [`.cursor/rules/document-emphasis.mdc`](.cursor/rules/document-emphasis.mdc), [`AGENTS.md`](AGENTS.md)).
