"""Private phone-alert preferences shared by desktop, CLI, and scheduler."""
from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from urllib.parse import unquote, urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parent.parent
SETTINGS_FILE = Path("private/phone-alerts.json")
SETTING_KEYS = frozenset({
    "enabled", "macos_notification", "provider", "ntfy_topic", "ntfy_server",
    "telegram_bot_token", "telegram_chat_id", "advance_notice_minutes",
})
_DEFAULTS = {
    "enabled": False,
    "macos_notification": True,
    "provider": "none",
    "ntfy_topic": "",
    "ntfy_server": "https://ntfy.sh",
    "telegram_bot_token": "",
    "telegram_chat_id": "",
    "advance_notice_minutes": 10,
}
_TOPIC = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_TELEGRAM_TOKEN = re.compile(r"^\d{5,}:[A-Za-z0-9_-]{20,}$")
_TELEGRAM_CHAT = re.compile(r"^-?\d{5,20}$")
_PROVIDERS = frozenset({"none", "ntfy", "telegram", "webhook"})


def _server(value: str) -> str:
    try:
        parsed = urlsplit(value)
        valid = (
            parsed.scheme.lower() in ("http", "https")
            and parsed.netloc and parsed.hostname
            and parsed.username is None and parsed.password is None
            and not parsed.query and not parsed.fragment
            and not any(character.isspace() for character in value)
        )
        # Accessing port also validates malformed/out-of-range port numbers.
        parsed.port
    except ValueError:
        valid = False
    if not valid:
        raise ValueError("Enter an HTTP or HTTPS server address without a login, query, or fragment.")
    return urlunsplit((parsed.scheme.lower(), parsed.netloc, parsed.path.rstrip("/"), "", ""))


def normalize_settings(values: dict) -> dict:
    """Validate GUI fields, including an optional pasted ntfy subscription URL."""
    if not isinstance(values, dict) or set(values) - SETTING_KEYS:
        raise ValueError("The phone-alert settings contain an unsupported field.")
    settings = {**_DEFAULTS, **values}
    for key in ("enabled", "macos_notification"):
        if not isinstance(settings[key], bool):
            raise ValueError("Alert switches must be on or off.")
    provider = settings["provider"]
    if not isinstance(provider, str) or provider.strip().lower() not in _PROVIDERS:
        raise ValueError("Choose Mac only, ntfy, Telegram, or the configured webhook.")
    settings["provider"] = provider.strip().lower()
    minutes = settings["advance_notice_minutes"]
    if isinstance(minutes, str) and re.fullmatch(r"[0-9]+", minutes.strip()):
        minutes = int(minutes.strip())
    if isinstance(minutes, bool) or not isinstance(minutes, int) or not 0 <= minutes <= 1440:
        raise ValueError("Advance notice must be a whole number from 0 to 1440 minutes.")
    settings["advance_notice_minutes"] = minutes
    if not isinstance(settings["ntfy_topic"], str) or not isinstance(settings["ntfy_server"], str):
        raise ValueError("Enter a topic and server address as text.")
    if not isinstance(settings["telegram_bot_token"], str) or not isinstance(settings["telegram_chat_id"], str):
        raise ValueError("Enter the Telegram bot token and chat id as text.")
    topic = settings["ntfy_topic"].strip()
    server = settings["ntfy_server"].strip() or _DEFAULTS["ntfy_server"]
    if "://" in topic:
        # A full phone subscription link supplies both server and topic.
        link = _server(topic)
        parsed = urlsplit(link)
        prefix, _, topic = parsed.path.rpartition("/")
        topic = unquote(topic)
        server = urlunsplit((parsed.scheme, parsed.netloc, prefix, "", ""))
        if not topic:
            raise ValueError("The phone link must end with a topic name.")
    settings["ntfy_server"] = _server(server)
    if topic and not _TOPIC.fullmatch(topic):
        raise ValueError("Use a topic of 1 to 64 letters, numbers, underscores, or hyphens.")
    token = settings["telegram_bot_token"].strip()
    chat_id = settings["telegram_chat_id"].strip()
    if token and not _TELEGRAM_TOKEN.fullmatch(token):
        raise ValueError("Enter a Telegram bot token from BotFather (numbers, colon, then the secret).")
    if chat_id and not _TELEGRAM_CHAT.fullmatch(chat_id):
        raise ValueError("Enter the numeric Telegram chat id (digits only; groups may start with -).")
    if settings["enabled"] and settings["provider"] == "ntfy" and not topic:
        raise ValueError("Enter the ntfy topic or subscription link before enabling phone alerts.")
    if settings["enabled"] and settings["provider"] == "telegram" and (not token or not chat_id):
        raise ValueError("Enter the Telegram bot token and chat id before enabling phone alerts.")
    settings["ntfy_topic"] = topic
    settings["telegram_bot_token"] = token
    settings["telegram_chat_id"] = chat_id
    return settings


