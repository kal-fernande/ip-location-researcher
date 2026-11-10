# Study report: `2026-10-24_browser`

Status: ended 2026-11-02.
Automatic collection is off: the LaunchAgent was uninstalled and `results/studies/ACTIVE` was cleared.
Raw rows remain under [`results/studies/2026-10-24_browser/`](../results/studies/2026-10-24_browser/); first-pass analysis under [`results/analysis/`](../results/analysis/).

A 2026-11-05 merge with the other copy of this study id unioned that copy's observations into the same CSV.
The counts below describe this machine's 107 rows.
The merged file has 167 rows: 16 shared (days 1–2), 91 only from this machine (days 3–10), and 60 only from the parallel collector (days 3–7).
`schedule.json` remains this machine's 10-day timeline.
The other timeline is [`schedule-parallel-collector.json`](../results/studies/2026-10-24_browser/schedule-parallel-collector.json).
`results/analysis/` was computed before that union.

## Question

Do logged-out chatbot web apps give different answers when the source IP is New York (US) versus Tel Aviv (IL), with the prompt and other controllable browser settings held fixed?

## Design (this campaign)

| Factor | Setting |
|---|---|
| Study id | `2026-10-24_browser` (Alexander’s machine; split from Kalyan’s `2026-10-19_browser`) |
| Mode | Browser only (Playwright); Claude disabled for automation |
| Providers | ChatGPT, Gemini; Google Search AI Overview from day 3 |
| Languages | English, Hebrew |
| Locations | Mullvad `us-nyc-wg-001` (US) and `il-tlv-wg-101` (IL); exit country verified every observation |
| Days planned | 10 scheduled slots, one run per day |
| Prompt | Same semantic question every day: whether the Fourth Geneva Convention applies to Judea and Samaria (EN/HE in `questions.json`) |
| Target cells/day | 12 (3 providers × 2 languages × 2 locations), once Google Search was enabled |

Collection ran through a macOS LaunchAgent that called `python main.py run-due --mode browser` every minute against the active study schedule.

## What was collected

| Metric | Value |
|---|---|
| Calendar span | 2026-10-24 through 2026-11-02 (day 10 aborted mid-run) |
| CSV rows | 107 |
| `status: ok` | 66 (62%) |
| `status: captcha` | 20 (all Google Search) |
| `status: ui_error` | 15 |
| `status: timeout` / `error` | 4 / 2 |
| VPN verified | 107 / 107 |
| US / IL rows | 56 / 51 |
| Manual CAPTCHA clears | 10 (`manual_intervention` true) |

Day coverage (ok / total cells that day):

| Day | Cells | Ok | Notes |
|---:|---:|---:|---|
| 1 | 8 | 7 | ChatGPT + Gemini only |
| 2 | 8 | 8 | ChatGPT + Gemini only |
| 3–9 | 12 each | 5–10 | Google Search often captcha/ui_error |
| 10 | 7 | 2 | Stopped during IL leg; US mostly incomplete |

Comparable US/IL pairs with both sides `ok`: 25 (mostly ChatGPT/Gemini; one Google Search EN pair).

## Operational outcome

The scheduler ran for about nine full days and started day 10 on 2026-11-02.
Google Search was the main failure mode: 20 timed-out human-verification waits and only 6 successful overviews.
ChatGPT and Gemini usually returned answers; remaining failures were UI scrape/evidence problems and occasional timeouts.
Alerts for CAPTCHAs sometimes timed out at the macOS notification step; collection still waited the configured human window.
Day 10 was interrupted on purpose when automatic collection was shut down (VPN disconnected, LaunchAgent removed, active pointer cleared).

## Content findings (first pass)

Re-run the tables with `python main.py analyze` (writes `results/analysis/`).

1. Low pairwise text similarity between US and IL.
Mean similarity on the 25 comparable pairs is about 0.11 (range roughly 0.04–0.19).
That measures surface wording; long chatbot answers paraphrase heavily even when the outline is stable.

2. No location-focus flip on the config heuristic.
Every pair is labeled `focus=IL` on both sides (`focus_change_rate = 0` in `summary.csv`).
The study question is about Israeli/Palestinian territory law, so IL-related terms dominate regardless of VPN exit.

3. Substance looks stable across exits in sampled pairs.
ChatGPT and Gemini answers from US and IL both typically present the international-consensus “yes, applies (West Bank / occupied territory)” line and the Israeli government dispute (often de jure vs humanitarian application), citing ICJ/ICRC/UN-style authority.
This report does not claim a formal causal test; stochastic regeneration and UI packaging remain confounds.

4. Google Search AI Overview is not a usable automatic channel here.
CAPTCHA / unusual-traffic blocks dominated; the channel cannot support balanced US/IL comparison without heavy human attendance.

5. Setup checks earlier in the project (country and emergency-number probes) already showed that some apps can surface location when asked directly; this campaign asked a legal question that does not request the user’s country.

## Shutdown (2026-11-02)

1. Soft-stopped the in-progress collector and disconnected Mullvad (`python main.py vpn-off`).
2. Unloaded and deleted `~/Library/LaunchAgents/local.ip-location-experiment.plist`.
3. Removed `results/studies/ACTIVE` so `run-due` has no study to attach to.
4. Marked [`manifest.json`](../results/studies/2026-10-24_browser/manifest.json) with `status: ended`.

Data on disk is unchanged aside from the partial day-10 rows already written.
To collect again later: `python main.py start-study` (new id) and `python scripts/install_scheduler.py --install`.

## Files

| Path | Role |
|---|---|
| [`results/studies/2026-10-24_browser/`](../results/studies/2026-10-24_browser/) | Schedule, CSV/Excel (local), run JSON, evidence |
| [`results/analysis/report.txt`](../results/analysis/report.txt) | Automated summary text |
| [`results/analysis/location_pairs.csv`](../results/analysis/location_pairs.csv) | US/IL paired responses and similarities |
| [`docs/overview.md`](overview.md) | Research framing |
| [`docs/scheduling.md`](scheduling.md) | How the LaunchAgent was installed and paused |
