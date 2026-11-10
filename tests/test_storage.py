"""CSV append, Excel mirror, and header upgrade."""
from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from experiment.storage import FIELDNAMES, append_result, excel_path_for


class StorageTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.csv_path = Path(temporary.name) / "results.csv"

    def test_append_writes_new_human_columns(self):
        append_result(self.csv_path, {
            "observation_id": "a",
            "day": 1,
            "status": "captcha",
            "human_wait_ms": 12000,
            "human_timeout_seconds": 300,
        })
        with self.csv_path.open(newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        self.assertEqual(list(rows[0].keys()), FIELDNAMES)
        self.assertEqual(rows[0]["human_wait_ms"], "12000")
        self.assertEqual(rows[0]["human_timeout_seconds"], "300")

    def test_upgrade_adds_missing_columns(self):
        old_fields = [f for f in FIELDNAMES if f not in (
            "human_wait_ms", "human_timeout_seconds",
        )]
        with self.csv_path.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=old_fields)
            w.writeheader()
            w.writerow({k: ("1" if k == "day" else "") for k in old_fields})
        append_result(self.csv_path, {
            "observation_id": "b",
            "day": 2,
            "human_wait_ms": 5000,
            "human_timeout_seconds": 900,
        })
        with self.csv_path.open(newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        self.assertEqual(list(rows[0].keys()), FIELDNAMES)
        self.assertEqual(rows[0]["human_wait_ms"], "")
        self.assertEqual(rows[1]["human_wait_ms"], "5000")
        self.assertEqual(rows[1]["human_timeout_seconds"], "900")

    def test_append_writes_matching_excel_mirror(self):
        append_result(self.csv_path, {
            "observation_id": "he1",
            "day": 1,
            "language": "he",
            "prompt": "שלום",
            "response": "עולם",
            "status": "ok",
        })
        append_result(self.csv_path, {
            "observation_id": "en1",
            "day": 1,
            "language": "en",
            "prompt": "hello",
            "status": "ok",
        })
        xlsx = excel_path_for(self.csv_path)
        self.assertTrue(xlsx.is_file())
        csv_df = pd.read_csv(self.csv_path, dtype=str, keep_default_na=False)
        xlsx_df = pd.read_excel(xlsx, dtype=str, engine="openpyxl").fillna("")
        self.assertTrue(csv_df.equals(xlsx_df[csv_df.columns]))


if __name__ == "__main__":
    unittest.main()
