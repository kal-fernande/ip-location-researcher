"""Small desktop console for the existing experiment commands."""
from __future__ import annotations

import json
import os
import queue
import re
import subprocess
import sys
import threading
import tkinter as tk
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, ttk

from .gui_support import ROOT, TaskRunner, find_desktop_python, read_dashboard


@dataclass(frozen=True)
class Option:
    flag: str
    label: str
    kind: str = "text"
    default: str = ""
    choices: tuple[str, ...] = ()
    required: bool = False


@dataclass(frozen=True)
class Action:
    label: str
    args: tuple[str, ...]
    description: str
    options: tuple[Option, ...] = ()
    script: str = "main.py"


MODE = Option("--mode", "Collect using", "choice", "Browser", ("Browser", "API", "Browser and API"))
RECOVERY = Option("--recovery", "Missing answers", "choice", "Use study setting",
                  ("Use study setting", "Keep gaps and continue", "Retry missing answers"))
DAY = Option("--day", "Study day", "day", "1", required=True)
PROVIDER = Option("--provider", "Browser source", "provider", "All enabled sources")
DATE = Option("--start-date", "First date (YYYY-MM-DD; blank = tomorrow)", "date")
SEED = Option("--seed", "Random seed (optional)", "integer")
AGENDA = Option("--agenda", "Fixed-times agenda JSON (optional)", "file")
CHECKS = (
    Action("Browser readiness", ("check-browser",),
           "Checks the VPN and opens each enabled browser source without sending questions.", (PROVIDER,)),
    Action("Browser answers", ("check-browser-prompt",),
           "Sends check questions in each browser source; answers stay separate from the study. This can take several minutes.",
           (PROVIDER, Option("--study-only", "Ask only the study question", "flag"))),
    Action("VPN only", ("check", "--no-api"), "Checks VPN locations without sending questions."),
    Action("VPN and API answers", ("check",), "Checks VPN locations and sends questions to enabled API providers."),
    Action("VPN, API and browser answers", ("check", "--browser"),
           "Sends check questions through enabled API and browser sources; answers stay separate from the study."),
    Action("Practice study run", ("check-browser-study",),
           "Runs the full browser study pipeline in a separate test folder. This can take several minutes.",
           (Option("--launchagent-delay", "Test background startup after a 60-second delay (Mac)", "flag"),)),
    Action("Automatic scheduler setup", ("check-scheduler",),
           "Checks configuration and the installed macOS scheduler without collecting answers.",
           (Option("--probe", "Also test a fresh background launch (refuses near a due run)", "flag"),)),
)
TOOLS = (
    Action("Run scheduled days once", ("run-due",),
           "Runs due study days once using the saved schedule, with optional source and recovery settings.", (MODE, RECOVERY)),
    Action("Run a specific study day now", ("run-day",),
           "Collects the chosen day immediately. Existing answers are skipped by default. This does not advance the saved schedule.",
           (DAY, MODE, RECOVERY, Option("--force", "Collect again even when answers already exist (adds rows)", "flag"))),
    Action("Keep checking the schedule", ("daemon",),
           "Keeps this task running and collects when saved times become due. Keep the Mac awake. Use Stop current task to finish.",
           (MODE, RECOVERY, Option("--poll-seconds", "Check every (seconds)", "day", "30"))),
    Action("Start a new study", ("start-study",),
           "Creates a new study folder and schedules day 1. Automatic scheduling is installed separately.",
           (Option("--name", "Study name (optional)"), Option("--id", "Folder id (optional)"), DATE, SEED, AGENDA, Option("--notes", "Notes (optional)"),
            Option("--force", "Make the new study active even if a study is already active", "flag"))),
    Action("Recreate the active study schedule", ("schedule",),
           "Replaces the active study's schedule starting at day 1. Use only when deliberately resetting the schedule.",
           (DATE, SEED, AGENDA)),
    Action("Analyze study results", ("analyze",),
           "Writes comparison tables and a descriptive report in the active study's analysis folder."),
    Action("Recover saved source links", ("refresh-links",),
           "Updates the study CSV and Excel file using links already saved in run files. Does not collect new answers."),
    Action("Validate saved browser evidence", ("validate-evidence",),
           "Checks a saved check/run JSON against local browser evidence and optional CSV without new searches.",
           (Option("run_json", "Saved check or run JSON", "file", required=True),
            Option("--csv", "CSV to compare (optional)", "file"))),
    Action("Collect answers by hand", ("collect-manual",),
           "Uses your own logged-in browser. Read the activity instructions, then send the model name, a blank ready line, and each pasted answer below.",
           (Option("--provider", "App name", default="claude", required=True),
            Option("--day", "Study day (blank = a check)", "day"))),
    Action("Install automatic scheduling (Mac)", ("--install",),
           "Installs the browser scheduler on this Mac. Due collection may start immediately. An active study and saved schedule are required.",
           (Option("--replace", "Replace this project's existing scheduler when idle", "flag"),),
           "scripts/install_scheduler.py"),
    Action("Preview automatic scheduler settings", (),
           "Shows the macOS scheduler definition without installing it.", script="scripts/install_scheduler.py"),
)


