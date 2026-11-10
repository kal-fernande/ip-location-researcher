# Desktop app

The desktop app provides buttons for the experiment's existing command-line tools.
Use it to check the setup, see the next scheduled time, run a due study day, and open the results in a spreadsheet.

## Open the app

On macOS, double-click `Open Experiment.command` in the project folder.
Checks and collection use the project's Python environment automatically.
From a terminal, the equivalent is:

```bash
.venv/bin/python main.py gui
```

A maintainer must complete the existing [setup](../README.md#setup), including Mullvad login, before the first use.
The app adds no pip dependencies and uses Tk from the Python installation.
`source setup.sh` installs Homebrew's matching `python-tk@X.Y` on macOS when the project Python lacks Tk 8.6+.
On Macs that still have an older or missing Tk in the project environment, the app can open the window with another Python that already has Tk, while keeping checks and collection in the project environment.
That fallback does not download Python.
If no supported Tk installation is available after setup, ask the maintainer for Python 3.10+ with Tk.

## Create a separate study

Use `New study…` at the top of the main window when you want to collect a new study.

1. Enter an optional display name, which can contain spaces.
2. Choose the first date, or leave it blank to schedule the first run tomorrow.
3. Add any notes and click `Create study`.
4. If another study is active, confirm the switch to the new study.

The app creates a separate folder with its own generated ID under `results/studies/` and shows the new active study and next run time.
Previous study folders and their collected answers remain on this computer.
The active study determines which CSV, Excel file, schedule, and analysis the app uses.
Creating a study does not install automatic scheduling; an already-installed scheduler follows the newly active study's saved schedule.
The existing `Start a new study` tool remains available under `More tools` for additional options.

Each study keeps these files together in `results/studies/<id>/`, creating outputs as needed:

| File or folder | Contents |
|---|---|
| `results.csv` | All observations collected for this study. |
| `results.xlsx` | Excel copy of the same observations. |
| `runs/` | Separate JSON records for individual collection runs. |
| `schedule.json` | This study's planned run times and completion state. |
| `manifest.json` | Display name, notes, creation time, and study identity. |
| `analysis/` | Tables and report generated when this study is analyzed. |

Generated study, check, and analysis files under `results/` stay local and are excluded from Git and automatic commits.
Existing local result files are preserved.
Results published in older GitHub commits remain in that published history.

## Stop a study and begin again

Use `Stop study…` in the main window's `Study & checks` tab if you need to abandon the current study and set up a fresh run.
Confirm the study name when prompted.
This cancels that study's future runs and stops a running collector, including one started by automatic scheduling, with VPN cleanup.
Collected answers, spreadsheets, schedules, and evidence remain in the original study folder.
A local `STOPPED` file records when the study was stopped.
If cleanup needs attention, the activity area explains the problem; wait for collection to finish or use `Disconnect VPN` before creating the next study.

Once stopping finishes, click `New study…` to create a separate study with its own name, schedule, and results folder.
The installed automatic scheduler stays available and follows the new study when it is created.
While no study is active, scheduler checks collect nothing.
Stopping a study does not automatically create or start a replacement.

From the terminal, the equivalent is:

```bash
.venv/bin/python main.py stop-study
```

## Everyday use

1. Read the active study and next run time at the top of the window.
   Times include the study's timezone.
   The display refreshes every 15 seconds; use `Refresh next run time` to update it immediately.
2. Use `Run a check` to test the setup.
   The default browser readiness check switches the VPN and checks whether the web apps are ready without submitting questions.
   Other check types are available from the list.
3. Use `Run scheduled study` to collect any study day whose saved time is due, using browser mode.
   If the next time is still in the future, no answers are collected.
   This button runs once; it does not turn on automatic scheduling.
4. Use `Open study CSV` to open the active study's data in the computer's default spreadsheet app.
   `Open Excel` opens the matching Excel export, and the folder button opens the study folder.
   Results files become available after answers have been saved.

The next run time comes from the saved study schedule.
Use the scheduler check to verify that automatic collection is configured on this Mac.
Keep the Mac awake and logged in around scheduled collection times, as described in [scheduling](scheduling.md).
Closing an idle desktop app leaves the separately installed macOS scheduler in place.

## Retry a missed or incomplete study run

Open `Manual run / retry…` in the main GUI's `Study & checks` tab whenever you want to inspect or retry a due study day.
Choose the study day from the list, check its missing-answer count, then click `Run selected day`.
The list includes past scheduled days with missing or failed browser answers, even when the normal skip policy already marked that day finished.
Completed days are also listed as `Complete — will be skipped`.
Selecting a day with all successful answers simply reports that it is complete; no VPN or browser collection starts and research files remain unchanged.
The most recent incomplete day is selected by default when one is available.
It also includes a failed day whose automatic retry has been moved to a future time.
Future study days that have not become due cannot be started through this button.
The window remains accessible when no days are available or another task is running; its explanation shows why `Run selected day` is disabled.
Use `Refresh` in that window after a running task finishes to update the list.

The retry collects only answers that have no successful saved observation.
Successful answers are skipped, and earlier failed attempts remain in the CSV and run files.
An explicit manual retry can try again after the automatic attempt limit has been reached.
Each retry is recorded with its actual collection time, original planned time, and `manual_recovery` metadata in the run JSON.
Its results are appended to the active study's normal CSV and Excel files.
The `retry` column is blank for ordinary observations and lists manual retry times for each retried answer.
If that answer needs multiple manual retries, its newest row contains every recorded retry timestamp on a separate line in the same cell, including the current retry's actual date, time, and timezone offset.
Earlier rows retain their original timestamps, answers, and recorded retry history.
Selecting an already complete day appends no retry timestamp and changes no research files.
The CSV, Excel mirror, and new run JSON observations use the same retry history; see the [data dictionary](results-csv.md#manual-retry-timestamps) for an example.

An existing next run time is preserved.
If this fills a pending day, the program marks browser collection complete and draws the next day only if no next day was already scheduled.
If a CAPTCHA or another problem still leaves gaps, the day remains available for another manual retry.
The activity area shows the outcome, and `Stop current task` works as it does for ordinary collection.
The collector lock prevents a retry from overlapping another study collection.

From the terminal, the equivalent is:

```bash
.venv/bin/python main.py retry-day --day 3
```

## Phone alerts

Use `Phone alerts…` at the top of the window to configure the existing notifications.
If an older app window is already open, let its current task finish, then reopen the app to see this button.

1. Turn on `Enable notifications` and choose a phone service.
   Prefer `Phone (Telegram)` on iPhone if ntfy banners are unreliable.
   Turn on `Show Mac banners too` if you want notifications on the computer.
2. For Telegram: create a bot with [@BotFather](https://t.me/BotFather), paste the bot token, open a chat with the bot, and enter your numeric chat id (for example from [@userinfobot](https://t.me/userinfobot)).
   For ntfy: paste a topic or subscription link, or use `Generate ntfy topic`, then subscribe in the ntfy app.
   Secrets are masked until you choose `Show secrets`.
3. Choose how many minutes before a run to receive advance notice, then use `Save & send phone test` to check delivery.
   `Save` alone changes the settings without sending a notification.
   See also [phone setup in scheduling.md](scheduling.md#b-phone-as-well-or-instead-via-ntfy).

Choose the Mac-only service to keep Mac banners without phone delivery, or turn off `Enable notifications` to disable delivery.
Existing webhook setups remain available from the phone-service list.
Saved settings apply to future notifications, including notifications from a task already running.
Advance reminders use them on the next scheduler check.
They are saved locally in the ignored `private/phone-alerts.json` file and shared by the desktop app, command-line tools, and automatic scheduler.
Saved desktop choices override corresponding settings in `config.yaml`, including any topic supplied by `NTFY_TOPIC` in the environment or `.env`.

## Watch progress and stop a task

The output area shows what the current task is doing and where it saved its files.
Checks and collection can take several minutes.
If the app says human action is needed, complete the visible CAPTCHA in the browser window and let collection continue.
Failed answers or missing AI Overviews can leave gaps even when a study day finishes; review the output and saved results.

`Stop current task` asks the task started by this window to stop and allows time for cleanup.
Closing the window during a task offers to stop it and waits for it to finish.
`Disconnect VPN` uses the existing emergency VPN-off tool and can also stop a collector running in the background.
It preserves the active study, schedule, and answers already saved.

## Other tools

The `More tools` tab exposes the remaining operations through labeled fields.
Browser mode is selected by default for collection.
Use API or both modes only when those providers and their keys are configured.

| Tool | When to use it |
|---|---|
| Run scheduled days once | Collect due days once with a choice of browser, API, or both modes and a missing-answer policy. |
| Run a specific study day now | Collect or resume a chosen study day immediately, even before its scheduled time. |
| Keep checking the schedule | Keep checking for due days while this task remains running. |
| Start a new study | Create a new study folder and its first scheduled time. |
| Recreate the active study schedule | Replace the active schedule with a new first-day schedule or agenda; avoid this during ongoing collection. |
| Analyze study results | Create descriptive tables and a report in the active study's `analysis/` folder. |
| Recover saved source links | Update result links from answers and run files already saved. |
| Validate saved browser evidence | Check a selected saved run or check JSON against its local evidence and optional CSV. |
| Collect answers by hand | Record answers obtained by a person in their own browser. |
| Install automatic scheduling (Mac) | Configure automatic browser collection on this Mac, with an option to replace an existing scheduler. |
| Preview automatic scheduler settings | Show the scheduler definition without installing it. |

For manual collection, follow the prompts in the output area.
The app opens a separate `Enter manual answer` window for your input.
If you close it, use `Enter answer…` beside the activity area to reopen it.
Use `Send line / ready` for the model name and the ready prompt; a blank submission acts like pressing Enter.
Use `Send full answer` to paste each answer, which sends the answer and its terminating line together.
Follow the [manual collection guide](manual-collection.md) for the browser preparation checklist.

Starting a replacement study, forcing collection, or replacing the schedule requires an explicit choice in the app.
Existing observation skipping, recovery policies, and output locations are the same as in the command-line tools.