def effective_alerts(cfg: dict, *, root: Path | None = None) -> dict:
    """Precedence: shared config, environment secrets, then private preferences."""
    alerts = dict(cfg.get("experiment", {}).get("alerts") or {})
    env_topic = (os.environ.get("NTFY_TOPIC") or "").strip()
    if env_topic:
        alerts["ntfy_topic"] = env_topic
    env_token = (os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
    if env_token:
        alerts["telegram_bot_token"] = env_token
    env_chat = (os.environ.get("TELEGRAM_CHAT_ID") or "").strip()
    if env_chat:
        alerts["telegram_chat_id"] = env_chat
    path = (ROOT if root is None else Path(root)) / SETTINGS_FILE
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return alerts
    except (OSError, ValueError):
        raise RuntimeError("Private phone-alert settings could not be read. Open Phone alerts and save valid settings.") from None
    try:
        if not isinstance(raw, dict) or set(raw) - SETTING_KEYS:
            raise ValueError("Invalid settings fields")
        known = {key: value for key, value in alerts.items() if key in SETTING_KEYS}
        validated = normalize_settings({**known, **raw})
    except (TypeError, ValueError):
        raise RuntimeError("Private phone-alert settings are invalid. Open Phone alerts and save valid settings.") from None
    # Keep other configured channels, including an existing webhook URL.
    alerts.update(validated)
    return alerts


def get_settings() -> dict:
    from .config import load_config

    alerts = effective_alerts(load_config(), root=ROOT)
    return normalize_settings({key: value for key, value in alerts.items() if key in SETTING_KEYS})


def save_settings(values: dict) -> dict:
    """Atomically save only allowed preferences outside version control."""
    settings = normalize_settings(values)
    path = ROOT / SETTINGS_FILE
    temporary = None
    try:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", prefix=".phone-alerts-", suffix=".json",
            dir=path.parent, delete=False,
        ) as handle:
            temporary = Path(handle.name)
            os.fchmod(handle.fileno(), 0o600)
            json.dump(settings, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
    except OSError:
        raise RuntimeError("Phone-alert settings could not be saved. Check access to the project folder.") from None
    finally:
        if temporary is not None:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
    return settings


def test_phone_alert(values: dict) -> str:
    """Explicit test action; callers must not send tests while loading settings."""
    settings = normalize_settings(values)
    if not settings["enabled"] or settings["provider"] not in {"ntfy", "telegram"}:
        raise ValueError("Enable alerts and choose ntfy or Telegram to send a phone test.")
    from .alerts import _send_ntfy, _send_telegram

    try:
        if settings["provider"] == "ntfy":
            _send_ntfy(settings, "IP-location study: test", "Phone alerts are connected. No study data was collected.")
        else:
            _send_telegram(settings, "IP-location study: test", "Phone alerts are connected. No study data was collected.")
    except Exception as error:
        raise RuntimeError(
            f"The phone test could not be delivered ({type(error).__name__}). "
            "Check the phone service settings, then try again."
        ) from None
    return "Test message sent to the phone-alert service. Check your phone."
