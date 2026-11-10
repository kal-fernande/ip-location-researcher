# Testing the setup

Scheduler behavior can be tested without switching the VPN or opening browsers:

```bash
python -m unittest discover -s tests -v
```

Before collecting data, check that the VPN switches country and that the API providers answer through it:

```bash
python main.py check            # VPN and API
python main.py check --no-api   # VPN only
python main.py check --browser  # VPN, API and the web apps
python main.py check-browser    # VPN and browser app readiness only
python main.py check-browser-prompt  # VPN and the web apps, no API
python main.py check-browser-study   # sandboxed one-day study pipeline smoke test
python main.py check-browser-prompt --provider google_search  # only Google AI Overview
python main.py check-browser-prompt --study-only  # day 1 English/Hebrew on all sources; no diagnostics
```

Which check covers which layer: [checks.md](checks.md).

To verify ChatGPT's citation extraction without changing study data, run `python main.py check-browser-prompt --provider chatgpt --study-only`.
Each observation records `metadata.source_capture`; the browser-response method matches citations to the displayed message and requires every detected citation/link control to be accounted for.
A citation failure keeps the completed answer and fails the check without generating a replacement answer.
All three sources save the same browser evidence bundle and include its paths in `metadata.evidence`.
Use `python main.py validate-evidence path/to/check-or-run.json` to verify saved answers, URL support, and file hashes without new searches.
Study-run validation also compares its CSV and existing Excel mirror; see [saved-page review and validation](browser-evidence.md).
Live schedule / CAPTCHA recovery: [scheduling.md](scheduling.md).
Index: [README.md](README.md).

Setup checks are separate from live `run-day` collection.
They never write to the active study's CSV or `schedule.json`.
`check-browser-study` uses the study code path but only inside `results/checks/study-smoke/` ([folder README](../results/checks/study-smoke/README.md)).

Prompt checks now also ask **day 1's study question in every configured language**, read directly from `questions.json` each time.
With English and Hebrew enabled, each provider gets four independent prompts per VPN location: country, emergency number, day 1 English, and day 1 Hebrew.
Saved study-prompt entries include `question_id` and `language`.
This applies to `check`, `check --browser`, `check-browser-prompt`, and manual prompt checks; `check --no-api` without `--browser` remains VPN-only, and `check-browser` still only checks readiness.
Missing day 1 translations fail before switching the VPN.
Historical files retain the questions originally asked.

Add `--study-only` to `check-browser-prompt` to skip the country/emergency diagnostics and ask only day 1 in each language.
This can be combined with `--provider google_search`.
Google may not supply an AI Overview for a personal location question even when ordinary search results load correctly.

## Automatic one-minute study test (macOS)

```bash
python main.py check-browser-study --launchagent-delay
```

macOS starts a temporary LaunchAgent at the first minute boundary after a one-minute wait; the full browser study smoke test then runs in its background process.
Keep the terminal open and complete any visible CAPTCHAs.
Outputs stay in a study-smoke test folder, and the temporary agent is removed afterward.
Failure to start automatically, failed collection, or failed cleanup is recorded as FAIL.
The real agent, schedule, and study CSV are preserved.
See [the setup guide](scheduling.md) for timing limits and how to keep the Mac ready.

## macOS scheduler check

```bash
python main.py check-scheduler
python main.py check-scheduler --probe
```

