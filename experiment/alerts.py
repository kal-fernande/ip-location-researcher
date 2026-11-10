"""Notify the study operator when human action is needed (e.g. CAPTCHA)."""
from __future__ import annotations
import json
import subprocess
import sys
import urllib.error
import urllib.request

from .config import ROOT, load_config
from .alert_settings import effective_alerts


def _alerts_cfg(cfg: dict | None = None) -> dict:
    cfg = cfg or load_config()
    return effective_alerts(cfg, root=ROOT)


def send_alert(
    title: str,
    body: str,
    *,
    cfg: dict | None = None,
    local_line: str | None = None,
    webhook_provider: str = "study",
) -> None:
    """Print locally (with beep) and, if configured, ping Mac and/or a phone channel."""
    print(f"\a\n{local_line or f'{title}: {body}'}", flush=True)
    alerts = _alerts_cfg(cfg)
    if not alerts.get("enabled"):
        return
    # Mac Notification Center (default on) and remote channels can both fire.
    if alerts.get("macos_notification", True):
        try:
            _send_macos(title, body)
        except Exception as e:
            print(f"ALERTS: macOS notification failed ({type(e).__name__}: {e})", flush=True)
    provider_name = str(alerts.get("provider") or "ntfy").lower()
    try:
        if provider_name == "ntfy":
            if (alerts.get("ntfy_topic") or "").strip():
                _send_ntfy(alerts, title, body)
        elif provider_name == "telegram":
            _send_telegram(alerts, title, body)
        elif provider_name == "webhook":
            _send_webhook(alerts, title, body, webhook_provider)
        elif provider_name not in ("", "none", "off"):
            print(f"ALERTS: unknown provider {provider_name!r}", flush=True)
    except Exception as e:
        print(f"ALERTS: failed to send ({type(e).__name__}: {e})", flush=True)


def notify_human_action(provider: str, message: str, cfg: dict | None = None) -> None:
    """Print locally and, if configured, ping ntfy or a webhook."""
    send_alert(
        f"IP-location study: {provider}",
        message,
        cfg=cfg,
        local_line=f"HUMAN ACTION NEEDED for {provider}: {message}",
        webhook_provider=provider,
    )


def notify_upcoming_run(
    *,
    day: int,
    run_at: str,
    minutes_ahead: int,
    cfg: dict | None = None,
) -> None:
    """Warn that a scheduled collection (and possible CAPTCHA) is soon."""
    body = (
        f"Day {day} is scheduled at {run_at} "
        f"(about {minutes_ahead} minutes). "
        "Stay at the Mac; a CAPTCHA may need you."
    )
    send_alert(
        "IP-location study: upcoming run",
        body,
        cfg=cfg,
        local_line=f"UPCOMING STUDY RUN: {body}",
    )


def _apple_script_string(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"', '\\"')


def _send_macos(title: str, body: str) -> None:
    """Show a Notification Center banner on this Mac."""
    if sys.platform != "darwin":
        return
    script = (
        f'display notification "{_apple_script_string(body)}" '
        f'with title "{_apple_script_string(title)}"'
    )
    subprocess.run(
        ["osascript", "-e", script],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )


def _send_ntfy(alerts: dict, title: str, body: str) -> None:
    topic = (alerts.get("ntfy_topic") or "").strip()
    if not topic:
        return
    server = (alerts.get("ntfy_server") or "https://ntfy.sh").rstrip("/")
    url = f"{server}/{topic}"
    request = urllib.request.Request(
        url,
        data=body.encode("utf-8"),
        method="POST",
        headers={
            "Title": title,
            # urgent/max: best chance of iOS banners; high was often silent there.
            "Priority": "urgent",
        },
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        response.read()


def _send_telegram(alerts: dict, title: str, body: str) -> None:
    token = (alerts.get("telegram_bot_token") or "").strip()
    chat_id = (alerts.get("telegram_chat_id") or "").strip()
    if not token or not chat_id:
        print("ALERTS: telegram_bot_token or telegram_chat_id is empty; skip send", flush=True)
        return
    payload = json.dumps({
        "chat_id": chat_id,
        "text": f"{title}\n\n{body}",
        "disable_web_page_preview": True,
    }).encode("utf-8")
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=payload,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        response.read()


def _send_webhook(alerts: dict, title: str, body: str, provider: str) -> None:
    url = (alerts.get("webhook_url") or "").strip()
    if not url:
        print("ALERTS: webhook_url is empty; skip send", flush=True)
        return
    payload = json.dumps({
        "title": title,
        "message": body,
        "provider": provider,
    }).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=payload,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        response.read()
