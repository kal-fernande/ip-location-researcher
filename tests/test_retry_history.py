"""Cumulative retry dates and legacy upgrades using isolated study exports."""
from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from experiment import storage


class RetryHistoryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.csv = self.root / "results.csv"
        self.runs = self.root / "runs"
        self.runs.mkdir()
        self.t1 = "2026-11-09T12:15:01.123456-08:00"
        self.t2 = "2026-11-09T13:20:02.234567-08:00"
        self.t3 = "2026-11-10T08:30:03.345678-08:00"

    def row(self, observation_id, *, timestamp=None, retry="", **changes):
        row = {
            "observation_id": observation_id, "day": "1", "question_id": "Q01",
            "collector": "browser", "provider": "chatgpt", "language": "he", "vpn_location": "US",
            "timestamp": timestamp or self.t1, "status": "captcha", "retry": retry,
            "prompt": "שאלה, עם פסיק\nSecond line", "response": "תגובה, עם פסיק\nSecond line",
        }
        row.update(changes)
        return row

    def write_rows(self, rows, *, legacy=False, extra_fields=()):
        fields = [field for field in storage.FIELDNAMES if not (legacy and field == "retry")]
        if not legacy and "retry" not in fields:
            fields.insert(fields.index("timestamp") + 1, "retry")
        fields.extend(extra_fields)
        with self.csv.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
        return fields

    def read_rows(self):
        with self.csv.open(encoding="utf-8", newline="") as handle:
            return list(csv.DictReader(handle))

    def saved_run(self, filename, rows, *, manual=True):
        record = {"day": int(rows[0]["day"]), "mode": "browser", "locations": []}
        if manual:
            record.update(trigger="manual_recovery", command=f"retry-day --day {record['day']}", manual_recovery={"missing_before": len(rows)})
        else:
            record["command"] = f"run-day --day {record['day']} --mode browser --force"
        locations = {}
        for row in rows:
            location = locations.setdefault(row["vpn_location"], {"location": row["vpn_location"], "browser": [], "api": [], "manual": []})
            entry = {key: row[key] for key in ("observation_id", "question_id", "provider", "language", "timestamp", "response")}
            if row.get("retry"):
                entry["retry"] = row["retry"]
            location[row["collector"]].append(entry)
        record["locations"] = list(locations.values())
        path = self.runs / filename
        path.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
        return path

    def snapshot(self):
        return {str(path.relative_to(self.root)): (path.read_bytes(), path.stat().st_mtime_ns)
                for path in self.root.rglob("*") if path.is_file()}

    def test_cumulative_cells_merge_without_repeating_the_prefix(self):
        rows = [
            self.row("original", timestamp="2026-11-09T10:00:00-08:00"),
            self.row("retry-1", timestamp=self.t1, retry=self.t1),
            self.row("retry-2", timestamp=self.t2, retry=f"{self.t1}\n{self.t2}"),
            self.row("retry-3", timestamp=self.t3, retry=f"{self.t1}\n{self.t2}\n{self.t3}", status="ok"),
        ]
        self.write_rows(rows)
        before = self.snapshot()
        history = storage.retry_history(self.csv)
        self.assertEqual(history[storage.observation_key(rows[0])], [self.t1, self.t2, self.t3])
        self.assertEqual(self.snapshot(), before)

    def test_identical_timestamps_for_distinct_attempts_are_preserved(self):
        rows = [self.row("retry-1", retry=self.t1), self.row("retry-2", retry=f"{self.t1}\n{self.t1}")]
        self.write_rows(rows)
        history = storage.retry_history(self.csv)
        self.assertEqual(history[storage.observation_key(rows[0])], [self.t1, self.t1])

    def test_history_is_separate_for_every_observation_key_dimension(self):
        original = self.row("base", retry=self.t1)
        variants = {
            "day": "2", "question_id": "Q02", "collector": "api", "provider": "gemini",
            "language": "en", "vpn_location": "IL",
        }
        rows = [original] + [self.row(f"other-{key}", timestamp=self.t2, retry=self.t2, **{key: value}) for key, value in variants.items()]
        self.write_rows(rows)
        history = storage.retry_history(self.csv)
        self.assertEqual(history[storage.observation_key(original)], [self.t1])
        for row in rows[1:]:
            with self.subTest(key=storage.observation_key(row)):
                self.assertEqual(history[storage.observation_key(row)], [self.t2])

    def test_original_and_force_duplicate_rows_are_not_inferred_as_retries(self):
        rows = [self.row("first", timestamp=self.t1), self.row("forced", timestamp=self.t2)]
        self.write_rows(rows, legacy=True)
        self.saved_run("ordinary.json", [rows[0]], manual=False)
        self.saved_run("forced.json", [rows[1]], manual=False)
        history = storage.retry_history(self.csv, self.runs)
        self.assertEqual(history.get(storage.observation_key(rows[0]), []), [])
        result = storage.upgrade_retry_history(self.csv, self.runs)
        self.assertEqual(result["retry_rows"], 0)
        self.assertTrue(all(row["retry"] == "" for row in self.read_rows()))

    def test_header_upgrade_preserves_all_other_values_and_custom_columns(self):
        rows = [self.row("first", reviewer_note="Keep this note, unchanged\nעברית", status="ok")]
        old_fields = self.write_rows(rows, legacy=True, extra_fields=("reviewer_note",))
        before = self.read_rows()
        result = storage.upgrade_retry_history(self.csv)
        after = self.read_rows()
        self.assertEqual(result["rows"], 1)
        self.assertTrue(result["changed"])
        self.assertEqual(result["retry_rows"], 0)
        self.assertEqual([{key: row[key] for key in old_fields} for row in after], before)
        self.assertEqual(after[0]["retry"], "")
        self.assertEqual([key for key in after[0] if key != "retry"], old_fields)
        self.assertEqual(list(after[0]).index("retry"), list(after[0]).index("timestamp") + 1)

    def test_legacy_manual_json_fills_cumulative_history_using_exact_saved_observations(self):
        original = self.row("original", timestamp="2026-11-09T10:00:00-08:00")
        first = self.row("retry-1", timestamp=self.t1)
        second = self.row("retry-2", timestamp=self.t2, status="ok")
        self.write_rows([original, first, second], legacy=True)
        self.saved_run("first.json", [first])
        self.saved_run("second.json", [second])
        before = self.read_rows()
        history = storage.retry_history(self.csv, self.runs)
        self.assertEqual(history[storage.observation_key(first)], [self.t1, self.t2])
        result = storage.upgrade_retry_history(self.csv, self.runs)
        after = self.read_rows()
        self.assertEqual(result["retry_rows"], 2)
        self.assertEqual([row["retry"] for row in after], ["", self.t1, f"{self.t1}\n{self.t2}"])
        for old, new in zip(before, after):
            self.assertEqual({key: new[key] for key in old}, old)

    def test_legacy_retries_with_the_same_timestamp_are_distinct_events(self):
        first = self.row("retry-1", timestamp=self.t1)
        second = self.row("retry-2", timestamp=self.t1)
        self.write_rows([first, second], legacy=True)
        self.saved_run("first.json", [first])
        self.saved_run("second.json", [second])
        self.assertEqual(storage.retry_history(self.csv, self.runs)[storage.observation_key(first)], [self.t1, self.t1])
        storage.upgrade_retry_history(self.csv, self.runs)
        self.assertEqual(self.read_rows()[1]["retry"], f"{self.t1}\n{self.t1}")

    def test_corrupt_or_mismatched_json_does_not_invent_retry_dates(self):
        row = self.row("target")
        self.write_rows([row], legacy=True)
        (self.runs / "corrupt.json").write_text("{not-json", encoding="utf-8")
        mismatches = {
            "response": "Different answer", "timestamp": self.t2, "provider": "gemini",
            "language": "en", "vpn_location": "IL", "question_id": "Q02", "day": "2",
        }
        for index, (key, value) in enumerate(mismatches.items()):
            self.saved_run(f"mismatch-{index}.json", [{**row, key: value}])
        self.assertEqual(storage.retry_history(self.csv, self.runs).get(storage.observation_key(row), []), [])
        storage.upgrade_retry_history(self.csv, self.runs)
        self.assertEqual(self.read_rows()[0]["retry"], "")

    def test_excel_mirror_preserves_the_same_multiline_retry_cell(self):
        first = self.row("retry-1", timestamp=self.t1)
        second = self.row("retry-2", timestamp=self.t2)
        self.write_rows([first, second], legacy=True)
        self.saved_run("first.json", [first])
        self.saved_run("second.json", [second])
        storage.upgrade_retry_history(self.csv, self.runs)
        csv_frame = pd.read_csv(self.csv, dtype=str, keep_default_na=False)
        excel_frame = pd.read_excel(storage.excel_path_for(self.csv), dtype=str, engine="openpyxl").fillna("")
        self.assertEqual(excel_frame.loc[1, "retry"], f"{self.t1}\n{self.t2}")
        self.assertTrue(csv_frame.equals(excel_frame[csv_frame.columns]))

    def test_failed_atomic_replace_preserves_the_original_csv(self):
        self.write_rows([self.row("original")], legacy=True)
        before = self.csv.read_bytes()
        with patch.object(storage.os, "replace", side_effect=PermissionError("Fixture replace denied")):
            with self.assertRaises((OSError, RuntimeError)):
                storage.upgrade_retry_history(self.csv)
        self.assertEqual(self.csv.read_bytes(), before)
        self.assertEqual(list(self.root.glob("*.tmp")), [])
        self.assertFalse(storage.excel_path_for(self.csv).exists())

    def test_short_csv_row_is_rejected_without_rewriting_the_source(self):
        self.csv.write_text(",".join(storage.FIELDNAMES) + "\nold,1\n", encoding="utf-8")
        before = self.csv.read_bytes()
        with self.assertRaises(ValueError):
            storage.retry_history(self.csv)
        with self.assertRaises(ValueError):
            storage.upgrade_retry_history(self.csv)
        self.assertEqual(self.csv.read_bytes(), before)
        self.assertFalse(storage.excel_path_for(self.csv).exists())

    def test_missing_excel_mirror_is_rebuilt_without_rewriting_current_csv(self):
        self.write_rows([self.row("retry-1", retry=self.t1)])
        before = (self.csv.read_bytes(), self.csv.stat().st_mtime_ns)
        storage.upgrade_retry_history(self.csv)
        self.assertEqual((self.csv.read_bytes(), self.csv.stat().st_mtime_ns), before)
        mirror = storage.excel_path_for(self.csv)
        self.assertTrue(mirror.is_file())
        excel_frame = pd.read_excel(mirror, dtype=str, engine="openpyxl").fillna("")
        self.assertEqual(excel_frame.loc[0, "retry"], self.t1)

    def test_repeating_the_upgrade_does_not_rewrite_csv_excel_or_json(self):
        first = self.row("retry-1", timestamp=self.t1)
        self.write_rows([first], legacy=True)
        self.saved_run("first.json", [first])
        storage.upgrade_retry_history(self.csv, self.runs)
        before = self.snapshot()
        result = storage.upgrade_retry_history(self.csv, self.runs)
        self.assertFalse(result["changed"])
        self.assertEqual(self.snapshot(), before)


if __name__ == "__main__":
    unittest.main()
