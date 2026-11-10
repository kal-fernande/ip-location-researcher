"""Active study folders under results/studies/<id>/ (like study-smoke sandboxes)."""
from __future__ import annotations

import fcntl
import json
import re
import shutil
import tempfile
import unicodedata
from copy import deepcopy
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .config import ROOT, load_config, resolve_path

ACTIVE_NAME = "ACTIVE"
STOPPED_NAME = "STOPPED"
_STUDY_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class NoActiveStudy(RuntimeError):
    """Raised when results/studies/ACTIVE is missing or invalid."""


class ActiveStudyExists(RuntimeError):
    """Raised when start_study would replace an existing ACTIVE study without force."""


@contextmanager
def _study_state_lock():
    """Serialize activation and stopping, including collector cleanup."""
    path = ROOT / "private" / "scheduler" / "study-state.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def is_study_stopped(cfg: dict) -> bool:
    root = cfg.get("results", {}).get("study_root")
    return bool(root and (resolve_path(root) / STOPPED_NAME).exists())


def studies_dir(cfg: dict | None = None) -> Path:
    cfg = cfg or load_config()
    return resolve_path(cfg["results"].get("studies_dir", "results/studies"))


def active_pointer_path(cfg: dict | None = None) -> Path:
    return studies_dir(cfg) / ACTIVE_NAME


def validate_study_id(study_id: str) -> str:
    study_id = (study_id or "").strip()
    if not _STUDY_ID_RE.match(study_id):
        raise ValueError(
            "study id must start with a letter or digit and contain only "
            "letters, digits, '.', '_' or '-' (max 128 chars)"
        )
    return study_id


def study_root(study_id: str, cfg: dict | None = None) -> Path:
    return studies_dir(cfg) / validate_study_id(study_id)


def read_active_study_id(cfg: dict | None = None) -> str:
    path = active_pointer_path(cfg)
    if not path.is_file():
        raise NoActiveStudy(
            f"No active study ({path} missing). "
            "Create one with: python main.py start-study"
        )
    contents = path.read_text(encoding="utf-8").strip()
    if not contents:
        raise NoActiveStudy(f"Active study pointer is empty: {path}")
    study_id = contents.splitlines()[0].strip()
    return validate_study_id(study_id)


def set_active_study_id(study_id: str, cfg: dict | None = None) -> None:
    study_id = validate_study_id(study_id)
    pointer = active_pointer_path(cfg)
    pointer.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=pointer.parent,
            prefix=".ACTIVE-", delete=False,
        ) as stream:
            temporary = Path(stream.name)
            stream.write(study_id + "\n")
        temporary.replace(pointer)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def apply_study(cfg: dict, study_id: str) -> dict:
    """Return a deep copy of cfg with result/schedule paths under the study folder."""
    study_id = validate_study_id(study_id)
    root = study_root(study_id, cfg)
    out = deepcopy(cfg)
    results = out.setdefault("results", {})
    results["study_id"] = study_id
    results["study_root"] = str(root)
    results["schedule_path"] = str(root / "schedule.json")
    results["csv_path"] = str(root / "results.csv")
    results["runs_dir"] = str(root / "runs")
    results["failure_screenshot_dir"] = str(root / "failures")
    results["index_path"] = str(root / "README.md")
    results["details_path"] = str(root / "results.md")
    results["analysis_dir"] = str(root / "analysis")
    # Setup checks are diagnostics shared across studies.
    results.setdefault("checks_dir", "results/checks")
    results.setdefault("studies_dir", results.get("studies_dir", "results/studies"))
    return out


def apply_active_study(cfg: dict | None = None) -> dict:
    cfg = cfg or load_config()
    return apply_study(cfg, read_active_study_id(cfg))


def schedule_path_for_cfg(cfg: dict) -> Path:
    path = cfg.get("results", {}).get("schedule_path")
    if path:
        return resolve_path(path)
    raise NoActiveStudy(
        "Config has no results.schedule_path; call apply_active_study() or pass schedule_path="
    )


def start_study(
    study_id: str | None = None,
    *,
    start_date: str | None = None,
    seed: int | None = None,
    agenda_path: str | Path | None = None,
    cfg: dict | None = None,
    name: str = "",
    notes: str = "",
    force: bool = False,
) -> tuple[str, Path]:
    """Create a separate study and activate it only after its files are ready."""
    with _study_state_lock():
        return _start_study(
            study_id, start_date=start_date, seed=seed, agenda_path=agenda_path,
            cfg=cfg, name=name, notes=notes, force=force,
        )


