import csv
import json
from pathlib import Path
import tempfile
import unittest

from experiment.api_collectors import ApiResult
from experiment.browser_collectors import BrowserResult
from experiment.links import normalize_sources, observation_sources, sources_from_text
from experiment.storage import FIELDNAMES, append_result, read_rows, refresh_source_urls


class LinkCaptureTests(unittest.TestCase):
    def test_markdown_plain_unicode_and_parenthesized_urls(self):
        text = ('[Wikipedia](https://en.wikipedia.org/wiki/Convention_(law)) '
                '[מקור](https://example.org/עברית?a=1&b=2) '
                'See https://example.org/report. (https://example.org/other).')
        self.assertEqual(sources_from_text(text), [
            {"title": "Wikipedia", "url": "https://en.wikipedia.org/wiki/Convention_(law)"},
            {"title": "מקור", "url": "https://example.org/עברית?a=1&b=2"},
            {"title": "", "url": "https://example.org/report"},
            {"title": "", "url": "https://example.org/other"},
        ])

    def test_fragment_actions_and_duplicates_are_excluded_without_guessing_urls(self):
        self.assertEqual(normalize_sources([
            {"title": " Source ", "url": "/report"},
            {"title": "Duplicate", "url": "https://example.org/report"},
            {"url": "#citation-1"}, {"url": "javascript:void(0)"}, {"url": "mailto:a@example.org"},
        ], "https://example.org/answer"), [{"title": "Source", "url": "https://example.org/report"}])
        self.assertEqual(sources_from_text("Wikipedia [1] is cited, but there is no stored URL."), [])

    def test_browser_google_metadata_and_api_text_share_source_format(self):
        source = {"title": "Wiki", "url": "https://example.org/wiki"}
        browser = BrowserResult("Answer", None, 1, metadata={"sources": [source]})
        api = ApiResult("[Wiki](https://example.org/wiki)", None, 1)
        self.assertEqual(browser.sources, [source])
        self.assertEqual(api.sources, [source])
        self.assertEqual(observation_sources({"source_urls": "https://example.org/wiki\nhttps://example.org/second"}), [
            {"title": "", "url": "https://example.org/wiki"}, {"title": "", "url": "https://example.org/second"},
        ])


class SourceCSVTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.csv = self.root / "results.csv"

    def write_legacy(self, rows, extra_fields=()):
        fields = [field for field in FIELDNAMES if field != "source_urls"] + list(extra_fields)
        with self.csv.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)

    def test_csv_keeps_full_answer_and_multiple_urls_in_one_quoted_field(self):
        response = 'Answer, "quoted"\nתשובה בעברית'
        append_result(self.csv, {"observation_id": "new", "response": response, "sources": [
            {"title": "Wikipedia", "url": "https://en.wikipedia.org/wiki/Example"},
            {"title": "מקור", "url": "https://example.org/עברית?a=1&b=2"},
        ]})
        row = read_rows(self.csv)[0]
        self.assertEqual(row["response"], response)
        self.assertEqual(row["source_urls"], 'https://en.wikipedia.org/wiki/Example\nhttps://example.org/עברית?a=1&b=2')
        self.assertEqual(list(row), FIELDNAMES)

    def test_appending_to_old_csv_upgrades_header_and_preserves_custom_columns(self):
        response = 'Legacy, "quotes"\nעברית'
        self.write_legacy([{"observation_id": "old", "response": response, "notes": "keep me"}], ["notes"])
        append_result(self.csv, {"observation_id": "new", "response": "https://example.org/new"})
        rows = read_rows(self.csv)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["response"], response)
        self.assertEqual(rows[0]["notes"], "keep me")
        self.assertEqual(rows[0]["source_urls"], "")
        self.assertEqual(rows[1]["source_urls"], "https://example.org/new")
        self.assertFalse(list(self.root.glob("*.tmp")))

    def test_empty_existing_file_receives_header(self):
        self.csv.touch()
        append_result(self.csv, {"response": "Answer"})
        self.assertEqual(read_rows(self.csv)[0]["response"], "Answer")

    def test_malformed_legacy_csv_is_preserved(self):
        self.csv.write_text("observation_id,response\nold,Answer,unexpected\n")
        before = self.csv.read_bytes()
        with self.assertRaisesRegex(ValueError, "wider than"):
            append_result(self.csv, {"response": "new"})
        self.assertEqual(self.csv.read_bytes(), before)

    def test_refresh_uses_matching_saved_observation_and_preserves_original_cells(self):
        self.write_legacy([
            {"observation_id": "google", "response": "Overview answer"},
            {"observation_id": "markdown", "response": "[Source](https://example.org/text)"},
            {"observation_id": "missing", "response": "Wikipedia [1], no URL saved"},
        ])
        before = read_rows(self.csv)
        runs = self.root / "runs"
        runs.mkdir()
        record = {"locations": [{"browser": [
            {"observation_id": "google", "response": "Overview answer", "metadata": {
                "sources": [{"title": "Wiki", "url": "https://example.org/overview"}]}},
            {"observation_id": "missing", "response": "Different answer", "sources": [
                {"url": "https://example.org/must-not-join"}]},
        ]}]}
        path = runs / "old.json"
        path.write_text(json.dumps(record))
        json_before = path.read_bytes()
        summary = refresh_source_urls(self.csv, runs)
        after = read_rows(self.csv)
        self.assertEqual(summary, {"rows": 3, "rows_with_links": 2, "links": 2})
        for old, new in zip(before, after):
            self.assertEqual(old, {key: new[key] for key in old})
        self.assertEqual(after[0]["source_urls"], "https://example.org/overview")
        self.assertEqual(after[1]["source_urls"], "https://example.org/text")
        self.assertEqual(after[2]["source_urls"], "")
        self.assertEqual(path.read_bytes(), json_before)
        self.assertEqual(refresh_source_urls(self.csv, runs), summary)

    def test_refresh_failure_does_not_rewrite_csv(self):
        self.write_legacy([{"response": "Old answer"}])
        before = self.csv.read_bytes()
        runs = self.root / "runs"
        runs.mkdir()
        (runs / "broken.json").write_text("{broken")
        with self.assertRaises(json.JSONDecodeError):
            refresh_source_urls(self.csv, runs)
        self.assertEqual(self.csv.read_bytes(), before)
