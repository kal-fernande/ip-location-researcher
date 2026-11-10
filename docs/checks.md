# What each check tests

The project has several ways to gain confidence before and during a study.
They are not interchangeable: each covers a different layer.
This note is the map; command details live in [testing.md](testing.md).

## Quick map

| Command / path | Real VPN + browsers? | Writes live `results.csv` / `schedule.json`? | Typical duration | Main question it answers |
|---|---|---|---|---|
| `python -m unittest discover -s tests` | No | No | seconds | Do scheduler, reports, and check wiring behave in isolation? |
| `python main.py check-scheduler [--probe]` | No | No | seconds | Does the macOS launch configuration match, and can a fresh background check start? |
| `python main.py check` | VPN (+ API) | No | minutes | Does the VPN switch and do API providers answer? |
| `python main.py check-browser` | Yes | No | minutes | Do ChatGPT, Gemini, and Google Search show a usable prompt box? |
| `python main.py check-browser-prompt` | Yes | No | ~10–30+ min | Can we collect diagnostic + day‑1 EN/HE answers at each location? |
| `python main.py check-browser-study --launchagent-delay` | Yes, after an automatic macOS timer | No (sandbox only) | 1 minute wait + collection | Does the LaunchAgent timer start and complete the study smoke test? |
| `python main.py check-browser-study` | Yes | No (sandbox only) | ~one study day of browser work | Does the **study pipeline** work end-to-end for one day? |
| `python main.py run-day --day N --mode browser` | Yes | **Yes** | one day | Collect real study data for day N |
| LaunchAgent / `run-due` / `daemon` | Yes | **Yes** | 10 scheduled days | Run the full study over time |

## Layers

### 1. Unit tests

No Mullvad, no Chromium.
They cover schedule draws, abort/retry, report generation, and that prompt checks load day 1 from `questions.json`.
They do not prove that a browser can reach ChatGPT from Tel Aviv.

### 2. Setup checks (`check`, `check-browser`, `check-browser-prompt`)

These share the check path in `experiment/checks.py`.
They verify VPN country codes and (for prompt checks) that web apps return answers.
Prompt checks also ask day 1’s study question in every configured language, so they smoke-test the **instrument**, but they:

- do not create or advance `schedule.json`
- do not write `results/results.csv` or `results/runs/`
- do not exercise skip-on-rerun or “draw next day after completion”

Safe to re-run any number of times.
Results go under `results/checks/`.

Use `--provider google_search` with `check-browser` or `check-browser-prompt`
to test Google alone. Readiness only checks its search box. A prompt check
requires an AI Overview for every query; `no_ai_overview` or `captcha` makes
it fail, with the outcome preserved in JSON. See [google-search.md](google-search.md).

### 3. macOS scheduler diagnostic (`check-scheduler`)

Checks configuration, saved schedule, translations, local write access, installed plist against the installer, and launchd status. `--probe` verifies a fresh successful no-op launch through the installed agent, only when no browser run is due soon.
Results, including failed diagnostics, are saved as scheduler check JSON and shown in the check report.
It does not collect study data or advance the schedule.
See [macOS setup](scheduling.md#macos-setup-and-launchagent-configuration) for limitations, permissions, and reproducible installation instructions.

### 4. Study pipeline smoke (`check-browser-study`)

This is the gap filler between prompt checks and a real 10‑day study.

It runs the **same** `run_due` → `run_day` code as the study, in browser mode, but redirects outputs into `results/checks/study-smoke/<timestamp>/`:

1. Builds a sandbox schedule with day 1 already due
2. Runs `run-due --mode browser` (collects day 1 into sandbox CSV + run JSON)
3. Re-runs day 1 and expects every observation key to be **skipped**
4. Asserts day 1 is marked complete and day 2 was **drawn** (study length is temporarily 2 for this check only)
5. Asserts the active study’s `schedule.json` and `results.csv` were not modified (or the legacy root paths if no study is active)

PASS/FAIL is recorded like other checks as `results/checks/<timestamp>_browser-study.json`.
That summary embeds the first-run `locations` (chatbot answers) so `results/checks/results.md` can show them, and the results index File column links the sandbox `results.csv` and run JSON.
Sandbox folders under `results/checks/study-smoke/` are tracked so the CSV and run JSON stay with the PASS summary.
Browse saved sandboxes (and what each file means) in [results/checks/study-smoke/README.md](../results/checks/study-smoke/README.md).

On macOS, add `--launchagent-delay` to run this same check through an automatic calendar-based LaunchAgent timer after at least one minute.
The temporary agent copies the production agent's calendar minute trigger, background classification, environment, and GUI-session settings, with immediate startup disabled.
It uses a separate label and test outputs; there is no `kickstart`.
Its worker collects at most once even if the minute trigger fires again.
A tick during the first 60 seconds waits for the next minute; it does not consume the one-time collection attempt.
The supervising terminal waits for completion and unloads the agent afterward.
Logs and the generated temporary plist live under ignored `private/scheduler/study-smoke-<timestamp>/`; timing, failures, cleanup status, and answers are saved in check JSON.
Startup is normally 60–120 seconds after setup, with a 90-second grace period after the minimum one-minute delay; collection has a one-hour timeout.
Keep the terminal open.

A failed startup, collection, or cleanup is a FAIL.
The check refuses to overlap a live run due within its timeout.
It does not keep the Mac awake or prove startup after reboot/login.
If interrupted, inspect the summary for cleanup instructions.
Sudden power loss or forcibly killing the supervising terminal can prevent cleanup; the one-time worker guard prevents repeated collection.
The temporary agent is not installed for future logins.

Without this flag, study smoke does not test LaunchAgent initiation.
Neither form tests lid-closed sleep, CAPTCHA rates over many days, or ten full days of collection.
Those remain operations and the real study.

### 5. Real study collection

`run-day`, `run-due`, `daemon`, and the macOS LaunchAgent write the live CSV and schedule.
Use them when you intend to keep the data.
A `check-scheduler --probe` PASS, a `check-browser-prompt` PASS, and a `check-browser-study` PASS form the usual gate before relying on the scheduler for the 10‑day run.

## Suggested order before a study

```bash
source setup.sh
python -m unittest discover -s tests -v
python main.py check-browser-prompt   # instrument + location probes
python main.py check-browser-study    # study I/O + schedule state machine
# then follow scheduling.md to install and verify the LaunchAgent
python main.py check-scheduler --probe # after installation, no run due soon
```

After the gate above: policies and LaunchAgent in [scheduling.md](scheduling.md).
CAPTCHA counts from historical checks: [notes/captchas.md](../notes/captchas.md).
Index of all docs: [README.md](README.md).
