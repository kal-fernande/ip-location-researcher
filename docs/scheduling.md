# Set up and run the study on a Mac

Follow the steps below to set up automatic study runs, give macOS permission to access the project, and test that everything is ready.
You only need to install automatic scheduling once on each Mac.

You create the study schedule with `python main.py start-study` (or `python main.py schedule`).
That writes timed run slots into `results/studies/<study_id>/schedule.json` for the active study.
The Mac uses a small instruction file called a LaunchAgent to check that schedule every minute.
When a slot’s `run_at` time arrives, the LaunchAgent starts the study for you.
You do not need to leave Terminal or your editor open.

Related: [what each check tests](checks.md), [project setup](../README.md#setup), [all documentation](README.md).

<a id="macos-setup-and-launchagent-configuration"></a>

## Set up automatic runs on your Mac

### 1. Open the project and prepare it

Open the project folder in your editor, then open a terminal there.
In VS Code, choose **Terminal → New Terminal**.
Run:

```bash
source setup.sh
```

Wait for setup to finish.
After that, the commands below use `python` (the project venv).
Mullvad must also be installed and logged in, as explained in the [project setup instructions](../README.md#setup).

### 2. Start a study folder and check the first run time

Each study has its own folder under `results/studies/<study_id>/` (schedule, CSV, run JSON), similar to the smoke-test sandboxes.
The file `results/studies/ACTIVE` names the study that `run-due` and the LaunchAgent use.

Open `config.yaml` and confirm the number of days, timezone, VPN locations, and languages.
Confirm that `questions.json` contains the questions you want to ask.

Create a study before installing the scheduler:

```bash
python main.py start-study
python main.py study-status
```

That uses a default id of the form `YYYY-MM-DD_HHMMSS_browser`; add `--id my_label` for a name you choose.
That writes `results/studies/<id>/schedule.json` and points `ACTIVE` at it.
If a study is already active, `start-study` refuses so you do not switch by accident; use `python main.py start-study --force` only when you mean to leave the previous study idle on disk.
Look at `run_at` in that schedule (or in `study-status`): for example `2026-10-20T16:16:00-07:00` means October 20 at 4:16 PM.

To rebuild day 1’s time for the active study only:

```bash
python main.py schedule
```

Do not run `schedule` again during an ongoing study: it resets that study’s schedule.
Use one Mac to collect a given study.

### 3. Allow Python to access the project

macOS may block automatic runs from reading a project on your Desktop or in Documents.
Give Full Disk Access to the project’s Python binary (the program the LaunchAgent runs).
The LaunchAgent instruction file is [`~/Library/LaunchAgents/local.ip-location-experiment.plist`](../../../Library/LaunchAgents/local.ip-location-experiment.plist), installed by [`scripts/install_scheduler.py`](../scripts/install_scheduler.py).

First, copy and run this command in the project terminal:

```bash
python -c 'import sys; from pathlib import Path; print(Path(sys.executable).resolve())'
```

It prints the location of the Python program this project uses.
Copy the whole line it prints, then:

1. Open System Settings → Privacy & Security → Full Disk Access.
2. Click +. Unlock the settings if macOS asks for your password or Touch ID.
3. In the file chooser, press Command–Shift–G.
4. Paste the Python location you copied and press Return.
5. Select the program and click Open.
6. Make sure its switch is on in the Full Disk Access list.

Use the location printed on your own Mac; it can differ between computers.
Permission for Terminal or a different Python program may not cover this one.
If you replace Python later, repeat this step for the new program.

### 4. Install automatic scheduling

In the project terminal, run:

```bash
python scripts/install_scheduler.py --install
```

You should see **Installed** and a message that the schedule is checked at each minute boundary.
That means automatic scheduling is now switched on.
A run whose scheduled time has already passed may start immediately.

If you see **Scheduler already installed**, continue to the test below.
To update an older installation to the current scheduling setup, run:

```bash
python scripts/install_scheduler.py --install --replace
```

This saves the old instruction file and replaces it only when no collection is running.
The study schedule and answers stay as they are.
Getting the project files or running `source setup.sh` alone does not install automatic scheduling.

### 5. Test that the Mac can start it

(optional diagnostic)

When the next study run is more than two minutes away, run:

```bash
python main.py check-scheduler --probe
```

This asks macOS to start the scheduler once and checks that it finishes successfully.
It also checks the setup and confirms that your saved schedule and study CSV have stayed unchanged.
It does not ask the chatbots any questions.

Look for **PASS** at the end.
The test saves its result under `results/checks/`; you can read it in [the check report](../results/checks/results.md).

If it cannot safely test startup—for example, collection is already running, a run is due soon, or there is no upcoming run—it reports why and skips the startup test.
Do not reset the schedule to get around that message.
You can still inspect the setup with:

```bash
python main.py check-scheduler
```

That shorter check does not start the scheduler, so it is not a substitute for a successful startup test.

### 6. Test that the study can collect and save answers

A startup test checks that the Mac can start the program.
To also test the browsers, questions, saved answers, and scheduling of the next day, run:

```bash
python main.py check-browser-study
```

Allow several minutes and stay at the Mac.
This switches the VPN, opens the chatbots, and asks the study questions.
If a browser asks you to prove you are human, complete that check in the browser window.

To also test that macOS starts the study **on its own after at least one minute**, use this command in place of the one above:

```bash
python main.py check-browser-study --launchagent-delay
```

This sets up a temporary scheduler with the same calendar-based minute checks as the real one, waits for macOS to start it, then runs the full study smoke test in the background.
The terminal shows the start time and collection progress.
You still need to complete any CAPTCHAs in the browser.

Keep this terminal open until the check finishes.
It removes the temporary scheduler afterward and leaves the real scheduler and study data alone.
Allow one to two minutes for startup plus time for the questions.
If no automatic start is seen within two and a half minutes, the check reports **FAIL** and explains what happened.
It also refuses to start if a real study run is due within the next hour, to keep their VPN work from overlapping.
This test does not change your Mac's sleep settings; keep it awake and logged in.

Look for **PASS** at the end.
Test answers are saved separately under `results/checks/study-smoke/`; your real study CSV and schedule stay unchanged.
This is the study “smoke test”: a small trial of the collection process.

You can also test the country and emergency-number questions, along with day 1's study questions, with:

```bash
python main.py check-browser-prompt
```

Run browser tests one at a time, while no scheduled collection is underway, because they all switch the same VPN.
[Checks explained](checks.md) describes what each test covers.

### 7. Leave the Mac ready for the scheduled run

Once installed, the scheduler is already running automatically.
There is no additional start command.
It checks the saved time at each minute boundary and collects when the run is due.
While a collection is in progress (including a CAPTCHA wait), later minute ticks see a lock at `private/scheduler/collection.lock` and exit with `SKIP` instead of starting a second collector.

The collector disconnects Mullvad when a day or check finishes, on SIGINT/SIGTERM, and when `vpn.max_session_seconds` elapses (default two hours).
To take the tunnel down immediately without abandoning the active study (schedule and CSV stay as they are):

```bash
python main.py vpn-off
```

That disconnects Mullvad, asks the running collector to stop, and force-stops it only if soft signals fail.
Incomplete cells remain available for a later `run-due`.

Keep the Mac awake, connected to the internet, and logged in.
You can close the editor and Terminal.
If the Mac sleeps, collection waits until it wakes.
You may still need to complete a CAPTCHA when the browser opens.

After a study run, look here (replace `<id>` with the active study id from `results/studies/ACTIVE` or `python main.py study-status`):

| What you want to see | Where to look |
|---|---|
| Study answers as a readable report | `results/studies/<id>/results.md` |
| All study answers in a spreadsheet-friendly file | `results/studies/<id>/results.csv` |
| Full details for an individual study run | `results/studies/<id>/runs/` |
| Test results | `results/checks/results.md` |
| Next planned time | `run_at` in `results/studies/<id>/schedule.json` |

The random schedule chooses the following day's time after the current day finishes.
For fixed times or retry options, see the optional settings below.

The installer uses the Mac's calendar clock to check at each minute boundary.
An earlier version used a repeating interval timer; this Mac left those jobs pending even while awake.
If you have that version, update it with `--replace` and repeat the delayed smoke test.
A manually started test alone does not confirm that automatic minute checks are firing.

### If something fails

| Message or problem | What to do |
|---|---|
| `Operation not permitted` or a file-access error | Repeat step 3 for the exact Python location printed by the command, then repeat the startup test. |
| `Scheduler already installed` | Continue to step 5. |
| The scheduler is not loaded | Follow “Pause or reinstall” below to reinstall it, then repeat step 5. |
| The startup test says a run is due soon or already running | Let that run finish and try later, once a future run is scheduled. |
| A browser shows a CAPTCHA | Complete it in the visible browser window. |
| A check ends with `FAIL` | Read the failed item in `results/checks/results.md`. For startup errors, also look in `private/scheduler/stderr.log`. |

Old error messages remain in the log.
A new **PASS** from the startup test is more useful than an older error further up the file.

### Pause or reinstall

To pause automatic scheduling for your current login session, run:

```bash
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/local.ip-location-experiment.plist
```

Wait for any collection to finish before pausing.
To keep it switched off at future logins too, remove `local.ip-location-experiment.plist` from `~/Library/LaunchAgents/` after running the pause command.
In Finder, press **Command–Shift–G** and paste that folder location to find it.
Your collected answers and schedule remain in the project.

To switch scheduling back on, run the installation command with `--replace`, then repeat step 5.
If you moved the project folder, pause and remove the old instruction file first, then repeat those steps from the new location.
If Python changed, repeat step 3 too.

The instruction file is local to your Mac.
The project includes the installer so another Mac can repeat these same setup steps.
Editing the questions or study settings does not require reinstalling it.

## Optional: timing, missed answers, and notifications

The sections below explain the settings you can change after setup.
You can keep the defaults to start: a random daily time, missed answers saved as failures, and phone notifications off.

### What happens if you miss a human-verification check?

A scheduled day opens ChatGPT or Gemini through Playwright.
A CAPTCHA may appear.
The collector waits up to `browser.human_captcha_timeout_seconds` (default 300 seconds; raise it in `config.yaml` if you need longer), prints `HUMAN ACTION NEEDED`, and optionally alerts you.
The wait duration and that timeout budget are written to `human_wait_ms` and `human_timeout_seconds` on the CSV row.
If nobody is at the Mac:

- With the default **skip** recovery, that observation is saved as `status: captcha`, the day is treated as finished, and the next study day is drawn. The missing cell is **not** retried later (the CSV key is skipped on later runs).
- With **retry** recovery, the day stays incomplete. The scheduler picks a later time in the retry window and collects only the cells that are still not `ok`.

Closing the laptop does not redraw a pending `run_at`.
When the Mac wakes, `run-due` runs if `now >= run_at`.
The LaunchAgent does not wake the machine.

## Three separate concerns

| Concern | Config key | What it controls |
|---|---|---|
| When days are planned | `schedule_policy` | `random` vs fixed `agenda` times |
| Failed / CAPTCHA cells | `recovery_policy` | `skip` gaps vs `retry` later |
| Notify a person | `alerts` | ntfy / webhook when human action is needed |

Settings live under `experiment:` in `config.yaml`.

## Schedule policy

### `random` (default)

After a day completes for the browser (or API) mode, the next day’s `run_at` is drawn at random inside `schedule_window` (default 08:00–21:59 in `experiment.timezone`).
Location order is randomized per day. `schedule.json` keeps completed days plus at most one pending day.

```bash
python main.py schedule                 # first day tomorrow (or --start-date)
python main.py schedule --seed 354      # optional reproducible first draw
```

### `agenda`

Fixed times you choose in advance (and can copy into a calendar app).

1. Copy [agenda.example.json](../agenda.example.json) to `agenda.json`.
2. Edit each day’s `run_at` and optional `location_order`.
3. Set `schedule_policy: agenda` in config, or pass the file on the CLI:

```bash
python main.py schedule --agenda agenda.json
```

All listed days (within `experiment.days`) are written into `schedule.json` up front.
Do not regenerate the schedule mid-study unless you intend to reset dates and completion tracking.

Example entry:

```json
{
  "day": 1,
  "run_at": "2026-10-20T16:16:00-07:00",
  "location_order": ["IL", "US"]
}
```

## Recovery policy

### `skip` (default)

Matches the original study behavior:

1. CAPTCHA times out → row written with `status: captcha`.
2. Collection continues to the next cell.
3. Day is marked complete for that mode; the next day is scheduled.
4. Later `run-day` / `run-due` calls **skip** any key already present in the CSV (any status), so the gap stays unless you edit the CSV by hand.

Use this when a short pilot can tolerate missing cells.

### `retry` (try missed answers again)

1. Only rows with `status: ok` count as filled (plus cells that already hit `recovery.max_attempts_per_cell`).
2. After `run-due`, if any planned cell for that day is still missing, the day is **not** marked complete and **no** next day is drawn.
3. The same day’s `run_at` is moved to a random time at least ~15 minutes later inside `recovery.retry_window` (same calendar day when possible, else the next window).
4. The LaunchAgent keeps calling `run-due`; holes are filled when you are present (or when a later attempt succeeds without a CAPTCHA).
5. After `max_attempts_per_cell` failed rows for one key, that cell is abandoned so the day can eventually complete.

Override for one shell invocation (LaunchAgent ignores this unless you change the plist):

```bash
python main.py run-due --mode browser --recovery retry
```

For automatic collection, set `recovery_policy: retry` in `config.yaml`.

## Alerts

Without any alert config, a CAPTCHA still prints `HUMAN ACTION NEEDED` and beeps in `private/scheduler/` logs.
That is easy to miss if you are away from the Terminal.
Optional alerts add a Mac banner and/or a phone push for CAPTCHAs and for the advance notice before a scheduled run.

For desktop setup, open `Phone alerts…`, enable notifications, and choose a phone service.
Prefer `Phone (Telegram)` when you need a reliable iPhone banner; use `Phone (ntfy)` if that channel already works for you.
For Telegram, paste the bot token from BotFather and your numeric chat id.
For ntfy, paste a topic or subscription link (or generate one) and subscribe in the ntfy app.
`Save` stores the settings without sending a notification, while `Save & send phone test` explicitly sends a test.
The [desktop app guide](gui.md#phone-alerts) covers these controls.
If the app was already open before Telegram was added, let any task finish and reopen it to see the updated service list.

Desktop alert choices are stored locally in the ignored `private/phone-alerts.json` and used by command-line collection and the automatic scheduler too.
They override corresponding `config.yaml` settings, including a topic set through `NTFY_TOPIC` in the environment or `.env`.
Saved settings apply to future notifications, including notifications from a task already running.
Advance reminders use them on the next scheduler check.
The terminal examples below are useful for setup without saved desktop overrides.

### A. Mac laptop only (Notification Center)

1. In `config.yaml`, under `experiment.alerts`, set:

```yaml
  alerts:
    enabled: true
    macos_notification: true
    provider: none
    ntfy_topic: ""
    advance_notice_minutes: 10
```

2. Allow notifications if macOS asks (System Settings → Notifications → Script Editor or `osascript`, depending on your macOS version).
3. Test from the project folder after `source setup.sh`:

```bash
python -c 'from experiment.alerts import send_alert; from experiment.config import load_config; c=load_config(); c["experiment"]["alerts"]["enabled"]=True; send_alert("IP-location study: test", "Mac alerts work.", cfg=c)'
```

You should see a Notification Center banner.
No phone app is required for this path.

### B. Phone as well (or instead)

Mac Notification Center (section A) and a phone channel are independent; you can use both at once.
The desktop `Phone alerts…` dialog is the usual setup path; the notes below are for CLI / `.env` setup.

#### Telegram (recommended on iPhone)

1. In Telegram, open [@BotFather](https://t.me/BotFather), create a bot, and copy the bot token.
2. Open a chat with your new bot and send any message (so Telegram knows your chat id).
3. Get your numeric chat id (for example from [@userinfobot](https://t.me/userinfobot)).
4. Either enter token and chat id in `Phone alerts…` → `Phone (Telegram)`, or put them in `.env`:

```bash
echo 'TELEGRAM_BOT_TOKEN=123456:replace-me' >> .env
echo 'TELEGRAM_CHAT_ID=987654321' >> .env
```

5. Set `provider: telegram` under `experiment.alerts` in `config.yaml`, or choose Telegram in the desktop dialog (private settings override config).
6. Use `Save & send phone test`, or:

```bash
source setup.sh
python -c 'from experiment.alerts import send_alert; from experiment.config import load_config; c=load_config(); c["experiment"]["alerts"]["enabled"]=True; c["experiment"]["alerts"]["provider"]="telegram"; send_alert("IP-location study: test", "Phone and Mac alerts work.", cfg=c)'
```

#### ntfy

ntfy posts to a **topic** URL; any device subscribed to that topic gets the message.
On iPhone, system banners are often unreliable even when the message appears in the ntfy app.

1. Invent a hard-to-guess topic string (a shared secret), for example `ip-loc-kal-x7f9q2m`.
2. Put it in `.env` (gitignored), not in the committed `config.yaml`:

```bash
echo 'NTFY_TOPIC=ip-loc-kal-x7f9q2m' >> .env
```

3. In `config.yaml`, set `provider: ntfy` (leave `ntfy_topic` empty if `NTFY_TOPIC` is in `.env`).
4. On your phone, install [ntfy](https://ntfy.sh), tap Subscribe, enter the **same** topic string, and enable notifications for the app.
5. Test with `Save & send phone test` or the same `send_alert` one-liner after enabling ntfy in settings.

### What the settings mean

| Setting | Meaning |
|---|---|
| `enabled: true` | Turn on Mac and/or phone/webhook delivery (not only the log beep). |
| `macos_notification: true` | Show a Notification Center banner on this Mac. |
| `provider: telegram` | Send via the Telegram Bot API. |
| `provider: ntfy` | Also POST to ntfy when a topic is set. |
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` in `.env` | Bot credentials (or enter them in Phone alerts…). |
| `NTFY_TOPIC` in `.env` or `ntfy_topic` in config | The secret topic name devices subscribe to. |
| `advance_notice_minutes: 10` | About 10 minutes before `run_at`, send one “upcoming run” alert (same channels). |
| `advance_notice_minutes: 0` | No advance ping; CAPTCHA alerts still use Mac/phone. |

The advance notice is recorded on the schedule event as `advance_notice_sent_for` so it fires once per `run_at`.

**webhook:** set `provider: webhook` and `webhook_url` to POST JSON `{"title", "message", "provider"}` (Telegram bots, Zapier, etc.).

Alerts do not solve CAPTCHAs.
They only tell you to look at the Mac.
They work with both `skip` and `retry`.

## Suggested combinations

| Situation | `schedule_policy` | `recovery_policy` | Alerts |
|---|---|---|---|
| Simple pilot, accept gaps | `random` | `skip` | off |
| Laptop study, heal overnight misses | `random` | `retry` | Mac on; ntfy optional |
| Fixed hours in your calendar | `agenda` | `retry` | Mac on; ntfy optional |

## Config reference (excerpt)

```yaml
experiment:
  schedule_policy: random          # random | agenda
  agenda_path: "agenda.json"

  recovery_policy: skip            # skip | retry
  recovery:
    max_attempts_per_cell: 3
    retry_window:
      start_hour: 8
      end_hour: 21

  alerts:
    enabled: false
    macos_notification: true
    provider: ntfy              # none | ntfy | telegram | webhook
    ntfy_topic: ""              # or set NTFY_TOPIC in .env
    ntfy_server: "https://ntfy.sh"
    telegram_bot_token: ""      # or set TELEGRAM_BOT_TOKEN in .env
    telegram_chat_id: ""        # or set TELEGRAM_CHAT_ID in .env
    webhook_url: ""
    advance_notice_minutes: 10

  schedule_window:
    start_hour: 8
    end_hour: 21
```

## Command reference (optional)

```bash
python main.py schedule
python main.py schedule --agenda agenda.json
python main.py run-due --mode browser
python main.py run-due --mode browser --recovery retry
python main.py run-day --day 1 --mode browser --recovery skip
```

`--mode` remains the collector (api / browser / both). `--recovery` and `--agenda` are the schedule/recovery knobs.
