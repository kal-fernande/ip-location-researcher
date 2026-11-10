from __future__ import annotations

import atexit
import os
import signal
import subprocess
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Iterator

import requests

from .config import ROOT, load_config

ABORT_PATH = ROOT / "private" / "scheduler" / "abort.request"
LOCK_PATH = ROOT / "private" / "scheduler" / "collection.lock"

_owned_cfg: dict | None = None
_handlers_installed = False
_abort_flag = False
_timeout_flag = False
_prior_handlers: dict[int, Any] = {}


class VpnSessionAborted(RuntimeError):
    """Collection stopped because vpn-off (or an abort signal) was requested."""


class VpnSessionTimeout(RuntimeError):
    """Collection stopped because vpn.max_session_seconds elapsed."""


@dataclass
class VpnState:
    requested_location: str
    expected_country_code: str
    exit_ip: str | None
    detected_country_code: str | None
    detected_country_name: str | None
    verified: bool
    raw: dict[str, Any] | None
    error: str | None = None


def _render_command(parts: list[str], **values) -> list[str]:
    return [str(part).format(**values) for part in parts]


def request_abort(*, timeout: bool = False) -> None:
    """Ask the in-process collector to stop after the current cell when possible."""
    global _abort_flag, _timeout_flag
    _abort_flag = True
    if timeout:
        _timeout_flag = True
    ABORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    ABORT_PATH.write_text(f"{os.getpid()}\n", encoding="utf-8")


def clear_abort() -> None:
    global _abort_flag, _timeout_flag
    _abort_flag = False
    _timeout_flag = False
    try:
        ABORT_PATH.unlink(missing_ok=True)
    except TypeError:
        # Python < 3.8 style (should not hit on 3.15); keep defensive.
        if ABORT_PATH.exists():
            ABORT_PATH.unlink()


def abort_requested() -> bool:
    return _abort_flag or ABORT_PATH.exists()


def raise_if_aborted() -> None:
    if not abort_requested():
        return
    if _timeout_flag:
        raise VpnSessionTimeout(
            "VPN session wall-clock (vpn.max_session_seconds) elapsed. "
            "Study schedule and CSV are unchanged; incomplete cells can resume later."
        )
    raise VpnSessionAborted(
        "VPN/collection abort requested (vpn-off). "
        "Study schedule and CSV are unchanged; incomplete cells can resume later."
    )


def _cleanup_disconnect() -> None:
    """Best-effort disconnect using the cfg that last owned the tunnel."""
    cfg = _owned_cfg
    if cfg is None:
        try:
            cfg = load_config()
        except Exception:
            return
    disconnect_vpn(cfg)


def _on_signal(signum: int, frame) -> None:
    request_abort()
    _cleanup_disconnect()
    raise SystemExit(128 + signum)


def _on_usr1(signum: int, frame) -> None:
    """Soft stop from vpn-off: disconnect now, abort collection, keep process exiting via raise_if_aborted."""
    request_abort()
    _cleanup_disconnect()


def install_cleanup_handlers(cfg: dict) -> None:
    """Ensure SIGINT/SIGTERM/atexit disconnect the tunnel this process opened."""
    global _handlers_installed, _owned_cfg
    _owned_cfg = cfg
    if _handlers_installed:
        return
    _handlers_installed = True
    atexit.register(_cleanup_disconnect)
    for sig, handler in (
        (signal.SIGINT, _on_signal),
        (signal.SIGTERM, _on_signal),
        (signal.SIGUSR1, _on_usr1),
    ):
        try:
            _prior_handlers[sig] = signal.getsignal(sig)
            signal.signal(sig, handler)
        except (ValueError, OSError):
            # Signals unavailable in some threads/platforms.
            pass


def _watchdog(cfg: dict, max_seconds: int, stop: threading.Event) -> None:
    if stop.wait(max_seconds):
        return
    print(
        f"VPN session exceeded {max_seconds}s; disconnecting and aborting collection",
        flush=True,
    )
    request_abort(timeout=True)
    disconnect_vpn(cfg)
    # Allow soft abort to finish the current cell; then force-exit so a hung
    # Playwright wait cannot leave the tunnel owned forever.
    if not stop.wait(30):
        try:
            os.kill(os.getpid(), signal.SIGTERM)
        except OSError:
            pass


@contextmanager
def vpn_session(cfg: dict) -> Iterator[None]:
    """Own the VPN for one collection run.

    Installs cleanup handlers, clears prior abort requests, starts an optional
    wall-clock watchdog (`vpn.max_session_seconds`), and always disconnects on exit.
    """
    clear_abort()
    install_cleanup_handlers(cfg)
    stop = threading.Event()
    max_seconds = int((cfg.get("vpn") or {}).get("max_session_seconds") or 0)
    watchdog: threading.Thread | None = None
    if max_seconds > 0:
        watchdog = threading.Thread(
            target=_watchdog,
            args=(cfg, max_seconds, stop),
            name="vpn-session-watchdog",
            daemon=True,
        )
        watchdog.start()
    try:
        yield
    finally:
        stop.set()
        disconnect_vpn(cfg)
        clear_abort()