def build_arguments(action: Action, values: dict) -> list[str]:
    """Validate form values before starting a command; never use a shell."""
    args = list(action.args)
    for option in action.options:
        value = values.get(option.flag, option.default)
        if option.kind == "flag":
            if value:
                args.append(option.flag)
            continue
        value = str(value).strip()
        if option.required and not value:
            raise ValueError(f"Please enter {option.label.lower()}.")
        if not value:
            continue
        if option.flag == "--mode":
            value = {"Browser": "browser", "API": "api", "Browser and API": "both"}[value]
        elif option.flag == "--recovery":
            value = {"Use study setting": "", "Keep gaps and continue": "skip", "Retry missing answers": "retry"}[value]
            if not value:
                continue
        elif option.kind == "provider" and value == "All enabled sources":
            continue
        elif option.kind in ("day", "integer"):
            try:
                number = int(value)
            except ValueError:
                raise ValueError(f"{option.label} must be a whole number.") from None
            if option.kind == "day" and number < 1:
                raise ValueError(f"{option.label} must be at least 1.")
        elif option.kind == "date":
            try:
                date.fromisoformat(value)
            except ValueError:
                raise ValueError("Enter the first date as YYYY-MM-DD.") from None
        elif option.kind == "file":
            path = Path(value).expanduser()
            if not path.is_absolute():
                path = ROOT / path
            if not path.is_file():
                raise ValueError(f"Choose an existing file for {option.label.lower()}.")
            value = str(path)
        if option.flag.startswith("--"):
            args.extend((option.flag, value))
        else:
            args.append(value)
    return args


def completion_message(args: list[str], code: int, output: str, cancelled: bool) -> tuple[str, str]:
    """Do not equate a collector's zero exit code with complete research data."""
    if cancelled:
        return "Stopped. Any saved answers remain available; check the activity for details.", "notice"
    if args and args[0] == "stop-study":
        if code:
            if "Study stopped:" in output:
                return "Study stopped. Collector cleanup needs attention; review the activity before creating a new study.", "notice"
            return "Could not stop the study. Review the activity and refresh the study details.", "error"
        if "No active study" in output:
            return "No active study. Use New study… to begin.", "notice"
        return "Study stopped. Saved results were kept. Use New study… to begin again.", "ok"
    if code:
        if args and args[0] == "retry-day" and "Manual retry finished with gaps" in output:
            return "Retry finished with some answers still missing. You can retry that day again when ready.", "notice"
        return "Needs attention. Read the activity below for the reason.", "error"
    if "Collection already running" in output:
        return "Another collection is already running. No second collection was started.", "notice"
    if args and args[0] == "retry-day" and "is already complete. No answers were collected." in output:
        return "That day is already complete. No answers were collected.", "ok"
    failed = "finished with gaps" in output or "incomplete; recovery=retry" in output
    for filename in re.findall(r"Run JSON written to (.+)", output):
        try:
            failed = failed or not json.loads(Path(filename.strip()).read_text(encoding="utf-8")).get("ok", False)
        except (OSError, ValueError):
            return "Finished. Could not verify the saved run; review the activity and results.", "notice"
    if failed or re.search(r"^FAIL\b", output, re.MULTILINE):
        return "Finished with missing or failed answers. Review the activity and saved results.", "notice"
    if args and args[0] == "run-due" and "Running scheduled day" not in output:
        return "No collection was needed. The next saved run time is shown above.", "notice"
    return "Finished. Review the activity and saved results below.", "ok"


class ExperimentApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.runner = TaskRunner()
        self.disconnect_runner = TaskRunner()
        self.disconnect_args = []
        self.disconnect_output = ""
        self.study_stop_message = None
        self.desktop_events = queue.Queue()
        self.dashboard = {}
        self.task_args = []
        self.task_output = ""
        self.closing = False
        self.controls = []
        self.form_values = {}
        self.check_values = {}
        self.phone_alert_dialog = None
        self.retry_window = None
        self.new_study_window = None
        self.root.title("IP-location experiment")
        self.root.geometry("1040x850")
        self.root.minsize(860, 780)
        self.root.configure(background="#f3f6fa")
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self._styles()
        self._build()
        self.refresh()
        self.root.after(80, self.poll)
        self.root.after(15000, self.auto_refresh)

    def _styles(self):
        style = ttk.Style(self.root)
        if "clam" in style.theme_names():
            style.theme_use("clam")
        style.configure("TFrame", background="#f3f6fa")
        style.configure("TLabel", background="#f3f6fa", foreground="#24394c", font=("Helvetica Neue", 11))
        style.configure("Title.TLabel", font=("Helvetica Neue", 21, "bold"))
        style.configure("Heading.TLabel", font=("Helvetica Neue", 14, "bold"))
        style.configure("Next.TLabel", font=("Helvetica Neue", 17, "bold"))
        style.configure("TButton", padding=(12, 8), font=("Helvetica Neue", 12))
        style.configure("Primary.TButton", background="#28658a", foreground="white")
        style.map("Primary.TButton", background=[("disabled", "#b9c6cf"), ("active", "#1d4f70")])
        style.configure("TCheckbutton", background="#f3f6fa", font=("Helvetica Neue", 11))
        style.configure("TNotebook.Tab", padding=(16, 8), font=("Helvetica Neue", 12))
        style.configure("TCombobox", padding=5, font=("Helvetica Neue", 12))

    def _button(self, parent, text, command, *, primary=False, managed=True):
        button = ttk.Button(parent, text=text, command=command,
                            style="Primary.TButton" if primary else "TButton")
        if managed:
            self.controls.append(button)
        return button

    def _build(self):
        outer = ttk.Frame(self.root, padding=18)
        outer.pack(fill="both", expand=True)
        self.wrap_labels = []
        heading = ttk.Frame(outer)
        heading.pack(fill="x")
        ttk.Label(heading, text="Research console", style="Title.TLabel").pack(side="left")
        self._button(heading, "Phone alerts…", self.show_phone_alerts, managed=False).pack(side="right")
        self._button(heading, "New study…", self.show_new_study_dialog).pack(side="right", padx=8)
        ttk.Label(outer, text="IP-location chatbot experiment · Run checks, collect answers and open your results.").pack(anchor="w", pady=(3, 16))
        self.study_text = tk.StringVar(value="Reading study…")
        self.next_text = tk.StringVar()
        self.schedule_text = tk.StringVar()
        self.collector_text = tk.StringVar()
        study_label = ttk.Label(outer, textvariable=self.study_text, style="Heading.TLabel", wraplength=970)
        study_label.pack(anchor="w")
        self.wrap_labels.append(study_label)
        next_label = ttk.Label(outer, textvariable=self.next_text, style="Next.TLabel", wraplength=970)
        next_label.pack(anchor="w", pady=(6, 2))
        schedule_label = ttk.Label(outer, textvariable=self.schedule_text, wraplength=970)
        schedule_label.pack(anchor="w")
        self.wrap_labels.extend((next_label, schedule_label))
        ttk.Label(outer, textvariable=self.collector_text).pack(anchor="w", pady=(3, 10))
        row = ttk.Frame(outer)
        row.pack(fill="x", pady=(0, 14))
        self._button(row, "Refresh next run time", self.refresh, managed=False).pack(side="left")
        self._button(row, "Open study CSV", lambda: self.open_result("csv_path"), managed=False).pack(side="left", padx=6)
        self._button(row, "Open Excel", lambda: self.open_result("xlsx_path"), managed=False).pack(side="left")
        self._button(row, "Open study folder", lambda: self.open_result("root"), managed=False).pack(side="left", padx=6)

        notebook = ttk.Notebook(outer)
        notebook.pack(fill="x")
        basic = ttk.Frame(notebook, padding=14)
        advanced = ttk.Frame(notebook, padding=14)
        notebook.add(basic, text="Study & checks")
        notebook.add(advanced, text="More tools")
        study_row = ttk.Frame(basic)
        study_row.pack(fill="x")
        self._button(study_row, "Run scheduled study", self.run_scheduled, primary=True).pack(side="left")
        self._button(study_row, "Manual run / retry…", self.show_retry_dialog, managed=False).pack(side="left", padx=8)
        self.stop_study_button = self._button(study_row, "Stop study…", self.stop_study, managed=False)
        self.stop_study_button.pack(side="left")
        ttk.Label(basic, text="Run due days once, or retry an incomplete day when you are here to help with browser checks.", wraplength=930).pack(anchor="w", pady=(6, 0))
        ttk.Separator(basic).pack(fill="x", pady=12)
        checks_row = ttk.Frame(basic)
        checks_row.pack(fill="x")
        self.check_choice = tk.StringVar(value=CHECKS[0].label)
        self.check_select = ttk.Combobox(checks_row, textvariable=self.check_choice,
                                         values=[a.label for a in CHECKS], state="readonly", width=34)
        self.check_select.pack(side="left")
        self.controls.append(self.check_select)
        self.check_select.bind("<<ComboboxSelected>>", lambda _: self.render_check())
        self._button(checks_row, "Run a check", self.run_check, primary=True).pack(side="left", padx=10)
        self.check_description = ttk.Label(basic, wraplength=930)
        self.wrap_labels.append(self.check_description)
        self.check_description.pack(anchor="w", pady=(8, 4))
        self.check_form = ttk.Frame(basic)
        self.check_form.pack(fill="x")
        self.render_check()

        self.tool_choice = tk.StringVar(value=TOOLS[0].label)
        tool_row = ttk.Frame(advanced)
        tool_row.pack(fill="x")
        self.tool_select = ttk.Combobox(tool_row, textvariable=self.tool_choice,
                                        values=[a.label for a in TOOLS], state="readonly", width=44)
        self.tool_select.pack(side="left")
        self.controls.append(self.tool_select)
        self._button(tool_row, "Run selected tool", self.run_tool).pack(side="left", padx=10)
        self.tool_select.bind("<<ComboboxSelected>>", lambda _: self.render_tool())
        self.tool_description = ttk.Label(advanced, wraplength=930)
        self.wrap_labels.append(self.tool_description)
        self.tool_description.pack(anchor="w", pady=(8, 4))
        form_shell = ttk.Frame(advanced)
        form_shell.pack(fill="x")
        self.tool_canvas = tk.Canvas(form_shell, height=1, highlightthickness=0, background="#f3f6fa")
        form_scroll = ttk.Scrollbar(form_shell, orient="vertical", command=self.tool_canvas.yview)
        form_scroll.pack(side="right", fill="y")
        self.tool_canvas.configure(yscrollcommand=form_scroll.set)
        self.tool_canvas.pack(side="left", fill="x", expand=True)
        self.tool_form = ttk.Frame(self.tool_canvas)
        form_window = self.tool_canvas.create_window(0, 0, window=self.tool_form, anchor="nw")
        self.tool_form.bind("<Configure>", lambda _: self.tool_canvas.configure(scrollregion=self.tool_canvas.bbox("all")))
        self.tool_canvas.bind("<Configure>", lambda e: self.tool_canvas.itemconfigure(form_window, width=e.width))
        self.render_tool()

        self.notice = tk.Label(outer, text="Ready.", anchor="w", justify="left", wraplength=950,
                               background="#e7eef5", foreground="#24394c", padx=12, pady=9,
                               font=("Helvetica Neue", 12))
        self.notice.pack(fill="x", pady=(14, 6))
        activity_row = ttk.Frame(outer)
        activity_row.pack(fill="x", pady=(2, 5))
        ttk.Label(activity_row, text="Activity", style="Heading.TLabel").pack(side="left")
        self.stop_button = self._button(activity_row, "Stop current task", self.stop, managed=False)
        self.stop_button.pack(side="right")
        self.stop_button.state(["disabled"])
        self.manual_button = self._button(activity_row, "Enter answer…", self.show_input, managed=False)
        self.disconnect_button = self._button(activity_row, "Disconnect VPN", self.disconnect, managed=False)
        self.disconnect_button.pack(side="right", padx=6)
        self.output = scrolledtext.ScrolledText(outer, height=9, state="disabled", wrap="word",
                                               font=("Menlo", 11), background="white", foreground="#24394c")
        self.output.pack(fill="both", expand=True)
        self.output.tag_configure("attention", foreground="#9c4a00")
        self.input_window = tk.Toplevel(self.root)
        self.input_window.title("Enter manual answer")
        self.input_window.geometry("680x300")
        self.input_window.minsize(520, 240)
        self.input_window.protocol("WM_DELETE_WINDOW", self.input_window.withdraw)
        self.input_window.withdraw()
        self.input_frame = ttk.Frame(self.input_window, padding=14)
        self.input_frame.pack(fill="both", expand=True)
        ttk.Label(self.input_frame, text="Manual collection: send one line for the model/ready step, or a full pasted answer.", wraplength=950).pack(anchor="w", pady=(6, 3))
        self.input_text = scrolledtext.ScrolledText(self.input_frame, height=3, wrap="word", font=("Helvetica Neue", 12))
        self.input_text.pack(fill="both", expand=True)
        input_row = ttk.Frame(self.input_frame)
        input_row.pack(fill="x", pady=(4, 0))
        ttk.Button(input_row, text="Send line / ready", command=lambda: self.send_input(False)).pack(side="left")
        ttk.Button(input_row, text="Send full answer", command=lambda: self.send_input(True)).pack(side="left", padx=6)
        outer.bind("<Configure>", lambda e: self.resize_text(e.width))

    def resize_text(self, width):
        for label in self.wrap_labels:
            label.configure(wraplength=max(300, width - 64))
        self.notice.configure(wraplength=max(300, width - 32))

    def _selected(self, actions, label):
        return next(a for a in actions if a.label == label)

    def render_check(self):
        action = self._selected(CHECKS, self.check_choice.get())
        self.check_description.configure(text=action.description)
        self.check_values = self.render_form(self.check_form, action)

    def render_tool(self):
        action = self._selected(TOOLS, self.tool_choice.get())
        self.tool_description.configure(text=action.description)
        self.form_values = self.render_form(self.tool_form, action)
        self.tool_form.update_idletasks()
        self.tool_canvas.configure(height=min(self.tool_form.winfo_reqheight(), 120))
        self.tool_canvas.yview_moveto(0)

    def render_form(self, parent, action):
        for widget in parent.winfo_children():
            widget.destroy()
        values = {}
        for row, option in enumerate(action.options):
            if option.kind == "flag":
                var = tk.BooleanVar(value=False)
                widget = ttk.Checkbutton(parent, text=option.label, variable=var)
                widget.grid(row=row, column=0, columnspan=3, sticky="w", pady=3)
            else:
                var = tk.StringVar(value=option.default)
                ttk.Label(parent, text=option.label).grid(row=row, column=0, sticky="w", padx=(0, 12), pady=3)
                if option.kind in ("choice", "provider"):
                    choices = option.choices or ("All enabled sources", *self.dashboard.get("providers", ()))
                    widget = ttk.Combobox(parent, textvariable=var, values=choices, state="readonly", width=31)
                else:
                    widget = ttk.Entry(parent, textvariable=var, width=34)
                widget.grid(row=row, column=1, sticky="ew", pady=3)
                if option.kind == "file":
                    ttk.Button(parent, text="Choose…", command=lambda v=var: self.choose_file(v)).grid(row=row, column=2, padx=(6, 0))
            values[option.flag] = var
        parent.columnconfigure(1, weight=1)
        return values

    def choose_file(self, var):
        path = filedialog.askopenfilename(parent=self.root, initialdir=str(ROOT), title="Choose a saved file")
        if path:
            var.set(path)

    def refresh(self):
        self.dashboard = read_dashboard()
        state = self.dashboard
        name = state.get("study_name") or state.get("study_id")
        self.study_text.set(f"Active study: {name}" if state.get("study_id") else "No active study — use New study… to begin")
        self.next_text.set(state.get("next_run_text", "Next run unavailable"))
        self.schedule_text.set(state.get("error") or state.get("schedule_text", ""))
        self.collector_text.set(state.get("collector_text", ""))
        for widget in self.check_form.winfo_children():
            if isinstance(widget, ttk.Combobox) and self.check_values.get("--provider"):
                widget.configure(values=("All enabled sources", *state.get("providers", ())))
        self.update_controls()

    def auto_refresh(self):
        self.refresh()
        self.root.after(15000, self.auto_refresh)

    def run_scheduled(self):
        self.refresh()
        if self.dashboard.get("error"):
            messagebox.showinfo("Study needs attention", self.dashboard["error"], parent=self.root)
            return
        self.start(["run-due", "--mode", "browser"], "Scheduled study")

    def show_new_study_dialog(self):
        if self.runner.running or self.disconnect_runner.running:
            return
        if self.new_study_window and self.new_study_window.winfo_exists():
            self.new_study_window.deiconify()
            self.new_study_window.lift()
            return
        window = self.new_study_window = tk.Toplevel(self.root)
        window.title("Create a new study")
        window.geometry("720x440")
        window.minsize(660, 420)
        window.transient(self.root)
        panel = ttk.Frame(window, padding=20)
        panel.pack(fill="both", expand=True)
        ttk.Label(panel, text="Create a new study", style="Heading.TLabel").pack(anchor="w")
        description = ttk.Label(panel, text="Each study gets its own local results folder, including CSV, Excel, saved runs and analysis. Previous studies stay saved separately.", wraplength=670)
        description.pack(anchor="w", pady=(8, 12))
        action = Action("Create study", ("start-study",), "", (
            Option("--name", "Study name (optional)"), DATE, Option("--notes", "Notes (optional)"),
        ))
        form = ttk.Frame(panel)
        form.pack(fill="x")
        fields = self.render_form(form, action)
        detail = ttk.Label(panel, text="The new study becomes active. Its results stay on this computer and are excluded from Git. If automatic scheduling is installed, it will use this study when a run is due.", wraplength=670)
        detail.pack(anchor="w", pady=(12, 0))
        panel.bind("<Configure>", lambda e: [label.configure(wraplength=max(300, e.width - 40)) for label in (description, detail)])
        buttons = ttk.Frame(panel)
        buttons.pack(side="bottom", fill="x", pady=(12, 0))

        def create():
            try:
                args = build_arguments(action, {key: variable.get() for key, variable in fields.items()})
            except ValueError as error:
                messagebox.showerror("Check the study details", str(error), parent=window)
                return
            self.refresh()
            if self.dashboard.get("collector_text") == "Collection is running":
                messagebox.showinfo("Collection in progress", "Wait for the current collection to finish before creating a new active study.", parent=window)
                return
            if self.dashboard.get("study_id"):
                current = self.dashboard.get("study_name") or self.dashboard["study_id"]
                if not messagebox.askokcancel("Make the new study active", f"Switch future runs from {current!r} to this new study? The previous study's saved files remain in its own folder.", parent=window):
                    return
                args.append("--force")
            window.destroy()
            self.start(args, "Create new study")

        ttk.Button(buttons, text="Create study", command=create, style="Primary.TButton").pack(side="left")
        ttk.Button(buttons, text="Cancel", command=window.destroy).pack(side="right")
        window.grab_set()

    def show_retry_dialog(self):
        if self.retry_window and self.retry_window.winfo_exists():
            self.retry_window.deiconify()
            self.retry_window.lift()
            return
        window = self.retry_window = tk.Toplevel(self.root)
        window.title("Manual study run / retry")
        window.geometry("700x360")
        window.minsize(620, 330)
        window.transient(self.root)
        panel = ttk.Frame(window, padding=20)
        panel.pack(fill="both", expand=True)
        ttk.Label(panel, text="Manual run / retry", style="Heading.TLabel").pack(anchor="w")
        description = ttk.Label(panel, text="Choose a due study day. Missing or failed answers are retried now; a day with all successful answers is skipped. Be here to complete any CAPTCHA.", wraplength=650)
        description.pack(anchor="w", pady=(8, 14))
        choice = tk.StringVar()
        selector = ttk.Combobox(panel, textvariable=choice, state="readonly", width=65)
        selector.pack(fill="x")
        status = ttk.Label(panel, wraplength=650)
        status.pack(anchor="w", pady=(8, 0))
        detail = ttk.Label(panel, text="Successful answers are kept. Failed attempts remain in the results, and an existing next run time is preserved.", wraplength=650)
        detail.pack(anchor="w", pady=(12, 0))
        panel.bind("<Configure>", lambda e: [label.configure(wraplength=max(300, e.width - 40)) for label in (description, detail, status)])
        buttons = ttk.Frame(panel)
        buttons.pack(side="bottom", fill="x", pady=(12, 0))
        selected = {"options": [], "labels": [], "study_id": None}

        def refresh_choices():
            self.refresh()
            issue = self.dashboard.get("error") or self.dashboard.get("retry_error")
            if self.runner.running or self.disconnect_runner.running:
                issue = "A task is running. Wait for it to finish, then refresh this list."
            options = [] if issue else self.dashboard.get("retry_days") or []
            labels = []
            for option in options:
                planned = datetime.fromisoformat(option["run_at"])
                outcome = f"{option['missing']} of {option['total']} answers missing" if option["missing"] else "Complete — will be skipped"
                labels.append(f"Day {option['day']} · {planned:%b %d, %Y} · {outcome}")
            previous = choice.get()
            selected.update(options=options, labels=labels, study_id=self.dashboard.get("study_id"))
            selector.configure(values=labels, state="readonly" if labels else "disabled")
            default = next((label for label, option in zip(labels, options) if option["missing"]), labels[0] if labels else "No due study days available")
            choice.set(previous if previous in labels else default)
            run_button.state(["!disabled"] if labels else ["disabled"])
            status.configure(text=issue or ("Completed days are safe to select; they do not collect again." if labels
                                          else "No study days are due yet. Future days cannot be started early from this window."))

        def run_selected():
            if choice.get() not in selected["labels"]:
                return
            day = selected["options"][selected["labels"].index(choice.get())]["day"]
            window.destroy()
            self.start(["retry-day", "--day", str(day), "--study-id", selected["study_id"]], f"Manual run / retry · day {day}")

        run_button = ttk.Button(buttons, text="Run selected day", command=run_selected, style="Primary.TButton")
        run_button.pack(side="left")
        ttk.Button(buttons, text="Refresh", command=refresh_choices).pack(side="left", padx=8)
        ttk.Button(buttons, text="Cancel", command=window.destroy).pack(side="right")
        refresh_choices()
        window.grab_set()

    def run_check(self):
        self.run_action(self._selected(CHECKS, self.check_choice.get()), self.check_values)

    def run_tool(self):
        self.run_action(self._selected(TOOLS, self.tool_choice.get()), self.form_values)

    def run_action(self, action, fields):
        try:
            args = build_arguments(action, {key: var.get() for key, var in fields.items()})
        except (ValueError, KeyError) as error:
            messagebox.showerror("Check the details", str(error), parent=self.root)
            return
        confirmation = None
        if args and args[0] == "schedule":
            confirmation = "This replaces the active study schedule starting at day 1. Recreating a schedule during a study can repeat days. Continue?"
        elif "--force" in args:
            confirmation = ("Make a new study active? Future scheduled runs will use the new study. The previous folder remains saved."
                            if args[0] == "start-study" else "Collect this day again and add rows even where answers already exist?")
        elif action.script != "main.py" and "--install" in args:
            confirmation = "Install automatic scheduling on this Mac? It will collect in the background when runs are due, including immediately if a saved run is overdue."
        if confirmation and not messagebox.askokcancel(action.label, confirmation, parent=self.root):
            return
        self.start(args, action.label, script=action.script)

    def start(self, args, label, *, script="main.py"):
        if self.runner.running or self.disconnect_runner.running:
            return
        self.study_stop_message = None
        self.task_args = args
        self.task_output = ""
        self.append_output(f"\n── {label} ──\n")
        self.set_notice(f"{label} is running. Progress appears below; browser checks may take several minutes.")
        if args and args[0] == "collect-manual":
            self.manual_button.pack(side="right", padx=6)
            self.show_input()
        else:
            self.manual_button.pack_forget()
            self.input_window.withdraw()
        self.input_text.delete("1.0", "end")
        try:
            self.runner.start(args, script=script)
        except (OSError, RuntimeError) as error:
            self.set_notice(f"Could not start: {error}", "error")
        self.update_controls()

    def append_output(self, text):
        self.output.configure(state="normal")
        self.output.insert("end", text, "attention" if "HUMAN ACTION NEEDED" in text else ())
        # Keep long-running monitors from retaining unlimited text in the window.
        if int(self.output.index("end-1c").split(".")[0]) > 6000:
            self.output.delete("1.0", "1000.0")
        self.output.see("end")
        self.output.configure(state="disabled")

    def set_notice(self, text, tone="normal"):
        colors = {"normal": ("#e7eef5", "#24394c"), "ok": ("#e5f1e9", "#235738"),
                  "notice": ("#fff0d8", "#795012"), "error": ("#f9e4e2", "#913529")}
        bg, fg = colors[tone]
        self.notice.configure(text=text, background=bg, foreground=fg)

    def update_controls(self):
        busy = self.runner.running or self.disconnect_runner.running or self.closing
        for widget in self.controls:
            if isinstance(widget, ttk.Combobox):
                widget.configure(state="disabled" if busy else "readonly")
            else:
                widget.state(["disabled"] if busy else ["!disabled"])
        self.stop_button.state(["!disabled"] if self.runner.running and not self.runner.cancelled else ["disabled"])
        self.disconnect_button.state(["disabled"] if self.disconnect_runner.running else ["!disabled"])
        can_stop_study = self.dashboard.get("study_id") and not self.disconnect_runner.running and not self.closing
        self.stop_study_button.state(["!disabled"] if can_stop_study else ["disabled"])

    def poll(self):
        for runner in (self.runner, self.disconnect_runner):
            while True:
                try:
                    kind, value = runner.events.get_nowait()
                except queue.Empty:
                    break
                if kind == "output":
                    self.append_output(value)
                    if runner is self.runner:
                        self.task_output += value
                        if (not self.disconnect_runner.running
                                and "HUMAN ACTION NEEDED" in self.task_output[-max(1000, len(value)):]):
                            self.set_notice("Action needed: complete the verification in the browser window. Collection will continue afterward.", "notice")
                    else:
                        self.disconnect_output += value
                elif kind == "error":
                    self.append_output(f"\n{value}\n")
                    self.set_notice(str(value), "error")
                elif kind == "input_sent":
                    self.append_output("\n[Input delivered]\n")
                elif kind == "input_error":
                    self.append_output(f"\nInput could not be delivered: {value}\n")
                    self.set_notice(f"Input could not be delivered: {value}", "error")
                elif kind == "done":
                    if runner is self.runner:
                        text, tone = completion_message(self.task_args, value, self.task_output, runner.cancelled)
                        if self.disconnect_runner.running and self.disconnect_args[:1] == ["stop-study"]:
                            self.set_notice("Stopping the study and cancelling future runs…", "notice")
                        else:
                            self.set_notice(*(self.study_stop_message or (text, tone)))
                        self.input_window.withdraw()
                        self.manual_button.pack_forget()
                    else:
                        if self.disconnect_args[:1] == ["stop-study"]:
                            self.study_stop_message = completion_message(self.disconnect_args, value, self.disconnect_output, False)
                            self.set_notice(*self.study_stop_message)
                        else:
                            self.set_notice("VPN disconnect task finished. Review the activity for details." if value == 0
                                            else "VPN disconnect needs attention. Review the activity below.", "notice")
                    self.refresh()
        while not self.desktop_events.empty():
            error = self.desktop_events.get_nowait()
            messagebox.showerror("Could not open the file", error, parent=self.root)
        self.update_controls()
        if self.closing and not self.runner.running and not self.disconnect_runner.running:
            self.root.destroy()
            return
        self.root.after(80, self.poll)

    def send_input(self, answer):
        if not self.runner.running:
            return
        value = self.input_text.get("1.0", "end-1c")
        if not answer and "\n" in value:
            messagebox.showinfo("Send a single line", "Use Send full answer for a pasted answer with multiple lines.", parent=self.root)
            return
        try:
            self.runner.send_input(value + ("\n.\n" if answer else "\n"))
            self.input_text.delete("1.0", "end")
            self.append_output("\n[Answer queued]\n" if answer else "\n[Line queued]\n")
        except (OSError, RuntimeError, ValueError) as error:
            self.set_notice(f"Could not send input: {error}", "error")

    def show_input(self):
        self.input_window.deiconify()
        self.input_window.lift()
        self.input_text.focus_set()

    def show_phone_alerts(self):
        if self.phone_alert_dialog and self.phone_alert_dialog.window.winfo_exists():
            self.phone_alert_dialog.window.deiconify()
            self.phone_alert_dialog.window.lift()
            return
        from .gui_phone_alerts import PhoneAlertsDialog

        self.phone_alert_dialog = PhoneAlertsDialog(
            self.root, on_saved=lambda: self.set_notice("Phone alert settings saved for future notifications.", "ok"),
        )

    def stop(self):
        self.runner.stop()
        self.set_notice("Stopping the current task. Waiting for it to save available results and disconnect its VPN…", "notice")
        self.update_controls()

    def disconnect(self):
        if self.disconnect_runner.running:
            return
        if not messagebox.askokcancel("Disconnect VPN", "Disconnect Mullvad and stop any study collector, including one started by automatic scheduling?", parent=self.root):
            return
        if self.runner.running:
            self.runner.stop()
        self.append_output("\n── Disconnect VPN ──\n")
        self.disconnect_args = ["vpn-off"]
        self.disconnect_output = ""
        self.disconnect_runner.start(self.disconnect_args)
        self.set_notice("Requesting VPN disconnect…", "notice")
        self.update_controls()

    def stop_study(self):
        if self.disconnect_runner.running or self.closing:
            return
        self.refresh()
        study_id = self.dashboard.get("study_id")
        if not study_id:
            messagebox.showinfo("No active study", "Use New study… to set up a new run.", parent=self.root)
            return
        name = self.dashboard.get("study_name") or study_id
        if not messagebox.askokcancel(
            "Stop study",
            f"Stop {name!r} and cancel its future runs?\n\n"
            "Any running collection will be stopped and its VPN disconnected. "
            "Saved results stay in the study folder. You can then use New study… to set up a new run.",
            parent=self.root,
        ):
            return
        self.disconnect_args = ["stop-study", "--study-id", study_id]
        self.disconnect_output = ""
        self.study_stop_message = None
        self.append_output("\n── Stop study ──\n")
        try:
            self.disconnect_runner.start(self.disconnect_args)
        except (OSError, RuntimeError) as error:
            self.set_notice(f"Could not stop the study: {error}", "error")
            return
        if self.runner.running:
            self.runner.stop()
        self.set_notice("Stopping the study and cancelling future runs. Waiting for collection cleanup…", "notice")
        self.update_controls()

    def open_result(self, key):
        self.refresh()
        path = self.dashboard.get(key)
        if not path or not Path(path).exists():
            messagebox.showinfo("No saved file yet", "This study has no saved file here yet. Results are created when answers are collected.", parent=self.root)
            return
        def open_in_desktop():
            try:
                command = ["open", str(path)] if sys.platform == "darwin" else ["xdg-open", str(path)]
                result = subprocess.run(command, capture_output=True, text=True, timeout=20)
                if result.returncode:
                    self.desktop_events.put(f"{result.stderr.strip() or 'No application could open this file.'}\n\nFile: {path}")
            except (OSError, subprocess.TimeoutExpired) as error:
                self.desktop_events.put(f"{error}\n\nFile: {path}")
        threading.Thread(target=open_in_desktop, daemon=True).start()

    def close(self):
        if self.closing:
            return
        if self.disconnect_runner.running:
            self.closing = True
            self.set_notice("Waiting for study and VPN cleanup before closing…", "notice")
        elif self.runner.running:
            if not messagebox.askokcancel("Task still running", "Stop the current task and close after it finishes saving and cleaning up?", parent=self.root):
                return
            self.closing = True
            self.stop()
        else:
            self.root.destroy()


def launch():
    # Apple's old system Tk can abort the interpreter before TclError is raised.
    # Keep collection in its configured environment while using a supported Tk
    # installation for this stdlib-only window if one is already on this Mac.
    if sys.platform == "darwin" and tk.TkVersion < 8.6:
        desktop_python = find_desktop_python()
        if not desktop_python:
            raise SystemExit(
                "The desktop console needs a Python installation with Tk 8.6 or later. "
                "On this Mac, run: source setup.sh"
            )
        environment = os.environ.copy()
        environment["IP_LOCATION_CLI_PYTHON"] = str(Path(sys.executable).absolute())
        result = subprocess.call([desktop_python, "-u", "-m", "experiment.gui"], cwd=str(ROOT), env=environment)
        if result:
            raise SystemExit(result)
        return
    try:
        root = tk.Tk()
    except tk.TclError as error:
        raise SystemExit(f"Could not open the desktop window: {error}\nUse a desktop session and Python with Tk support.") from error
    ExperimentApp(root)
    root.mainloop()


if __name__ == "__main__":
    launch()
