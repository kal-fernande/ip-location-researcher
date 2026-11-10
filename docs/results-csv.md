# Results CSV data dictionary

A **data dictionary** defines each field of a dataset.
This note defines the columns of every `results.csv` written by the experiment (live study data, sandboxed study-smoke CSVs, and archived pilot data).
New files use the column order in `experiment/storage.py` (`FIELDNAMES`); header upgrades preserve any extra user columns.

## Files that use this schema

| Path | Role |
|---|---|
| `results/studies/<study_id>/results.csv` | Live study observations for one study (`start-study`, then `run-day` / `run-due` / `daemon` / `collect-manual --day`). Active id is in `results/studies/ACTIVE`. |
| `results/checks/study-smoke/<timestamp>/results.csv` | Sandbox copy from `check-browser-study` (same columns; does not touch the active study) |
| `results/pilot-historical-data/results.csv` | Archived pilot; same columns, not updated by collection |
| `results/studies/_abandoned_*/results.csv` | Interrupted or superseded test runs kept for reference |

After merging an older checkout, an existing root-level `results/results.csv` may remain as a local legacy copy.
Current collection and `refresh-links` use the active study folder; the merge preserves the previous CSV contents and run JSONs there.

One row is one **observation**: one prompt asked once under one collector, provider, language, and VPN location.
Answers may contain newlines; open the file with a CSV reader that respects quoted fields.
Each time a study or study-smoke `results.csv` is appended, the collector also rewrites a sibling `results.xlsx` (same cells) so Windows Excel opens Hebrew and other Unicode without encoding glitches.

## Columns

| Column | Type | Meaning |
|---|---|---|
| `observation_id` | string (UUID or check id) | Unique id for this ask. Used in failure screenshot names under `results/failures/`. |
| `day` | integer | Study day number (from `questions.json` / the schedule). |
| `question_id` | string | Question label for that day (e.g. `Q01`). |
| `collector` | string | How the answer was obtained: `api`, `browser`, or `manual`. |
| `provider` | string | Source name from config (e.g. `chatgpt`, `gemini`, `google_search`). Google Search AI Overview is separate from Gemini. |
| `language` | string | Prompt language key from config (`en`, `he`). |
| `vpn_location` | string | Intended VPN location name from config (`US`, `IL`). |
| `timestamp` | ISO-8601 datetime | Local time when the observation was recorded (timezone offset included). |
| `retry` | string | Cumulative manual study retry timestamps for this observation key, one ISO-8601 value per line inside a quoted cell; blank for ordinary collection rows. |
| `exit_ip` | string | Public IP seen after connecting the VPN. |
| `detected_country_code` | string | Country code returned by the IP lookup used for VPN verification. |
| `vpn_verified` | boolean | `True` when `detected_country_code` matches the location’s expected country. |
| `model_requested` | string | API model id requested in config. Empty for browser collection. |
| `model_observed` | string | Model string reported by the API or noted for manual collection. Empty for browser collection. |
| `prompt` | string | Exact text sent to the chatbot. |
| `response` | string | Captured answer text (may be incomplete UI chrome if the collector mis-read the page). |
| `status` | string | Outcome of the attempt (see below). |
| `error` | string | Error detail when collection failed; empty on success. |
| `latency_ms` | integer or empty | Wall time for the ask, in milliseconds. Empty for manual rows. |
| `attempts` | integer | Browser tries used for this row (`1` = no retry). May be empty on older or manual rows. |
| `source_urls` | string | Captured HTTP(S) source URLs, one per line inside a quoted CSV field. Empty when no URL was saved. Link labels and URLs are also retained as structured `sources` in new JSON observations. |
| `manual_intervention` | boolean | `True` when a person cleared a CAPTCHA (or similar) before the answer was collected. `False` for API rows and for browser rows with no human challenge. Empty on rows written before this column existed. |
| `human_wait_ms` | integer or empty | Wall time spent in CAPTCHA human-wait loops for this observation, in milliseconds. Empty when no human wait ran (API rows, or browser rows with no CAPTCHA pause). |
| `human_timeout_seconds` | integer or empty | Configured `browser.human_captcha_timeout_seconds` budget for that wait. Empty when no human wait ran. Compare with `human_wait_ms` to see whether the wait timed out near the budget. |

## Observation key

Rows that already exist are skipped on re-run.
The skip key is the tuple

`(day, question_id, collector, provider, language, vpn_location)`

defined in `experiment/storage.py` as `observation_key`.
A successful prior row (`status == ok`) blocks a duplicate ask for that key.

## Manual retry timestamps

The `retry` column identifies observations collected through `Manual run / retry…` in the GUI or `retry-day` from the terminal.
Initial scheduled observations have an empty `retry` cell.
Each new manual retry row contains the earlier recorded manual retry times for its observation key, followed by that row's own `timestamp`.
History is separate for each study day, question, collector, provider, language, and VPN location.
For example, after two retries the newest row's `retry` cell contains these two lines:

