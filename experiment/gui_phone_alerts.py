"""Phone-alert preferences window; network and disk work stays off the Tk thread."""
from __future__ import annotations

import queue
import secrets
import threading
import tkinter as tk
from tkinter import ttk
from urllib.parse import quote, urlsplit

from .gui_support import alert_settings_request


SERVICES = {
    "Mac only": "none",
    "Phone (ntfy)": "ntfy",
    "Phone (Telegram)": "telegram",
    "Webhook (existing configuration)": "webhook",
}


class PhoneAlertsDialog:
    def __init__(self, parent, on_saved=None):
        self.window = tk.Toplevel(parent)
        self.window.title("Phone alerts")
        self.window.geometry("720x640")
        self.window.minsize(660, 580)
        self.window.transient(parent)
        self.events = queue.Queue()
        self.poll_timer = None
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        self.busy = False
        self.on_saved = on_saved
        self.controls = []
        self.variables = {
            "enabled": tk.BooleanVar(value=False),
            "macos_notification": tk.BooleanVar(value=True),
            "provider": tk.StringVar(value="Mac only"),
            "ntfy_topic": tk.StringVar(),
            "ntfy_server": tk.StringVar(value="https://ntfy.sh"),
            "telegram_bot_token": tk.StringVar(),
            "telegram_chat_id": tk.StringVar(),
            "advance_notice_minutes": tk.StringVar(value="10"),
        }
        panel = ttk.Frame(self.window, padding=20)
        panel.pack(fill="both", expand=True)
        ttk.Label(panel, text="Phone alerts", style="Heading.TLabel").pack(anchor="w")
        ttk.Label(
            panel,
            text=(
                "Get a notification before a scheduled run and when a browser needs your help. "
                "Prefer Telegram on iPhone if ntfy banners are unreliable."
            ),
            wraplength=660,
        ).pack(anchor="w", pady=(6, 14))
        for key, label in (("enabled", "Enable notifications"), ("macos_notification", "Show Mac banners too")):
            control = ttk.Checkbutton(panel, text=label, variable=self.variables[key])
            control.pack(anchor="w", pady=3)
            self.controls.append(control)
        form = ttk.Frame(panel)
        form.pack(fill="x", pady=(10, 0))
        self._field(form, 0, "Phone service", "provider", choices=tuple(SERVICES))
        self.topic_entry = self._field(form, 1, "ntfy topic or link", "ntfy_topic", secret=True)
        self._field(form, 2, "ntfy server", "ntfy_server")
        self.token_entry = self._field(form, 3, "Telegram bot token", "telegram_bot_token", secret=True)
        self._field(form, 4, "Telegram chat id", "telegram_chat_id")
        self._field(form, 5, "Advance notice (minutes)", "advance_notice_minutes")
        form.columnconfigure(1, weight=1)
        topic_row = ttk.Frame(panel)
        topic_row.pack(fill="x", pady=(6, 12))
        self.show_topic = tk.BooleanVar(value=False)
        show = ttk.Checkbutton(
            topic_row, text="Show secrets", variable=self.show_topic,
            command=self._toggle_secrets,
        )
        show.pack(side="left")
        self.controls.append(show)
        self._button(topic_row, "Generate ntfy topic", self.generate_topic).pack(side="left", padx=10)
        self._button(topic_row, "Copy ntfy phone link", self.copy_link).pack(side="left")
        ttk.Label(
            panel,
            text=(
                "Telegram: message @BotFather to create a bot, copy its token here, open a chat with the bot, "
                "then put your numeric chat id in the field (ask @userinfobot, or use getUpdates after messaging the bot). "
                "Settings are saved privately on this Mac. Use 0 minutes to turn off the advance reminder."
            ),
            wraplength=660,
        ).pack(anchor="w", pady=(0, 12))
        self.status = ttk.Label(panel, text="Loading settings…", wraplength=660)
        self.status.pack(anchor="w", fill="x")
        buttons = ttk.Frame(panel)
        buttons.pack(side="bottom", fill="x", pady=(12, 0))
        self._button(buttons, "Save", lambda: self.save(False)).pack(side="left")
        self.test_button = self._button(buttons, "Save & send phone test", lambda: self.save(True))
        self.test_button.pack(side="left", padx=8)
        ttk.Button(buttons, text="Close", command=self.close).pack(side="right")
        self.variables["enabled"].trace_add("write", lambda *_: self.update_controls())
        self.variables["provider"].trace_add("write", lambda *_: self.update_controls())
        self.begin("get")
        self.poll_timer = self.window.after(80, self.poll)

    def _field(self, parent, row, label, key, *, choices=(), secret=False):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=(0, 12), pady=5)
        if choices:
            control = ttk.Combobox(parent, textvariable=self.variables[key], values=choices, state="readonly")
        else:
            control = ttk.Entry(parent, textvariable=self.variables[key], show="•" if secret else "")
        control.grid(row=row, column=1, sticky="ew", pady=5)
        self.controls.append(control)
        return control

    def _button(self, parent, text, command):
        control = ttk.Button(parent, text=text, command=command)
        self.controls.append(control)
        return control

    def _toggle_secrets(self):
        show = "" if self.show_topic.get() else "•"
        self.topic_entry.configure(show=show)
        self.token_entry.configure(show=show)

    def update_controls(self):
        for control in self.controls:
            if isinstance(control, ttk.Combobox):
                control.configure(state="disabled" if self.busy else "readonly")
            else:
                control.state(["disabled"] if self.busy else ["!disabled"])
        provider = self.variables["provider"].get()
        phone_ready = self.variables["enabled"].get() and provider in {"Phone (ntfy)", "Phone (Telegram)"}
        self.test_button.state(["!disabled"] if phone_ready and not self.busy else ["disabled"])

    def generate_topic(self):
        self.variables["ntfy_topic"].set("ip-location-" + secrets.token_hex(12))
        self.variables["provider"].set("Phone (ntfy)")
        self.status.configure(text="Topic generated. Save the settings and copy the phone link to subscribe in ntfy.")

    def copy_link(self):
        topic = self.variables["ntfy_topic"].get().strip()
        server = self.variables["ntfy_server"].get().strip().rstrip("/")
        if not topic:
            self.status.configure(text="Enter a topic or generate one first.")
            return
        try:
            parsed = urlsplit(topic)
            destination = parsed if "://" in topic else urlsplit(server)
            if (destination.scheme not in {"http", "https"} or not destination.netloc
                    or destination.username or destination.password or destination.query or destination.fragment):
                raise ValueError("Invalid subscription address")
            destination.port
        except ValueError:
            self.status.configure(text="Enter a valid topic and server, or a full ntfy subscription link, then save the settings.")
            return
        link = topic if parsed.scheme in {"http", "https"} and parsed.netloc else f"{server}/{quote(topic, safe='_-')}"
        self.window.clipboard_clear()
        self.window.clipboard_append(link)
        self.status.configure(text="Phone link copied. Subscribe to it in the ntfy app on your phone.")

    def save(self, send_test):
        if self.busy:
            return
        values = {key: variable.get() for key, variable in self.variables.items()}
        values["provider"] = SERVICES[values["provider"]]
        self.begin("save", values, send_test)

    def begin(self, action, values=None, send_test=False):
        self.busy = True
        self.update_controls()
        self.status.configure(text="Loading settings…" if action == "get" else "Saving settings…")

        def work():
            try:
                response = alert_settings_request(action, values)
                self.events.put(("loaded" if action == "get" else "saved", response["settings"]))
                if send_test:
                    try:
                        tested = alert_settings_request("test", response["settings"])
                        self.events.put(("tested", tested["message"]))
                    except RuntimeError as error:
                        self.events.put(("error", f"Settings saved, but the phone test failed. {error}"))
            except RuntimeError as error:
                self.events.put(("error", str(error)))
            finally:
                self.events.put(("idle", None))

        threading.Thread(target=work, name="phone-alert-settings", daemon=True).start()

    def poll(self):
        if not self.window.winfo_exists():
            return
        while True:
            try:
                event, value = self.events.get_nowait()
            except queue.Empty:
                break
            if event in {"loaded", "saved"}:
                for key, variable in self.variables.items():
                    if key == "provider":
                        variable.set(next(label for label, name in SERVICES.items() if name == value[key]))
                    else:
                        variable.set(value[key])
                self.status.configure(text="Settings loaded. Save when ready; saving does not send a notification." if event == "loaded"
                                      else "Phone alert settings saved for future tasks.")
                if event == "saved" and self.on_saved:
                    self.on_saved()
            elif event in {"tested", "error"}:
                self.status.configure(text=value)
            elif event == "idle":
                self.busy = False
                self.update_controls()
        self.poll_timer = self.window.after(80, self.poll)

    def close(self):
        if self.poll_timer is not None:
            self.window.after_cancel(self.poll_timer)
            self.poll_timer = None
        self.window.destroy()