The standalone preflight checks the installed LaunchAgent and saved schedule; `--probe` also verifies a new no-op background invocation, refusing when a run is due soon.
It writes diagnostic PASS/FAIL JSON into `results/checks/` without collecting study data.
Full setup, test boundaries, and troubleshooting are in [scheduling.md](scheduling.md#macos-setup-and-launchagent-configuration).

## VPN/API Check

For each location in `config.yaml` (`US`, `IL`):

1. Connects the VPN with `vpn.connect_command`, after disconnecting any previous tunnel.
2. Looks up the exit IP and its country using `vpn.verify_urls` (falling back to `vpn.verify_url` for older configs) and compares the country with `expected_country_code`. If the lookup fails, it retries (`verify_attempts`, `verify_retry_seconds`), because DNS can lag a few seconds behind a fresh tunnel.
3. If the country matches, asks every API provider enabled under `api: providers:` two short questions, each in its own conversation:
   - `country`: `Which country am I in? Answer with the country name only, or "unknown" if you cannot tell.` A direct probe: does the model know where the user is?
   - `emergency`: `What number do I call in an emergency? Answer with the number only.` An indirect probe: does location change a factual answer (the appropriate local emergency service number)?

The API gets no location, so the expected answers are `unknown` and no systematic US/Israel difference in the emergency number.
Short questions keep the cost negligible and are never mistaken for study data.

4. With `--browser`, asks the diagnostic questions and day 1's translations in every enabled browser source (ChatGPT, Gemini, and Google Search AI Overview; Claude is disabled because claude.ai has no logged-out chat, see `notes/web-app-access.md`), each in a fresh, logged-out browser window with the fixed locale and timezone from `config.yaml`. Logged out, the app can only learn the location from the IP. Apps that need a login record `login_required`; captchas record `captcha` and are not bypassed. Failure screenshots go to `results/failures/`, which git ignores.

Unlike the API, an app may add the user's location before the model sees the question.
If it does, the answers should differ by location: a country name instead of `unknown`, and an appropriate local emergency number.

The VPN is disconnected at the end, also when a step fails.
The command prints `PASS` or `FAIL` and exits with status 0 or 1.

## Browser Check

`python main.py check-browser` also visits each configured VPN location.
At each location, it opens every enabled browser provider in a fresh Playwright context and checks whether the app exposes a usable prompt box.
It does not submit a prompt and does not write study rows.

The browser check records `status=ready` when the provider looks usable.
It records `login_required`, `captcha`, `ui_error`, `timeout`, or `error` when collection would not be able to proceed.

`python main.py check-browser-prompt` goes one step further: it asks the diagnostic questions and day 1's translations in every enabled browser source (step 4 above) and records each prompt and answer.
It is the same as `check --no-api --browser`, except that its file is named `..._browser-prompt.json`.
It passes only if every location was verified and every source answered every question with `status: ok`.

Both browser check commands accept `--provider google_search` to check only Google.
Its readiness check finds a search box; its prompt check requires an AI Overview, not ordinary search snippets.
Google may return `no_ai_overview` for a query, which is saved and makes the check fail.
Source URLs are retained in the study CSV's `source_urls` column and in JSON; Google also records its search URL in JSON metadata.
See [Google Search collection](google-search.md) for verification limits and the separate Chromium fixture tests.

If `browser.human_captcha_enabled` is true and the browser is not headless, a CAPTCHA/security page pauses the run instead of failing immediately.
The script prints `HUMAN ACTION NEEDED`, keeps the browser window open, and waits for a person to complete the visible check.
It does not solve, click, or bypass the challenge.
If the prompt box appears before `human_captcha_timeout_seconds`, the check continues and records `manual_intervention: true`; otherwise it records `status: captcha`.

## Where the results go

The JSON example below is a historical US/France record; new checks use `US` and `IL`.
Each run writes one JSON file to `results/checks/`, named by start time, for example `results/checks/2026-10-12_104401.json`:

```json
{
  "started_at": "2026-10-12T10:44:01-07:00",
  "checks": ["vpn", "api"],
  "ok": true,
  "locations": [
    {
      "location": "US",
      "vpn": {"exit_ip": "104.36.50.47", "detected_country_code": "US", "verified": true, ...},
      "api": [{"provider": "anthropic", "prompt_id": "country", "prompt": "Which country am I in? ...", "response": "unknown", "model_observed": "claude-sonnet-5-5", "status": "ok", ...}],
      "browser": [{"provider": "claude", "url": "https://claude.ai/new", "status": "ready", ...}]
    },
    ...
  ]
}
```

For the VPN/API check, `ok` is true only if every location was verified and every API call returned `status: ok`.
With `check --browser`, browser prompt results must also return `status: ok`.
For `check-browser-prompt` the same holds for the browser prompt results alone.
If a check was asked for but no API provider or web app is enabled, `ok` is false.
For the browser readiness check, `ok` is true only if every location was verified and every enabled browser provider returned `status: ready`.
On failure, `vpn.error`, the API entry's `error`, or the browser entry's `error` says why.

Each run also adds a line to the results index, `results/README.md`.
Check files are tracked in git, like the rest of `results/`, so passing checks are part of the study record.

## Common failures

- `detected` is empty and `vpn.error` mentions a timeout or `Failed to resolve`: the tunnel or its DNS was not ready even after the retries, or all configured verifier services were unreachable. Run again; if it keeps happening, raise `verify_attempts`.
- `detected` is the wrong country: the VPN did not switch. Run `mullvad status` and check `vpn_target` in `config.yaml`.
- API `status` is `error` with a `KeyError`: the provider's key is missing from `.env`.
- Browser `status` is `captcha`: human verification was shown and either human intervention is disabled or it was not completed before the timeout.
- Browser `status` is `login_required`: the consumer app did not expose an anonymous prompt box in a fresh context.
- Browser `status` is `ui_error`: the app loaded, but the selectors in `experiment/browser_collectors.py` did not find a prompt box.
