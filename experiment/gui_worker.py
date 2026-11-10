"""CLI entry point that gives GUI setup checks the scheduler's collection lock."""
from __future__ import annotations

import sys

from .config import ROOT
from .run_lock import CollectionBusy, collection_lock


LOCKED_COMMANDS = frozenset({
    "check", "check-browser", "check-browser-prompt", "collect-manual",
    "start-study", "schedule", "refresh-links", "analyze",
})


def _invoke_cli(args: list[str]) -> None:
    import main as cli

    previous = sys.argv
    try:
        sys.argv = [str(ROOT / "main.py"), *args]
        cli.main()
    finally:
        sys.argv = previous


def main(argv: list[str] | None = None) -> None:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) == 2 and args[0] == "--gui-alerts":
        import json
        from .alert_settings import get_settings, save_settings, test_phone_alert

        try:
            if args[1] == "get":
                payload = {"ok": True, "settings": get_settings()}
            elif args[1] in {"save", "test"}:
                raw = sys.stdin.read(65537)
                if len(raw) > 65536:
                    raise ValueError("Phone settings input is too large.")
                try:
                    values = json.loads(raw)
                except ValueError:
                    raise ValueError("Phone settings input could not be read.") from None
                if args[1] == "save":
                    payload = {"ok": True, "settings": save_settings(values)}
                else:
                    payload = {"ok": True, "message": test_phone_alert(values)}
            else:
                raise ValueError("Unknown phone settings action.")
        except (ValueError, RuntimeError) as error:
            payload = {"ok": False, "error": str(error)}
        except Exception:
            payload = {"ok": False, "error": "Could not access phone settings. Check that the project folder is writable."}
        print(json.dumps(payload), flush=True)
        return
    if args == ["--gui-dashboard"]:
        import json
        from .gui_support import read_dashboard

        print(json.dumps(read_dashboard(), default=str), flush=True)
        return
    try:
        if args and args[0] in LOCKED_COMMANDS:
            with collection_lock(blocking=False):
                _invoke_cli(args)
        else:
            _invoke_cli(args)
    except CollectionBusy as error:
        print(error, file=sys.stderr, flush=True)
        raise SystemExit(1) from error


if __name__ == "__main__":
    main()
