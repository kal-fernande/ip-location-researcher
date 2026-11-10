\
from __future__ import annotations
import csv
import io
import json
import os
import tempfile
from pathlib import Path
from threading import Lock
from .links import observation_sources

FIELDNAMES = [
    "observation_id",
    "day",
    "question_id",
    "collector",
    "provider",
    "language",
    "vpn_location",
    "timestamp",
    "retry",  # Cumulative manual-retry observation times, one ISO-8601 value per line.
    "exit_ip",
    "detected_country_code",
    "vpn_verified",
    "model_requested",
    "model_observed",
    "prompt",
    "response",
    "status",
    "error",
    "latency_ms",
    "attempts",  # browser: 1 = no retry
    "manual_intervention",  # True if a person cleared a CAPTCHA for this ask
    "human_wait_ms",  # wall time spent in CAPTCHA human-wait loops (empty if none)
    "human_timeout_seconds",  # configured CAPTCHA wait budget when a wait ran
    "source_urls",  # One HTTP(S) URL per line; answer text stays unchanged
]

_lock = Lock()

def observation_key(row: dict) -> tuple:
    return (
        str(row.get("day")),
        row.get("question_id"),
        row.get("collector"),
        row.get("provider"),
        row.get("language"),
        row.get("vpn_location"),
    )

def existing_keys(csv_path: Path) -> set[tuple]:
    """Every observation key present in the CSV (any status)."""
    if not csv_path.exists():
        return set()
    with csv_path.open("r", newline="", encoding="utf-8") as f:
        return {observation_key(r) for r in csv.DictReader(f)}