```text
2026-11-09T14:05:12.123456-08:00
2026-11-09T15:20:45.654321-08:00
```

The newest retry row holds the full history known at that point, while earlier rows retain the history recorded when they were created.
Successful answers are skipped and receive no additional retry time.
Selecting an already complete day creates no row, adds no timestamp, and leaves the CSV unchanged.
The Excel mirror and new JSON observations retain the same `retry` value.
Automatic scheduler retries are outside this manual retry history; browser attempts within a single observation remain counted in `attempts`.

Existing CSVs gain the `retry` column automatically on their next append.
An explicit schema upgrade can recover earlier manual retry times only when a saved run identifies `manual_recovery` and its observation id, key, timestamp, and response match the CSV row.
Ordinary duplicate rows are not assumed to be manual retries, and unavailable historical times remain blank.
The upgrade preserves other columns and refreshes the Excel mirror.

## `status` values

| Value | Meaning |
|---|---|
| `ok` | An answer was recorded. For browser collection this means a finished turn: response text was stable, not known status/error chrome, and no Stop control was visible. |
| `skipped` | Manual collection: the operator left the answer blank. |
| `captcha` | A CAPTCHA or similar block stopped the browser ask. |
| `no_ai_overview` | Google Search loaded but no supported AI Overview appeared within the wait period; the answer is empty. |
| `login_required` | The page required login. |
| `timeout` | The browser did not get a usable answer in time. |
| `ui_error` | The page was open but the expected prompt or answer UI was missing. |
| `error` | Another failure (API exception, unexpected browser error). |
| `ready` | Used only in `check-browser` JSON (prompt box found; no ask). Not written to study CSVs. |

Browser retries apply to `timeout`, `ui_error`, `error`, and Google's `no_ai_overview` (see `RETRY_STATUSES` in `experiment/browser_collectors.py`).
The CSV row stores the final attempt.

ChatGPT and Gemini collect links from the answer element.
Google uses links inside its AI Overview.
URLs written out in API/manual answers are captured too.
`response` stays unchanged; `source_urls` stores addresses separately.
These are plain URL strings in a CSV, without Excel hyperlink formatting.
The sibling `.xlsx` mirror contains the same URL strings.

Existing CSVs gain the column automatically on the next append.
All prior answer cells and any extra user columns are preserved.
To recover available links into the current CSV from saved answer text and matching run JSON:

```bash
python main.py refresh-links
```

This command reads only local study data.
It does not run a search, ask a question, change the schedule, or rewrite JSON; it also refreshes the Excel mirror.
It joins saved run answers by `observation_id` and exact response text; it does not mix check results into study data.
Older answers with source names or numbered citations but no saved URL cannot have their missing URLs reconstructed.
ChatGPT's citation popovers, Gemini's source chips, and Google's related-source list are opened to collect their URLs.
ChatGPT source buttons are read without visiting the source pages.
If an expected source control cannot be read, collection records `ui_error` with the available answer and links instead of silently treating citation capture as complete.
Google redirect links are preserved and resolved to publisher URLs when the redirect responds; unresolved redirect URLs remain usable captured links.
New JSON observations include citation-control coverage in `metadata.source_capture`.
Saved URLs are also shown as links in the Markdown study/check reports.
A response can legitimately have no source URLs when it contains no hyperlinks or citation controls; this happened for Gemini's Hebrew answers in the live check.
Coverage records the supported controls found in that answer, rather than guaranteeing support for every future website layout.

ChatGPT now reads the citation and link components delivered to its browser, matched to the displayed assistant message.
It observes incoming data because the response connection can stay open after the answer finishes rendering.
Only declared answer citations and links are collected; unused search results and transport credentials are excluded.
The [live ChatGPT check on 2026-10-26](../results/checks/2026-10-26_115226_browser-prompt.json) passed all four English/Hebrew and US/Israel cases on the first attempt, with every detected citation/link control accounted for.
The earlier failed checks remain as historical records of the old extraction method.
A completed answer with incomplete citations is retained as `ui_error` without asking the question again; a missing citation must not replace a study answer with a different generated answer.
Source-capture diagnostics include the extraction method, message id, component groups, coverage counts, and per-control failure details when a fallback interaction fails.
The same observation also has a local HTML/archive/screenshot bundle under `metadata.evidence`; [review and validation instructions](browser-evidence.md) explain how to compare it with the CSV/JSON and Excel mirror.

Google also retains the query URL and overview metadata in JSON; see [google-search.md](google-search.md).

## Related files

- Run metadata: JSON under `results/studies/<id>/runs/` (or `results/checks/study-smoke/.../runs/` for sandboxes).
- Index of checks: [results/README.md](../results/README.md); each study also has its own `README.md`.
- What checks write vs live study data: [checks.md](checks.md).
- Starting a study: [scheduling.md](scheduling.md).
- Hand collection that uses the same columns: [manual-collection.md](manual-collection.md).
- [All docs](README.md).
