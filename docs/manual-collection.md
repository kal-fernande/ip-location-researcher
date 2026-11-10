# Manual collection

`python main.py collect-manual` records answers from a web app that a person uses by hand, in their own browser.
It is for Claude, whose web app cannot be used logged out and so cannot be collected by the browser collector (see [web-app-access.md](../notes/web-app-access.md)).
The script never opens or controls the web app.
It switches the VPN, verifies the exit country, prints each question, and saves the answer the person pastes back.

```bash
python main.py collect-manual                     # the two check questions, at each location
python main.py collect-manual --day 1             # day 1's study question, in each language at each location
python main.py collect-manual --provider chatgpt  # another web app; the default is claude
```

## Before a run

In your own browser:

- Log in to the web app as usual, with the same account for every location and every day.
- Turn off memory and search of past chats, so earlier chats cannot reveal the location.
- Keep the browser's language and the computer's timezone unchanged for the whole study.

The script prints this checklist, asks which model the app shows (for example `Sonnet 5.5`, or Enter if none), and waits for Enter before switching the VPN.

## During a run

For each location, the script connects the VPN and prints `exit_ip=... detected=... verified=True`.
Then, for each question:

1. Start a new chat in the web app.
2. Paste the question the script prints.
3. Copy the app's full answer, paste it into the terminal, and end with a line containing only `.`.

An empty answer is recorded as `status: skipped`.
The VPN is disconnected at the end, also when the run is interrupted.

## Where the results go

- Without `--day`, the run writes `results/checks/<time>_manual-<provider>.json`, with the same structure as the other checks and the answers in a `manual` list. It passes only if both locations were verified and every question was answered.
- With `--day N`, each answer is appended to the active study’s `results.csv` as one row with `collector: manual`. Questions already recorded for that day, provider, language and location are skipped. Locations follow the order in that study’s `schedule.json`, if it exists.

Either way the run adds a line to `results/README.md`.

## How manual results differ

Only the IP differs between the US and Israel runs, as in the other collectors.
But the conditions are not those of the browser collector:

- The person is logged in, so the app also knows the account, which is the same in both locations.
- The browser's language and the timezone are the person's own, not the fixed `en-US` and `UTC` of the browser collector.
- `latency_ms` is empty, because the script cannot see how long the app took to answer.
- Pasted answers may contain copying mistakes.

The methods section should therefore report manual results separately from the browser results.

## Related

- [web-app-access.md](../notes/web-app-access.md): why Claude needs a person
- [results-csv.md](results-csv.md): columns written for pasted answers
- [overview.md](overview.md): experiment design without code
- [All docs](README.md)