def connect_vpn(cfg: dict, location_name: str, location_cfg: dict) -> VpnState:
    vpn_cfg = cfg["vpn"]
    expected = location_cfg["expected_country_code"].upper()
    raise_if_aborted()

    if not vpn_cfg.get("enabled", True):
        return verify_location(cfg, location_name, expected)

    install_cleanup_handlers(cfg)
    # Drop any previous tunnel first, so switching location never leaves two running.
    disconnect_vpn(cfg)
    raise_if_aborted()
    cmd = _render_command(
        vpn_cfg["connect_command"],
        target=location_cfg.get("vpn_target") or location_cfg["nord_target"],  # nord_target: old name
        location=location_name,
        root=ROOT,
    )
    subprocess.run(cmd, check=True)
    global _owned_cfg
    _owned_cfg = cfg
    time.sleep(vpn_cfg.get("settle_seconds", 8))
    # DNS can lag a few seconds behind a fresh tunnel, so retry before giving up.
    attempts = vpn_cfg.get("verify_attempts", 3)
    for attempt in range(attempts):
        raise_if_aborted()
        state = verify_location(cfg, location_name, expected)
        if state.verified or attempt == attempts - 1:
            return state
        time.sleep(vpn_cfg.get("verify_retry_seconds", 5))


def disconnect_vpn(cfg: dict) -> None:
    if not cfg["vpn"].get("enabled", True):
        return
    try:
        subprocess.run(
            _render_command(cfg["vpn"]["disconnect_command"], root=ROOT),
            check=False,
        )
    except Exception:
        pass


def verify_location(cfg: dict, requested: str, expected_country_code: str) -> VpnState:
    vpn_cfg = cfg["vpn"]
    urls = vpn_cfg.get("verify_urls") or [vpn_cfg["verify_url"]]
    errors = []
    last_state = None

    for url in urls:
        state = _verify_location_url(cfg, requested, expected_country_code, url)
        if state.verified:
            return state
        if state.error:
            errors.append(f"{url}: {state.error}")
        else:
            last_state = state

    if last_state is not None:
        return last_state

    return VpnState(
        requested_location=requested,
        expected_country_code=expected_country_code,
        exit_ip=None,
        detected_country_code=None,
        detected_country_name=None,
        verified=False,
        raw=None,
        error="; ".join(errors),
    )


def _verify_location_url(
    cfg: dict,
    requested: str,
    expected_country_code: str,
    url: str,
) -> VpnState:
    vpn_cfg = cfg["vpn"]
    try:
        r = requests.get(
            url,
            timeout=vpn_cfg.get("verify_timeout_seconds", 15),
            headers={"User-Agent": "ip-location-experiment/0.1"},
        )
        r.raise_for_status()
        data = r.json()
        country_code = (
            data.get("country_code")
            or data.get("countryCode")
            or data.get("country")
            or ""
        ).upper()
        country_name = data.get("country_name") or data.get("country_name_en") or data.get("country")
        ip = data.get("ip") or data.get("query")
        raw = dict(data)
        raw["_verify_url"] = url
        return VpnState(
            requested_location=requested,
            expected_country_code=expected_country_code,
            exit_ip=ip,
            detected_country_code=country_code,
            detected_country_name=country_name,
            verified=(country_code == expected_country_code.upper()),
            raw=raw,
        )
    except Exception as e:
        return VpnState(
            requested_location=requested,
            expected_country_code=expected_country_code,
            exit_ip=None,
            detected_country_code=None,
            detected_country_name=None,
            verified=False,
            raw=None,
            error=str(e),
        )


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _read_collector_pid() -> int | None:
    try:
        text = LOCK_PATH.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None
    except OSError:
        return None
    if not text:
        return None
    try:
        return int(text.splitlines()[0].strip())
    except ValueError:
        return None


def vpn_off(
    *,
    cfg: dict | None = None,
    force_after_seconds: float = 15.0,
) -> dict[str, Any]:
    """Disconnect Mullvad immediately and soft-stop a running collector.

    Does not clear `ACTIVE`, edit the schedule, or delete CSV rows.
    Incomplete study cells remain available for a later `run-due`.
    """
    cfg = cfg if cfg is not None else load_config()
    # Drop the tunnel first so personal traffic is not stuck behind the study exit.
    disconnect_vpn(cfg)

    pid = _read_collector_pid()
    signaled: list[str] = []
    killed = False
    if pid is not None and _pid_alive(pid) and pid != os.getpid():
        # Abort file + soft signals: collector stops without clearing ACTIVE/schedule/CSV.
        request_abort()
        for sig, label in (
            (signal.SIGUSR1, "SIGUSR1"),
            (signal.SIGINT, "SIGINT"),
        ):
            try:
                os.kill(pid, sig)
                signaled.append(label)
            except OSError:
                break
            deadline = time.time() + min(5.0, force_after_seconds)
            while time.time() < deadline and _pid_alive(pid):
                time.sleep(0.2)
            if not _pid_alive(pid):
                break
        if _pid_alive(pid):
            remaining = max(0.0, force_after_seconds - 5.0)
            deadline = time.time() + remaining
            while time.time() < deadline and _pid_alive(pid):
                time.sleep(0.2)
            if _pid_alive(pid):
                try:
                    os.kill(pid, signal.SIGTERM)
                    signaled.append("SIGTERM")
                    killed = True
                except OSError:
                    pass
                time.sleep(0.5)
                if _pid_alive(pid):
                    try:
                        os.kill(pid, signal.SIGKILL)
                        signaled.append("SIGKILL")
                        killed = True
                    except OSError:
                        pass

    alive = bool(pid and _pid_alive(pid))
    # Do not leave abort.request around when nothing is collecting — that would
    # poison the next scheduled run.
    if not alive:
        clear_abort()

    return {
        "disconnected": True,
        "collector_pid": pid,
        "collector_alive": alive,
        "signaled": signaled,
        "force_killed": killed,
        "abort_path": str(ABORT_PATH),
    }
