# Reviewing saved browser answers

Every new browser answer from ChatGPT, Gemini, and Google Search uses the same evidence bundle.
Saving it uses the Chromium window that already collected the answer; it does not ask the question again or run a second search.
Automatic extraction still writes the usual CSV and run/check JSON.
The bundle preserves material you can use to review that extraction later.

## Open a saved answer

1. Open the results Markdown report and follow the observation's `Saved page` link.
2. Open `review.html` in Chrome or Chromium to read the exact recorded answer and captured source URLs.
3. Follow `Original page archive` to open `page.mhtml`, or open that file directly with Chrome/Chromium, to inspect the captured website.
4. Use `page.png` as the visual reference when comparing the extraction with the original page.

The review page and archive can be read offline without a VPN or an Ollama model.
The archive preserves the captured page state, including loaded styles and resources; it does not restore a working chat session or citation controls that were never captured.
The site's scroll panes, fixed toolbars, and visible overlays remain part of that layout; `review.html` provides an unobstructed copy of the full recorded answer.
Use the source list in `review.html` to open publisher URLs; opening those websites requires internet access and shows the publisher's current page.
Raw `page.html` is also saved for inspection and reprocessing, but it may depend on external website assets and scripts, so use the archive for offline review.

## Where the files live

| Collection | Folder |
|---|---|
| Prompt checks | `results/checks/evidence/<observation_id>/` |
| Study runs | `results/studies/<study_id>/evidence/<observation_id>/` |
| Sandboxed study smoke tests | `results/checks/study-smoke/<timestamp>/evidence/<observation_id>/` |

The observation JSON's `metadata.evidence` gives the exact paths.
Retries have separate folders, so a later attempt cannot overwrite an earlier snapshot.
Existing observations are not changed or given reconstructed snapshots.

| File | Purpose |
|---|---|
| `page.mhtml` | Self-contained archive for reviewing the captured website in Chrome/Chromium |
| `page.png` | Full-page screenshot |
| `page.html`, `answer.html` | Rendered page and answer-container markup for inspection |
| `answer.txt`, `observation.json` | Exact recorded answer, question, source list, capture time, provider, and observation id |
| `source-evidence.json` | Captured citation groups, links, or observed Google redirect mappings |
| `review.html` | Offline reading copy with clickable source links and links to the original evidence |
| `manifest.json` | Hashes used to detect missing or changed files |

Evidence saving is enabled by `browser.save_evidence: true` in `config.yaml`.
Raw browser evidence stays local and is ignored by Git, like failure screenshots, because it can contain temporary browser state.
New JSON results retain the evidence paths and citation data.

## Validate a saved check or run

Replace the example path with the JSON file you want to validate:

```bash
python main.py validate-evidence path/to/check-or-run.json
```

This command only reads saved files; it does not open the live websites, switch the VPN, or change the schedule.
For a study run under `runs/`, it also compares the sibling `results.csv` and its Excel mirror when present.
For a custom CSV location, add `--csv path/to/results.csv`.

Validation checks that:

- Every required evidence file exists and matches its saved hash.
- The exported answer, question, provider, observation id, and source URLs match the captured observation.
- Every exported URL has support in the captured answer markup/text, citation groups, or observed Google redirect mappings.
- Source-control coverage is complete for a successful answer, and ChatGPT/Gemini control counts still match the captured answer markup.
- CSV and Excel rows match the run JSON when those tables are checked.

An unreadable citation control or an evidence-saving failure fails the prompt check while preserving the completed answer.
A citation failure does not ask the question again to obtain a replacement answer.
A Google query with no AI Overview or an unresolved verification block remains a failed collection even if its failure page was archived successfully.

## Manual validation

Start by reviewing an English and Hebrew answer from every source and location.
Compare the answer boundaries and visible citations with the archive/screenshot and the saved source list, including responses with zero sources.
Repeat that review after a website changes, and inspect failed or incomplete observations before treating them as usable study data.
The automatic checks establish consistency and captured support; manual review checks whether the selected page content represents the intended answer.
No language model rewrites the answer or infers missing URLs.

Chromium supports the archive through [Page.captureSnapshot](https://chromium.googlesource.com/devtools/devtools-frontend/+/main/third_party/blink/public/devtools_protocol/domains/Page.pdl), accessed through [Playwright's CDP session](https://playwright.dev/python/docs/api/class-cdpsession).
