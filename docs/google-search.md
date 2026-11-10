# Google Search AI Overview

The browser study has three separate sources: **ChatGPT, Gemini, and Google Search AI Overview**.
The Google source is the AI answer above ordinary search results.
This is a different product experience from the Gemini app, even though Google uses Gemini technology in Search.
Google also offers an AI Mode tab, which this study does not collect.

Google explains that [AI Overviews appear only for some searches](https://developers.google.com/search/docs/appearance/ai-features).
A missing overview is a possible study outcome, even when the search itself works.

## How to use it

`config.yaml` enables `google_search` under `browser.providers`.
Every new browser study day asks all three sources the same question from `questions.json`, in English and Hebrew, from New York and Tel Aviv.
That makes 12 observations per day.
A failed attempt still gets an observation row.

The existing `run-day --mode browser`, scheduler, and study smoke check include Google automatically.
There is no extra service, API key, or LaunchAgent to install.
The running scheduler reads the configuration on each invocation.

To test only Google without changing live study data:

```bash
source setup.sh
python main.py check-browser --provider google_search
python main.py check-browser-prompt --provider google_search --study-only
```

The first command checks the search box.
The second submits day 1's English and Hebrew questions at both VPN locations.
`--study-only` omits the personal country/emergency diagnostics; Google may show ordinary results without an AI Overview for those questions.
Omit the flag to include them.
It writes a check JSON under `results/checks/`.
The second command passes only if every query produces a usable overview and the VPN is verified.
A readiness PASS alone does not establish that Google will supply an overview.

For the full sandboxed study test, including all three sources:

```bash
python main.py check-browser-study
```

See [checks.md](checks.md) for the optional macOS startup test.

## What is collected

Each query opens an ordinary `/search?q=...` URL in a fresh, logged-out browser.
The question is unchanged.
The interface language follows the fixed browser locale (`en-US`, so `hl=en`) for both prompt languages and locations.
No country override (`gl` or `uule`) is added.
Google sees the VPN exit IP.

The collector identifies the AI Overview, expands a visible “Show more” button, and waits for stable answer text with no visible generation indicator.
It reads only recognized answer elements inside that overview.
Ordinary search snippets, knowledge panels, and AI Mode responses are not used as substitutes.
Browser selectors live in `experiment/google_search.py` and may need updates when Google changes its page.

The output paths are the existing ones:

| Output | Google data |
|---|---|
| `results/studies/<id>/results.csv` | `provider=google_search`, prompt, answer text, `source_urls`, status, timestamps, VPN information |
| `results/studies/<id>/runs/<timestamp>_day-<N>_browser.json` | Same observation plus search metadata and source hyperlinks |
| `results/checks/<timestamp>_browser-prompt.json` | Diagnostic queries and their search metadata |
| `evidence/<observation_id>/` inside the study/check folder | Same HTML, MHTML archive, screenshot, and validation bundle as ChatGPT and Gemini |
| `results/failures/` | Failure screenshots, when enabled; ignored by Git |

A Google observation in JSON also includes:

```json
{
  "provider": "google_search",
  "metadata": {
    "surface": "ai_overview",
    "search_url": "https://www.google.com/search?q=...&hl=en",
    "final_url": "https://www.google.com/search?q=...&hl=en",
    "overview_found": true,
    "expanded": true,
    "extraction_selector": "[data-container-id='main-col']",
    "sources": [
      {"title": "Wikipedia", "url": "https://en.wikipedia.org/wiki/Fourth_Geneva_Convention"}
    ]
  }
}
```

This is an illustrative schema example, not a collected answer.
Source links are saved in JSON's structured `sources` field and its overview metadata, and in the CSV's `source_urls` column (one URL per line).
They are plain URLs rather than Excel hyperlink formatting.
The study's sibling `results.xlsx` mirrors the CSV cells.
Saved-page paths are recorded in `metadata.evidence`; see [offline review and validation](browser-evidence.md).
Only external links present inside the overview are captured, including its related-source list when available.
Google's opaque `/goto` source links are retained and resolved to publisher URLs when possible; otherwise the original redirect link is saved.
The answer is read from the main answer column, excluding source-card snippets and the footer.
JSON records citation-control coverage in `metadata.source_capture`; an unreadable expected source list records `ui_error`.
The underlying model is left blank because the page does not reliably identify it.

## When a query cannot provide an answer

| Status | Meaning |
|---|---|
| `ok` | A stable, expanded overview answer was captured. |
| `no_ai_overview` | Search results loaded, but no supported overview appeared before the wait period ended. The answer field is empty. |
| `captcha` | Google showed a verification or “unusual traffic” block. |
| `login_required` | Google redirected to sign-in. |
| `ui_error` | The expected results or overview structure could not be identified. Inspect the screenshot and selectors. |
| `timeout` | Loading, generation, or expansion did not finish in time. |
| `error` | Another browser failure occurred. |

The default wait is 90 seconds per attempt.
Timeouts, UI errors, and a missing overview use the existing browser retry setting (`retries: 1` means one fresh-browser retry).
If no overview appears on either attempt, the observation ends with `no_ai_overview` and makes the prompt/study smoke check fail.
A CAPTCHA does not trigger an immediate fresh-browser retry.
In a visible browser, the existing human CAPTCHA settings allow a person to complete verification; the collector does not bypass it.

Every non-`ok` result is recorded and makes a prompt/study smoke check fail.
The daily study follows `experiment.recovery_policy`: the current `skip` policy retains the failed row and moves on; `retry` follows the existing attempt limit and rescheduling rules.
Missing overviews therefore must be considered when interpreting missing study answers.

## Reproducible verification

Run the regular unit tests without browsers or network:

```bash
.venv/bin/python -m unittest discover -s tests -v
```

Run the separate Chromium tests with all requests intercepted by synthetic HTML:

```bash
.venv/bin/python -m unittest discover -s tests -p 'browser_google_search.py' -v
```

These cover expanded English and Hebrew answers, source links, ordinary-result exclusion, delayed overviews, verification blocks, missing/changed markup, and unfinished generation.
Unit tests also cover the 12-observation matrix, matching CSV/JSON outputs, and adding only Google's missing cells on a manual rerun.
Synthetic pages validate the extraction logic; they do not prove compatibility with every live Google layout.

The [live study-question check on 2026-10-25](../results/checks/2026-10-25_175138_browser-prompt.json) captured all 12 answers across the three sources, both languages, and both VPN locations.
All four Google answers included publisher URLs (6, 7, 10, and 6 links); Google's source-list control was read successfully for each answer.
Google verification required manual help during that check.
Loaded search results now end the verification wait even if a stale reCAPTCHA frame remains.
This verifies the tested questions and layouts; future queries can still have no overview, encounter verification, or require selector updates.
That check predates the additional ChatGPT citation-popover extraction.
The [ChatGPT browser-response citation check](../results/checks/2026-10-26_115226_browser-prompt.json) now passes all four study cases; see [citation capture](results-csv.md) for its evidence and failure handling.

## Existing study days

Enabling Google affects subsequent collections.
Already completed schedule entries and historical results are preserved; the scheduler does not reopen completed days.
A deliberate manual rerun of a prior day collects only missing provider/language/location keys, but those answers have a later timestamp and cannot reconstruct what Google would have answered at the original run time.
