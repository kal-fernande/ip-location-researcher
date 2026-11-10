# CAPTCHAs in the setup checks

Empirical summary from every saved browser check under `results/checks/` (readiness checks `*_browser.json` and prompt checks `*_browser-prompt.json`), as of 2026-10-19.
Policy background: [the-matrix.md](the-matrix.md).
How the collector waits for a person: [docs/testing.md](../docs/testing.md).
Missed overnight CAPTCHAs in the live study: [docs/scheduling.md](../docs/scheduling.md).
Docs index: [docs/README.md](../docs/README.md).

## What the collector records

When a web app shows a CAPTCHA or similar human check:

1. With `browser.human_captcha_enabled: true` and a visible browser, the run prints `HUMAN ACTION NEEDED` and waits up to `human_captcha_timeout_seconds` (300).
2. If a person completes it and the prompt box appears, the observation is stored with `status: ok` and `manual_intervention: true`.
3. If nobody completes it in time (or intervention is disabled), the observation is stored with `status: captcha`.
4. The run continues to the next observation either way.

Reporting:

| Signal | Where it appears |
|---|---|
| `status: captcha` | Check/run JSON; CSV `status` on study rows; Notes in `results/checks/results.md` |
| Solved by a person | JSON field `manual_intervention: true`; Notes text “CAPTCHA solved by a person” |
| Failure screenshots | `results/failures/` (gitignored) |

Study and study-smoke CSVs include a `manual_intervention` column (same meaning as the JSON field), plus `human_wait_ms` and `human_timeout_seconds` when a CAPTCHA wait ran.
Older rows may leave it empty; count those from the run JSON or the Notes column in the generated reports.

## Counts across all browser observations

23 check JSON files contain **140** browser observations (ChatGPT, Gemini, Claude while it was still enabled).

| Provider | Observations | `status: captcha` | `manual_intervention: true` | `status: ok` / `ready` |
|---|---:|---:|---:|---:|
| chatgpt | 56 | 2 | 13 | 32 (`ok` 28 + `ready` 4) |
| gemini | 56 | 0 | 0 | 39 (`ok` 33 + `ready` 6) |
| claude | 28 | 13 | 0 | 0 usable chat in these checks |

Gemini never recorded a CAPTCHA in this corpus.
ChatGPT’s early readiness checks hit CAPTCHAs; later prompt checks usually reached `ok`, often after a person solved the challenge.
Claude’s CAPTCHAs (and `login_required`) dominate the hard failures until Claude was disabled for browser automation ([web-app-access.md](web-app-access.md)).

## Prompt checks (`check-browser-prompt`), run by run

These are the checks that ask questions (diagnostic probes, and from mid-study also day‑1 EN/HE).

| When | Outcome | Browser obs | `captcha` | Solved (`manual_intervention`) | Notes |
|---|---|---:|---:|---:|---|
| 2026-10-12 13:00 | FAIL | 12 | 2 | 0 | Claude CAPTCHA timed out; other apps mixed timeout/error |
| 2026-10-12 14:48 | FAIL | 12 | 0 | 0 | All `error` (not CAPTCHA) |
| 2026-10-12 17:01 | FAIL | 12 | 0 | 0 | All `error` |
| 2026-10-12 17:03 | FAIL | 12 | 2 | 0 | Claude CAPTCHA; ChatGPT partly `ok` |
| 2026-10-13 08:03 | FAIL | 12 | 3 | 0 | Claude CAPTCHA again |
| 2026-10-13 09:45 | FAIL | 8 | 0 | 0 | Claude already out of the enabled set; one Gemini timeout |
| 2026-10-13 10:16 | PASS | 8 | 0 | 0 | US/FR ChatGPT+Gemini, no CAPTCHA flag |
| 2026-10-18 23:48 | PASS | 8 | 0 | 4 | US/IL; all four ChatGPT prompts needed a person |
| 2026-10-18 23:57 | PASS | 16 | 0 | 7 | US/IL + day‑1 EN/HE; ChatGPT CAPTCHAs solved; Gemini none |
| 2026-10-19 08:05 | PASS | 16 | 0 | 0 | US/IL + day‑1 EN/HE; all 16 `ok` without intervention |

Readiness-only checks on 2026-10-12 (`*_browser.json`) already showed the same pattern: Claude and sometimes ChatGPT blocked at the prompt box; a person could clear ChatGPT (`ready` + `manual_intervention`) while Claude still timed out.

## What this means for the study

- CAPTCHAs are expected for **ChatGPT** more than Gemini in this setup (automated Chrome for Testing, fresh context every question).
- A PASS can still mean people solved CAPTCHAs; look for `manual_intervention` in the JSON, not only the PASS/FAIL line in [results/README.md](../results/README.md).
- If a study observation times out as `status: captcha`, it is written to `results.csv` and **skipped on later runs** (same day/provider/language/location key). The day can still finish and the scheduler can draw the next day; that cell stays empty unless someone removes the CSV row and re-runs.
- Unattended laptop sleep does not redraw the pending `run_at`; the overdue day runs when the Mac is awake again. CAPTCHAs still need a person at the screen when they appear.

## How to refresh these numbers

From the project root, after new checks land in `results/checks/`:

```bash
python - <<'PY'
import json
from pathlib import Path
from collections import Counter
browser = []
for path in sorted(Path("results/checks").glob("*browser*.json")):
    rec = json.loads(path.read_text())
    for loc in rec.get("locations", []):
        for o in loc.get("browser", []):
            browser.append(o)
print("n", len(browser))
print("status", Counter(o.get("status") for o in browser))
print("manual_intervention", sum(1 for o in browser if o.get("manual_intervention")))
print("by provider captcha", Counter(
    o.get("provider") for o in browser if o.get("status") == "captcha"))
PY
```
