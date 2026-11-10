# Documentation

[Root README](../README.md) has setup commands and the short start path.
This page lists every note in `docs/` and the background notes under `notes/`.
Branch-level release notes: [CHANGELOG.md](../CHANGELOG.md).

## Experiment (little or no code)

| Doc | What it covers |
|---|---|
| [gui.md](gui.md) | Desktop app: run checks, see the next run, collect scheduled data, and open results |
| [overview.md](overview.md) | Research question, US/IL design, historical US/FR check table |
| [study-report-2026-10-24.md](study-report-2026-10-24.md) | Ended campaign `2026-10-24_browser`: coverage, failures, first-pass findings, shutdown |
| [manual-collection.md](manual-collection.md) | Hand-collecting Claude (or another logged-in web app) |
| [results-csv.md](results-csv.md) | Column meanings for every `results.csv` |
| [google-search.md](google-search.md) | Google AI Overview collection, cited links, missing answers, and tests |
| [browser-evidence.md](browser-evidence.md) | Open saved answers offline in Chromium and validate their text, links, and exports |

## Testing and the live study

| Doc | What it covers |
|---|---|
| [checks.md](checks.md) | Map of unit tests, browser checks, scheduler diagnostic, and study smoke |
| [testing.md](testing.md) | Commands and what each check does on the VPN and in browsers |
| [scheduling.md](scheduling.md) | macOS installation/configuration/permissions, scheduler diagnostics, policies, and alerts |

## Background notes (`notes/`)

| Note | What it covers |
|---|---|
| [vpn-providers.md](../notes/vpn-providers.md) | Why Mullvad |
| [web-app-access.md](../notes/web-app-access.md) | Which web apps work logged out |
| [the-matrix.md](../notes/the-matrix.md) | Why the browser does not hide automation |
| [captchas.md](../notes/captchas.md) | CAPTCHA rates in setup checks and how they are logged |

## Results indexes

| Page | What it covers |
|---|---|
| [results/README.md](../results/README.md) | Every check and collection run (newest first) |
| [results/results.md](../results/results.md) | Live study collection only |
| [results/checks/results.md](../results/checks/results.md) | Setup checks and study-smoke dialogues |
| [results/checks/study-smoke/README.md](../results/checks/study-smoke/README.md) | Sandboxed one-day study outputs — read before a live study |