def read_rows(csv_path: Path) -> list[dict]:
    if not csv_path.exists():
        return []
    with csv_path.open("r", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def ok_keys(csv_path: Path) -> set[tuple]:
    return {observation_key(r) for r in read_rows(csv_path) if r.get("status") == "ok"}


def attempt_counts(csv_path: Path) -> dict[tuple, int]:
    """How many CSV rows exist per observation key (each row is one attempt)."""
    counts: dict[tuple, int] = {}
    for row in read_rows(csv_path):
        key = observation_key(row)
        counts[key] = counts.get(key, 0) + 1
    return counts


def _merge_retry_snapshot(history: list[str], snapshot: list[str]) -> list[str]:
    """Extend cumulative cells without duplicating their shared prefix."""
    if snapshot[:len(history)] == history:
        return list(snapshot)
    if history[:len(snapshot)] == snapshot:
        return list(history)
    return [*history, *[stamp for stamp in snapshot if stamp not in history]]


def _saved_retry_observations(rows: list[dict], runs_dir: Path | None) -> dict:
    """Recover only retries explicitly identified in their matching run JSON."""
    if runs_dir is None or not runs_dir.is_dir():
        return {}
    by_id = {row["observation_id"]: row for row in rows if row.get("observation_id")}
    recovered = {}
    for path in sorted(runs_dir.glob("*.json")):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            if record.get("trigger") != "manual_recovery":
                continue
            for location in record.get("locations", []):
                for collector in ("browser", "api", "manual"):
                    for observation in location.get(collector, []):
                        if not isinstance(observation.get("retry") or "", str):
                            continue
                        row = by_id.get(observation.get("observation_id"))
                        if row is None:
                            continue
                        key = (str(record.get("day")), observation.get("question_id"), collector,
                               observation.get("provider"), observation.get("language"), location.get("location"))
                        if (key == observation_key(row) and row.get("timestamp")
                                and row["timestamp"] == observation.get("timestamp")
                                and row.get("response") == observation.get("response")):
                            recovered[row["observation_id"]] = observation
        except (OSError, ValueError, TypeError, AttributeError):
            continue
    return recovered


def _retry_data(rows: list[dict], runs_dir: Path | None = None) -> tuple[dict, list[str]]:
    saved = _saved_retry_observations(rows, runs_dir)
    histories = {}
    cells = []
    for row in rows:
        key = observation_key(row)
        observation = saved.get(row.get("observation_id"))
        value = row.get("retry") or (observation.get("retry") if observation else "") or ""
        if value:
            snapshot = [stamp.strip() for stamp in value.splitlines() if stamp.strip()]
            histories[key] = _merge_retry_snapshot(histories.get(key, []), snapshot)
            cells.append("\n".join(histories[key]))
        elif observation:
            # A pre-column manual retry has one actual observation time. Each
            # distinct observation counts, even when its timestamp is identical.
            histories.setdefault(key, []).append(row["timestamp"])
            cells.append("\n".join(histories[key]))
        else:
            cells.append("")
    return histories, cells


def retry_history(csv_path: Path, runs_dir: Path | None = None) -> dict[tuple, list[str]]:
    """Read prior manual-retry times for each full observation key."""
    if not csv_path.exists() or not csv_path.stat().st_size:
        return {}
    _, rows, _ = _read_csv_snapshot(csv_path)
    return _retry_data(rows, runs_dir)[0]


def upgrade_retry_history(csv_path: Path, runs_dir: Path | None = None) -> dict:
    """Add/backfill the retry column using saved evidence, without collection."""
    with _lock:
        fields, rows, original = _read_csv_snapshot(csv_path)
        _, cells = _retry_data(rows, runs_dir)
        changed = "retry" not in fields
        if "retry" not in fields:
            position = fields.index("timestamp") + 1 if "timestamp" in fields else len(fields)
            fields.insert(position, "retry")
        for row, value in zip(rows, cells):
            changed = changed or row.get("retry", "") != value
            row["retry"] = value
        if changed:
            _write_csv_atomic(csv_path, fields, rows, original)
        if changed or not excel_path_for(csv_path).is_file():
            write_excel_mirror(csv_path)
    return {"rows": len(rows), "retry_rows": sum(bool(value) for value in cells), "changed": changed}


def skip_keys(
    csv_path: Path,
    *,
    recovery_policy: str = "skip",
    max_attempts_per_cell: int = 3,
) -> set[tuple]:
    """Keys that run_day should not collect again.

    skip: any existing row blocks a rerun (historical default).
    retry: only ok rows, or keys that already hit max_attempts_per_cell.
    """
    policy = (recovery_policy or "skip").lower()
    if policy != "retry":
        return existing_keys(csv_path)
    filled = ok_keys(csv_path)
    for key, count in attempt_counts(csv_path).items():
        if count >= max_attempts_per_cell:
            filled.add(key)
    return filled


def _read_csv_snapshot(csv_path: Path):
    original = csv_path.read_bytes()
    with io.StringIO(original.decode("utf-8"), newline="") as stream:
        reader = csv.DictReader(stream, strict=True)
        fields = reader.fieldnames or []
        if len(set(fields)) != len(fields):
            raise ValueError(f"CSV has duplicate column names: {csv_path}")
        rows = list(reader)
        if any(None in row for row in rows):
            raise ValueError(f"CSV has rows wider than its header: {csv_path}")
        if any(any(value is None for value in row.values()) for row in rows):
            raise ValueError(f"CSV has rows shorter than its header: {csv_path}")
    return fields, rows, original


def _write_csv_atomic(csv_path: Path, fields: list[str], rows: list[dict], original: bytes):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", newline="", encoding="utf-8", dir=csv_path.parent,
                                         prefix=csv_path.name + ".", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
        if csv_path.read_bytes() != original:
            raise RuntimeError("CSV changed during the update; retry when collection is idle")
        os.replace(temporary, csv_path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _upgrade_header(csv_path: Path) -> list[str]:
    """Atomically extend old CSVs, preserving answer text and extra user columns."""
    if not csv_path.exists() or not csv_path.stat().st_size:
        return FIELDNAMES
    fields, rows, original = _read_csv_snapshot(csv_path)
    missing = [field for field in FIELDNAMES if field not in fields]
    if missing:
        fields = [*FIELDNAMES, *[field for field in fields if field not in FIELDNAMES]]
        _write_csv_atomic(csv_path, fields, rows, original)
    return fields


def excel_path_for(csv_path: Path) -> Path:
    return csv_path.with_suffix(".xlsx")


def write_excel_mirror(csv_path: Path) -> Path:
    """Rewrite csv_path's sibling .xlsx so Windows Excel opens Unicode cleanly."""
    import pandas as pd

    xlsx_path = excel_path_for(csv_path)
    if not csv_path.is_file():
        return xlsx_path
    df = pd.read_csv(csv_path, dtype=str, keep_default_na=False)
    df.to_excel(xlsx_path, index=False, engine="openpyxl")
    return xlsx_path


def append_result(csv_path: Path, row: dict) -> None:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with _lock:
        fields = _upgrade_header(csv_path)
        clean = {k: row.get(k, "") for k in fields}
        clean["source_urls"] = "\n".join(source["url"] for source in observation_sources(row))
        exists = csv_path.exists() and csv_path.stat().st_size > 0
        with csv_path.open("a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            if not exists:
                w.writeheader()
            w.writerow(clean)
        write_excel_mirror(csv_path)


def refresh_source_urls(csv_path: Path, runs_dir: Path | None = None) -> dict:
    """Recover only URLs in saved data; leave all other cells and JSON untouched."""
    with _lock:
        fields, rows, original = _read_csv_snapshot(csv_path)
        fields = [*fields, *[field for field in FIELDNAMES if field not in fields]]
        by_id = {row["observation_id"]: row for row in rows if row.get("observation_id")}
        for row in rows:
            row["source_urls"] = "\n".join(source["url"] for source in observation_sources(row))
        if runs_dir is not None and runs_dir.exists():
            for path in sorted(runs_dir.glob("*.json")):
                record = json.loads(path.read_text(encoding="utf-8"))
                for location in record.get("locations", []):
                    for kind in ("api", "browser", "manual"):
                        for entry in location.get(kind, []):
                            row = by_id.get(entry.get("observation_id"))
                            if row is not None and row.get("response") == entry.get("response"):
                                sources = observation_sources({
                                    "sources": observation_sources(entry),
                                    "source_urls": row["source_urls"],
                                })
                                row["source_urls"] = "\n".join(source["url"] for source in sources)
        _write_csv_atomic(csv_path, fields, rows, original)
        write_excel_mirror(csv_path)
    return {"rows": len(rows), "rows_with_links": sum(bool(row["source_urls"]) for row in rows),
            "links": sum(len(row["source_urls"].splitlines()) for row in rows)}
