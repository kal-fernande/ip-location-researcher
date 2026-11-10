# IP-Location Chatbot Experiment

One project with two **collection methods**:

1. *API* — stateless API calls routed through the active VPN.
2. *Browser* — fresh Playwright browser context for every observation.

For web apps that cannot be used logged out (Claude), a person can collect answers by hand while the program switches the VPN; see [docs/manual-collection.md](docs/manual-collection.md).

The goal is to change the **source IP location** (US vs Israel) while holding the explicit prompt and other controllable variables fixed, then compare output.

## Start here

For the desktop interface, complete the setup below once, then double-click `Open Experiment.command` on macOS.
The app provides checks, the next run time, scheduled collection, and buttons to open the active study's CSV or Excel results.
Use `New study…` at the top of the window to create a separate study with an optional display name, first date, and notes.
Each study keeps its own results and schedule, and switching the active study preserves earlier study folders.
See the [desktop app guide](docs/gui.md) for everyday use and the other available tools.
From a terminal, launch it with `.venv/bin/python main.py gui`.

1. `source setup.sh` in each new terminal (see Setup below).
2. Log in to Mullvad (`mullvad account login`) and put the API key into `.env`.
3. Check the setup, see [docs/checks.md](docs/checks.md):
   - `python main.py check-browser`,
   - `python main.py check-browser-prompt`,
   - `python main.py check-browser-study` .
4. Read check outcomes in [results/README.md](results/README.md) (details in [results/checks/results.md](results/checks/results.md)).

### Study status (2026-11-02)

The browser campaign [`2026-10-24_browser`](results/studies/2026-10-24_browser/) is ended.
The LaunchAgent is uninstalled and there is no `results/studies/ACTIVE` pointer.
Write-up: [`docs/study-report-2026-10-24.md`](docs/study-report-2026-10-24.md).

### Start a new study run (only when collecting again)

`setup.sh` and `main.py` do **not** start automatic collection.
When checks look good and you want a scheduled browser study:

1. Choose policies in `config.yaml` (`schedule_policy`, `recovery_policy`, optional `alerts`) — see [docs/scheduling.md](docs/scheduling.md).
2. Click `New study…` in the desktop app and choose `Create study`, or run `python main.py start-study --name "Follow-up study"` to create a separate study folder and schedule day 1.
   The display name is optional and can contain spaces.
   The app asks before switching away from an active study; the terminal command refuses unless `--force` is supplied.
   Previous study folders remain available, and `results/studies/ACTIVE` identifies the newly active study.
   Do not regenerate the schedule mid-study.
