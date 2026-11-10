import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from experiment import checks
from experiment.browser_collectors import BrowserResult
from experiment.details import write_details, _observation_rows, _source_links
from experiment.storage import append_result
from experiment.vpn import VpnState


class ReportTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.cfg = copy.deepcopy(checks.load_config())
        for key, name in (("csv_path", "results.csv"), ("runs_dir", "runs"),
                          ("checks_dir", "checks"), ("index_path", "README.md"),
                          ("details_path", "results.md"), ("failure_screenshot_dir", "failures")):
            self.cfg["results"][key] = str(self.root / name)
        row = {"day": 1, "question_id": "Q01", "timestamp": "2026-10-19T16:16:00-07:00",
               "provider": "chatgpt", "collector": "browser", "language": "en",
               "vpn_location": "US", "exit_ip": "192.0.2.1", "status": "ok"}
        append_result(self.root / "results.csv", {**row, "response": "STUDY_ONLY"})
        append_result(self.root / "pilot-historical-data/results.csv", {**row, "response": "PILOT_ONLY"})

    def test_real_check_writer_updates_check_report_and_preserves_study(self):
        write_details(self.cfg)
        before = (self.root / "results.md").read_text()
        def vpn(cfg, location, loc_cfg):
            return VpnState(location, location, "192.0.2.1", location, location, True, {})
        with patch.object(checks, "load_config", return_value=self.cfg), patch.object(checks, "connect_vpn", side_effect=vpn), patch.object(checks, "disconnect_vpn"), patch.object(checks, "collect_browser", return_value=BrowserResult("CHECK_ONLY", None, 1)):
            first, ok = checks.run_checks(api=False, browser=True, file_suffix="_first")
            second, ok_again = checks.run_checks(api=False, browser=True, file_suffix="_second")
        self.assertTrue(ok and ok_again)
        self.assertEqual((self.root / "results.md").read_text(), before)
        report = (self.root / "checks/results.md").read_text()
        index = (self.root / "README.md").read_text()
        for path in (first, second):
            self.assertIn(f'id="{path.stem}"', report)
            self.assertIn(f"checks/results.md#{path.stem}", index)
            self.assertIn(f"[{path.name}]({path.name})", report)
        self.assertIn("CHECK_ONLY", report)
        self.assertNotIn("STUDY_ONLY", report)
        self.assertNotIn("PILOT_ONLY", report)
        self.assertFalse((self.root / "pilot").exists())
        self.assertFalse((self.root / "pilot-historical-data/results.md").exists())
        self.assertIn("PILOT_ONLY", (self.root / "pilot-historical-data/results.csv").read_text())
        self.assertNotIn("PILOT_ONLY", before)

    def test_empty_study_does_not_show_checks_or_pilot(self):
        # Keep a valid CSV header, with no study observations.
        csv_path = self.root / "results.csv"
        csv_path.write_text(csv_path.read_text().splitlines()[0] + "\n")
        write_details(self.cfg)
        report = (self.root / "results.md").read_text()
        self.assertIn("No study runs have been collected yet.", report)
        self.assertNotIn("PILOT_ONLY", report)

    def test_report_displays_captured_sources_and_handles_parenthesized_urls(self):
        observation = {"provider": "gemini", "response": "Answer", "status": "ok", "sources": [
            {"title": "Wikipedia", "url": "https://example.org/Convention_(law)"},
        ]}
        rows = _observation_rows({"browser": [observation]})
        self.assertIn("[Wikipedia](<https://example.org/Convention_(law)>)", rows[0])
        self.assertIn("Sources:", rows[0])
        self.assertEqual(_source_links({"source_urls": "https://example.org/one\nhttps://example.org/two"}),
                         "[https://example.org/one](<https://example.org/one>), [https://example.org/two](<https://example.org/two>)")


if __name__ == "__main__":
    unittest.main()
