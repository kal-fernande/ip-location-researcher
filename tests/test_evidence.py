import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from experiment.evidence import FILES, capture_evidence, validate_evidence, validate_run


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        session = Mock()
        session.send.return_value = {"data": "MIME-Version: 1.0\nCaptured page"}
        self.page = SimpleNamespace(url="https://example.org/answer", context=Mock(),
            content=lambda: '<html><body>Original page</body></html>',
            screenshot=lambda **args: Path(args["path"]).write_bytes(b'PNG fixture'))
        self.page.context.new_cdp_session.return_value = session
        self.element = Mock()
        self.element.evaluate.return_value = '<div>Exact answer <a href="/source">Source</a></div>'
        self.observation = {"observation_id": "id", "provider": "chatgpt", "prompt": "Question?",
                            "response": "Exact answer", "sources": [{"title": "Source", "url": "https://example.org/source"}],
                            "status": "ok", "attempts": 1}

    def capture(self, **kwargs):
        return capture_evidence(self.page, self.element, provider=self.observation["provider"],
            prompt=self.observation["prompt"], response=self.observation["response"],
            sources=kwargs.get("sources", self.observation["sources"]), status="ok",
            source_capture={"complete": True, "controls_found": 0, "controls_read": 0},
            browser_cfg={"evidence_dir": str(self.root / "evidence")},
            screenshot_dir=self.root / "failures", observation_id="id")

    def test_bundle_has_common_files_and_validates_against_export(self):
        evidence = self.capture()
        self.assertEqual(evidence["status"], "ok")
        self.assertEqual(set(p.name for p in Path(evidence["directory"]).iterdir()), {*FILES, "manifest.json"})
        self.assertEqual(validate_evidence(evidence, self.observation), [])
        path = self.root / "run.json"
        self.observation["metadata"] = {"evidence": evidence}
        path.write_text(json.dumps({"locations": [{"location": "US", "browser": [self.observation]}]}))
        self.assertTrue(validate_run(path)["ok"])

    def test_changed_archive_is_detected(self):
        evidence = self.capture()
        Path(evidence["archive"]).write_text("Changed archive")
        self.assertIn("Missing or changed file: page.mhtml", validate_evidence(evidence, self.observation))

    def test_exported_answer_and_sources_cannot_drift_from_saved_observation(self):
        evidence = self.capture()
        changed = {**self.observation, "response": "Different answer", "sources": []}
        errors = validate_evidence(evidence, changed)
        self.assertIn("Exported response differs from captured observation", errors)
        self.assertIn("Exported source URLs differ from captured observation", errors)

    def test_source_without_browser_evidence_is_rejected(self):
        evidence = self.capture(sources=[{"url": "https://example.org/invented"}])
        self.assertEqual(evidence["status"], "error")
        self.assertTrue(any("no captured" in error for error in evidence["errors"]))

    def test_citation_controls_that_arrive_after_extraction_are_detected(self):
        self.element.evaluate.return_value += '<span data-d-component="popover-trigger" aria-haspopup="dialog" aria-controls="late">Late citation</span>'
        evidence = self.capture()
        self.assertIn("Captured answer markup has a different citation-control count", evidence["errors"])

    def test_review_copy_preserves_unicode_and_escapes_answer_markup(self):
        self.observation["response"] = 'תשובה <script>alert("changed")</script> & exact text'
        evidence = self.capture()
        review = Path(evidence["review_html"]).read_text()
        self.assertIn("תשובה", review)
        self.assertIn("&lt;script&gt;", review)
        self.assertNotIn('<script>alert', review)
        self.assertEqual(validate_evidence(evidence, self.observation), [])

    def test_existing_observation_bundle_is_never_overwritten(self):
        evidence = self.capture()
        manifest = Path(evidence["manifest"]).read_bytes()
        repeated = self.capture()
        self.assertEqual(repeated["status"], "error")
        self.assertEqual(Path(evidence["manifest"]).read_bytes(), manifest)

    def test_csv_and_excel_are_compared_by_observation_id(self):
        from experiment.storage import append_result
        evidence = self.capture()
        self.observation["metadata"] = {"evidence": evidence}
        path = self.root / "run.json"
        path.write_text(json.dumps({"locations": [{"location": "US", "browser": [self.observation]}]}))
        csv_path = self.root / "results.csv"
        append_result(csv_path, self.observation)
        self.assertTrue(validate_run(path, csv_path)["ok"])
        csv_path.write_text(csv_path.read_text().replace("Exact answer", "Changed answer"))
        report = validate_run(path, csv_path)
        self.assertFalse(report["ok"])
        self.assertIn("CSV: answer fields differ from run JSON", report["results"][0]["errors"])


if __name__ == "__main__":
    unittest.main()