3. Follow the [macOS setup guide](docs/scheduling.md#macos-setup-and-launchagent-configuration) and install the LaunchAgent once: `python scripts/install_scheduler.py --install`
4. Verify startup with `python main.py check-scheduler --probe`.
5. Keep the Mac awake and logged in around each `run_at`. Every minute macOS runs `python main.py run-due --mode browser` against the active study. Logs: `private/scheduler/`. CAPTCHAs may still need a person — turn on Mac and/or phone alerts in [docs/scheduling.md](docs/scheduling.md#alerts).

Details, permissions, and how to stop: [Run the daily schedule](#run-the-daily-schedule).
Preview the plist without installing: omit `--install`.

[docs/overview.md](docs/overview.md) explains the experiment without code; [docs/README.md](docs/README.md) indexes every note.
## Known issues

- Gemini, logged out, often shows "Searching the web" or error boilerplate for a long time. The collector treats those as non-answers (keeps waiting; times out with `status: timeout` if they never become a real reply). Re-run `check-browser-study` after selector or UI changes; hand-check any Gemini `timeout` rows.
- Answers recorded before 2026-10-13 10:00 start with "Gemini said", a hidden label that the collector read by mistake.
- The web apps change their pages; when an app's answers come back as `timeout` or `ui_error` although the screenshot in `results/failures/` shows an answer, its selectors in `experiment/browser_collectors.py` need updating.

## Documentation

[docs/README.md](docs/README.md) is the full index (one click from here).
Short map:

- [CHANGELOG.md](CHANGELOG.md): program and doc changes on the current merge branch.
- [docs/overview.md](docs/overview.md): the experiment for readers without a programming background.
- [docs/checks.md](docs/checks.md) / [docs/testing.md](docs/testing.md): what to run before collecting data.
- [docs/scheduling.md](docs/scheduling.md): macOS setup and diagnostics, random vs agenda times, recovery, and alerts.
- [docs/manual-collection.md](docs/manual-collection.md): hand collection (Claude).
- [docs/results-csv.md](docs/results-csv.md): data dictionary for every `results.csv` column.
- [docs/browser-evidence.md](docs/browser-evidence.md): review saved pages offline in Chromium and validate captured answers, links, and exports.
- [notes/](notes/): Mullvad choice, logged-out access, automation policy, CAPTCHA counts.
- [results/checks/study-smoke/README.md](results/checks/study-smoke/README.md): sandboxed one-day study outputs to inspect before a live run.
- [results/README.md](results/README.md): every check and data-collection run; study-only and checks-only summaries sit beside it.
- [Archived pilot CSV](results/pilot-historical-data/results.csv): historical API archive only; current collection does not use this folder.

## Default design

The current study targets **New York, US** (`us-nyc-wg-001`) and **Tel Aviv, Israel** (`il-tlv-wg-101`), configured in `config.yaml`.
Earlier checks and the setup examples below used France; their historical results retain the `FR` label.
Prompt languages are English and Hebrew.

- Browser sources: ChatGPT, Gemini, Google Search AI Overview
- Languages: English, Hebrew
- Locations: US (New York), Israel (Tel Aviv)
- 10 days
- 1 semantic question/day
- 12 browser observations/day from three sources × two languages × two locations; Claude's web app needs a login and is collected by hand, if at all
- API currently enables only Anthropic. OpenAI and Gemini adapters are included but disabled in `config.yaml`.
- Browser locale and timezone are fixed across both VPN conditions so IP is the geographic manipulation.

**Remark:** The browser app and the API may not run the same LLM, and the app adds its own layer (system prompt, search, possibly location) on top.

## Setup

For automatic collection, complete the [macOS scheduling setup](docs/scheduling.md#macos-setup-and-launchagent-configuration) after the environment setup below.

Requires Python 3.10+.
From the project folder, run:

```bash
source setup.sh
```

The script creates `.venv` (with Python 3.10+) if it is missing, activates it in your shell, installs the dependencies and Chromium for Playwright, creates `.env` if it is missing, and prints which `python` it uses.
Run it again in each new terminal; otherwise `python` may be another installation, such as Anaconda's, with an older Playwright and no browser.
Put the API key for each provider enabled under `api: providers:` in `config.yaml` into `.env`.

### VPN on only during collection

Each `run-day` / check connects Mullvad for the locations it needs, then disconnects when the run ends (including on SIGINT/SIGTERM).
`vpn.max_session_seconds` (default 7200) is a hard wall-clock: after that the tunnel is dropped and collection aborts so a hung browser wait cannot leave the VPN up for days.
To disconnect now without abandoning the active study:

```bash
python main.py vpn-off
```

That leaves `ACTIVE`, `schedule.json`, and the CSV alone; incomplete cells can resume on the next `run-due`.

### VPN: Mullvad

The experiment switches location with Mullvad's command-line tool, which works on macOS and Linux.
`notes/vpn-providers.md` explains the choice.

1. Install the app, which includes the `mullvad` command: `brew install --cask mullvad-vpn` on macOS, or the package from mullvad.net on Linux.
2. Buy time on mullvad.net and log in: `mullvad account login`.
3. Check: `mullvad status` should print the connection state.

`config.yaml` already contains the Mullvad commands.
Each location is pinned to one server (`us-nyc-wg-001`, `il-tlv-wg-101`) through `vpn_target` to keep the exit IP consistent across runs.
The New York server is Mullvad-owned; the Tel Aviv server is rented from DataPacket.
`mullvad relay list` shows the alternatives; `vpn_target` also accepts a plain country code such as `us`.

### Change or add countries

The countries are set only in `config.yaml`, under `locations:`; no script names a country.
Each block is one condition:

```yaml
locations:
  US:                                    # label of your choice; it names the condition in the results
    vpn_target: "us nyc us-nyc-wg-001"   # from `mullvad relay list`: country, city, server
    expected_country_code: "US"          # what the IP lookup must report
    focus_terms: ["united states", "congress", ...]  # words showing an answer is about this country
```

To add Germany, for example, add a block:

```yaml
  DE:
    vpn_target: "de fra de-fra-wg-001"
    expected_country_code: "DE"
    focus_terms: ["germany", "german", "deutschland", "bundestag", "berlin"]
```

Every command then visits all listed locations: the checks in the order of the file, the study days in a random order per day.
The analysis compares every pair of locations (with three countries: US–IL, US–DE, IL–DE) and labels each answer with the location whose `focus_terms` it mentions most.

Renaming a label (`US` to `USA`) works for new data, but rows already collected keep the old label, so rename before the study starts.

Languages are separate from countries: every question is asked in every language at every location.
To add one, add it under `languages:` in `config.yaml` (`de: "German"`) and give each question in `questions.json` a `de` field.

## Questions

Edit `questions.json`.

The current file repeats one semantic question in English (`en`) and Hebrew (`he`) across the configured day entries.
Each question must contain a field for every language enabled in `config.yaml`.

Pre-write translations before the study.
Do not generate translations dynamically during collection.

## Check the setup

```bash
python main.py check
python main.py check-browser
python main.py check-browser-prompt
python main.py check-browser-study   # sandboxed one-day study pipeline; see docs/checks.md
```

In addition to the two diagnostic prompts below, prompt checks read day 1 from `questions.json` and ask it in each configured language (currently English and Hebrew).
Editing that file updates future checks automatically.
Each prompt is sent in its own conversation and check answers stay separate from study data.

Run this before collecting data.
For each configured location (currently US, then Israel) it:

1. connects the VPN and verifies the exit country with the configured IP lookup services;
2. asks each enabled API provider two questions, each in its own conversation:
   - `Which country am I in? Answer with the country name only, or "unknown" if you cannot tell.`
   - `What number do I call in an emergency? Answer with the number only.` (the appropriate local emergency service number)

The API receives no location, so neither answer should depend on the VPN: `unknown` for the first, and no systematic location difference for the second.
Historical output from the earlier US/France setup:

```
=== VPN -> US ===
exit_ip=23.165.56.216 detected=US verified=True
API anthropic [country]: status=ok model=claude-sonnet-5-5 latency_ms=2115 response='unknown'
API anthropic [emergency]: status=ok model=claude-sonnet-5-5 latency_ms=2138 response='911'
=== VPN -> FR ===
exit_ip=193.32.126.204 detected=FR verified=True
API anthropic [country]: status=ok model=claude-sonnet-5-5 latency_ms=1977 response='unknown'
API anthropic [emergency]: status=ok model=claude-sonnet-5-5 latency_ms=3333 response='911 or 112'
PASS — written to results/checks/2026-10-12_112734.json
```

`python main.py check --no-api` checks only the VPN.
`python main.py check --browser` also asks both check questions in each web app, logged out, where the answers may depend on location.
`python main.py check-browser` only checks whether the enabled browser apps expose a usable prompt box, without submitting a prompt.
`python main.py check-browser-prompt` asks both check questions in each web app without the API and records each answer.
Each run is saved as one JSON file in `results/checks/`.
The checks never write study data.
See `docs/testing.md`.

`results/README.md` lists every check and data-collection run, newest first, with a link to its file.
`check`, `check-browser`, `check-browser-prompt` and `run-day` add their line automatically, with the command that was run.
All generated files under `results/`, including study data, check JSON, schedules, manifests, summaries, screenshots, and analysis outputs, are local files excluded from Git and automatic commits.
They are not included in future GitHub pushes.
Existing local files are preserved, and results published in older commits remain in that published history.

## Create the schedule

```bash
python main.py schedule                 # first run tomorrow
# Or choose the first date:
python main.py schedule --start-date 2026-10-19
```

This creates only the first day's random time and location order in the active study's `results/studies/<id>/schedule.json`.
After each scheduled run finishes, the scheduler draws the next run's time for the following local calendar day.
Times may repeat by chance.
`--seed` optionally makes the initial draw reproducible; later draws are fresh.

Set the total study length in one place: `experiment.days` in `config.yaml`.
The scheduler reads it on every check, so increasing it extends the study and reducing it stops collection beyond that day number.
No schedule regeneration or launcher reinstall is needed.
Explicit per-day questions are used when available; for extra days, the existing question list cycles automatically.
Changing the study length does not require adding question entries.

## Run a single day

If a scheduled browser run timed out or left missing answers, use `Manual run / retry…` in the desktop app, or run `python main.py retry-day --day 3` for the affected day.
This retries missing or failed answers while retaining successful observations and the next scheduled time.
Selecting a day with all successful answers reports that it is already complete and collects nothing.
See [manual retry instructions](docs/gui.md#retry-a-missed-or-incomplete-study-run).

```bash
python main.py run-day --day 1 --mode api       # or browser, or both
```

This collects study data in both formats:

- `results/studies/<id>/results.csv`: cumulative rows, one per newly collected answer.
- `results/studies/<id>/runs/<timestamp>_day-<N>_<mode>.json`: a separate file for each run, matching the check JSON structure with run times, `ok`, and VPN locations containing `api`/`browser` answers. It also records the day, mode, planned `run_at`, question IDs, languages, and any error. Browser entries retain retries, screenshots, and CAPTCHA intervention metadata.

The CSV starts with headers and fills as study data is collected.
The active study id in `results/studies/ACTIVE` determines the output folder.
Each append also refreshes a sibling `results.xlsx` with the same cells.
Each study's folder also keeps its own `schedule.json`, `manifest.json`, run JSON records, and `analysis/` outputs.
The manifest records the optional display name and notes independently of the generated folder ID.
Captured source links are stored in the CSV's `source_urls` column, one URL per line, and as labeled `sources` in JSON.
The answer text stays unchanged.
To recover available URLs into existing study rows without collecting again, run `python main.py refresh-links`.
Links that were never saved cannot be recovered.
See [the CSV dictionary](docs/results-csv.md).
Run indexes link to CSV and JSON outputs.
Aborted collection saves partial JSON results; reruns get a new JSON file and list existing observations under `skipped` without duplicating CSV rows.
A later run of the same day skips existing rows.
To test the setup, use `python main.py check` for VPN/API, `python main.py check-browser` for browser app readiness, and `python main.py check-browser-prompt` for browser answers.

The program verifies the VPN country before collecting.
If verification fails, it aborts rather than contaminating the condition.

## Run the daily schedule

Follow [macOS setup and LaunchAgent configuration](docs/scheduling.md#macos-setup-and-launchagent-configuration) for prerequisites, installed plist settings, permissions, diagnostics, and stop/reinstall instructions.
The installed plist is local to each Mac; `scripts/install_scheduler.py` is the reproducible definition tracked here.

```bash
python scripts/install_scheduler.py --install  # once per Mac
python main.py check-scheduler --probe          # verify background startup
```

The agent checks the active study's `schedule.json` at each minute boundary using browser mode.
The saved `run_at` is the source of truth for the next time.
Random/agenda scheduling, recovery, and alerts are configured in `config.yaml`; see [scheduling.md](docs/scheduling.md).
Keep the Mac awake and logged in.
CAPTCHAs may require human action.

Without macOS automation, a terminal can run `python main.py daemon --mode browser`; on other systems an external scheduler can invoke `python main.py run-due --mode browser`.
Do not run multiple schedulers or VPN collectors concurrently.

## Browser behavior

Every browser observation gets a completely new Playwright context:

- no conversation history
- no cookies from the previous observation
- no local storage from the previous observation
- no geolocation permission
- fixed browser locale and timezone

The browser windows are visible (`headless: false`), so a person can solve a CAPTCHA.
On macOS, with `browser.stay_in_background: true`, each new window gives the focus back to the app you were using; click the window to use it.
When a CAPTCHA appears, the wait path brings that window forward once and prints `HUMAN ACTION NEEDED` (optional phone ping via `experiment.alerts`).

If a site presents a CAPTCHA/human-verification page, the script **does not attempt to bypass it**.
With `browser.human_captcha_enabled: true` and `browser.headless: false`, it leaves the browser open, prints a `HUMAN ACTION NEEDED` notice, and waits up to `human_captcha_timeout_seconds` for a person to complete the visible check.
If the prompt box appears afterward, collection resumes automatically; otherwise it records `status=captcha`.

If a site requires login and no anonymous prompt box is available, it records `status=login_required`.

Each attempt waits up to `browser.timeout_seconds` for an answer.
After a timeout or UI error, the question is asked again in a fresh browser, up to `browser.retries` times; CAPTCHAs and login walls are not retried.
The results record the number of attempts.
Apps differ in speed, so `timeout_seconds`, `retries`, `prompt_wait_seconds` and `pause_after_submit_seconds` can also be set under an app's entry in `browser.providers`, which overrides the shared value.
Gemini gets `timeout_seconds: 180`, because logged out it takes about a minute per answer.

Consumer UI selectors can change.
A `ui_error` usually means a selector needs updating in `experiment/browser_collectors.py`.

### Google Search AI Overview

Google Search is enabled as `browser.providers.google_search`.
It searches the exact study question and captures the **AI Overview above ordinary results**.
Gemini remains a separate source.
Google Search uses the same VPN, fresh browser, fixed locale/timezone, schedule, CSV, and run JSON as the other sources.

An overview is not guaranteed.
`no_ai_overview` records that none appeared during the wait period; `captcha` records a verification block.
Neither is saved as a successful answer.
The JSON's `metadata` includes the search URL and cited links found inside the overview; the CSV keeps plain answer text, status, and `source_urls`.

To check Google alone, without collecting live study rows:

```bash
python main.py check-browser --provider google_search
python main.py check-browser-prompt --provider google_search --study-only
```

Read [Google Search collection](docs/google-search.md) for output examples, test coverage, and the current live-verification limit.
Completed study days are not reopened automatically when a new source is enabled.

## Enable another API provider

Add its key to `.env` (`OPENAI_API_KEY`, `GEMINI_API_KEY` or `ANTHROPIC_API_KEY`), then set `enabled: true` for it under `api: providers:` in `config.yaml`.
Gemini currently has free-tier models.

## Analyze

```bash
python main.py analyze
```

Outputs for the active study:

- `results/studies/<id>/analysis/location_pairs.csv`
  - US vs Israel within the same collector/provider/language/day
- `results/studies/<id>/analysis/method_pairs.csv`
  - API vs browser at the same location/provider/language/day
- `results/studies/<id>/analysis/summary.csv`
- `results/studies/<id>/analysis/status_summary.csv`
- `results/studies/<id>/analysis/report.txt`

The first-pass analysis reports text similarity and a simple heuristic for whether the answer focuses on US or Israeli politics.
It is intentionally descriptive; manually validate the country labels before treating them as research findings.

## Stronger inference

With `replicates: 1`, ordinary LLM stochasticity is still a confound.
If the assignment permits it, repeated samples per condition would make the location comparison substantially stronger.
The starter runner currently uses one row per condition/day; if you decide to add replicates, extend the observation key to include replicate number.