def _start_study(
    study_id: str | None,
    *,
    start_date: str | None,
    seed: int | None,
    agenda_path: str | Path | None,
    cfg: dict | None,
    name: str,
    notes: str,
    force: bool,
) -> tuple[str, Path]:
    from .runner import create_schedule  # local import avoids cycle at module load

    cfg = cfg or load_config()
    try:
        current = read_active_study_id(cfg)
    except NoActiveStudy:
        current = None
    if current and not force and study_root(current, cfg).exists():
        raise ActiveStudyExists(
            f"Study {current!r} is already active "
            f"({study_root(current, cfg)}). "
            "Run: python main.py study-status. "
            "To start a different study anyway: python main.py start-study --force"
        )

    tz = ZoneInfo(cfg["experiment"]["timezone"])
    now = datetime.now(tz)
    name = name.strip()
    generated_id = study_id is None
    if study_id is None:
        if name:
            ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
            slug = re.sub(r"[^A-Za-z0-9]+", "-", ascii_name).strip("-").lower()[:80].rstrip("-") or "study"
            study_id = f"{now:%Y-%m-%d_%H%M%S_%f}_{slug}"
        else:
            study_id = f"{now:%Y-%m-%d_%H%M%S}_browser"
    study_id = validate_study_id(study_id)
    base_id = study_id
    suffix = 1
    while True:
        root = study_root(study_id, cfg)
        root.parent.mkdir(parents=True, exist_ok=True)
        try:
            root.mkdir()
            break
        except FileExistsError:
            if not generated_id:
                raise FileExistsError(f"Study folder already exists: {root}") from None
            suffix += 1
            study_id = f"{base_id}_{suffix}"

    try:
        (root / "runs").mkdir()
        (root / "failures").mkdir()
        manifest = {
            "study_id": study_id,
            "name": name,
            "created_at": now.isoformat(),
            "timezone": cfg["experiment"]["timezone"],
            "mode": "browser",
            "notes": notes,
        }
        (root / "manifest.json").write_text(
            json.dumps(manifest, indent=2) + "\n", encoding="utf-8",
        )
        study_cfg = apply_study(cfg, study_id)
        create_schedule(
            start_date,
            seed,
            cfg=study_cfg,
            schedule_path=root / "schedule.json",
            agenda_path=agenda_path,
        )
        set_active_study_id(study_id, cfg)
    except BaseException:
        # root.mkdir() above guarantees this call owns only this new folder.
        shutil.rmtree(root)
        raise
    return study_id, root


def stop_study(*, study_id: str | None = None, cfg: dict | None = None) -> dict | None:
    """Deactivate a study, preserve its files, and stop a running collector."""
    from .run_lock import collection_running
    from .vpn import vpn_off

    cfg = cfg or load_config()
    with _study_state_lock():
        try:
            current = read_active_study_id(cfg)
        except NoActiveStudy:
            return None
        if study_id is not None and current != study_id:
            raise ValueError("The active study changed. Refresh the window before stopping it.")
        root = study_root(current, cfg)
        root.mkdir(parents=True, exist_ok=True)
        # The marker also blocks a scheduler that read ACTIVE before this call.
        (root / STOPPED_NAME).write_text(
            datetime.now(ZoneInfo(cfg["experiment"]["timezone"])).isoformat() + "\n",
            encoding="utf-8",
        )
        active_pointer_path(cfg).unlink()
        result = {"study_id": current, "root": str(root), "collector_alive": False, "error": ""}
        if collection_running():
            try:
                cleanup = vpn_off(cfg=cfg)
                result["collector_alive"] = cleanup["collector_alive"] or collection_running()
            except Exception as error:
                result["collector_alive"] = collection_running()
                result["error"] = f"Collector cleanup needs attention: {type(error).__name__}: {error}"
        return result


def study_status(cfg: dict | None = None) -> dict:
    """Return active id, root, and next pending run_at (if any)."""
    cfg = apply_active_study(cfg)
    study_id = cfg["results"]["study_id"]
    root = Path(cfg["results"]["study_root"])
    schedule_path = Path(cfg["results"]["schedule_path"])
    schedule = []
    if schedule_path.is_file():
        schedule = json.loads(schedule_path.read_text(encoding="utf-8"))
    next_run = None
    for event in sorted(schedule, key=lambda e: e.get("run_at") or ""):
        modes = set(event.get("completed_modes") or [])
        if "browser" not in modes and "both" not in modes:
            next_run = event
            break
    return {
        "study_id": study_id,
        "root": str(root),
        "schedule_path": str(schedule_path),
        "csv_path": cfg["results"]["csv_path"],
        "next_event": next_run,
    }
