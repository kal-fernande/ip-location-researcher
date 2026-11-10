import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from experiment import checks
from experiment.api_collectors import ApiResult
from experiment.browser_collectors import BrowserResult
from experiment.vpn import VpnState


class CheckPromptTests(unittest.TestCase):
    def setUp(self):
        self.cfg = copy.deepcopy(checks.load_config())
        self.questions = [{"day": 2, "question_id": "Q02", "en": "Other", "he": "אחר"},
                          {"day": 1, "question_id": "Q01", "en": "English question", "he": "שאלה בעברית"}]

    def test_loads_day_one_by_day_and_picks_up_edits(self):
        with patch.object(checks, "load_questions", return_value=self.questions):
            prompts = checks.load_check_prompts(self.cfg)
            self.assertEqual([p["prompt_id"] for p in prompts], ["country", "emergency", "Q01_en", "Q01_he"])
            self.assertEqual(prompts[-1]["prompt"], "שאלה בעברית")
            self.questions[1]["en"] = "Changed question"
            self.assertEqual(checks.load_check_prompts(self.cfg)[2]["prompt"], "Changed question")

    def test_invalid_questions_fail_before_vpn_switch(self):
        for questions in ([], [{"day": 1, "question_id": "Q01", "en": "English only"}]):
            with self.subTest(questions=questions), patch.object(checks, "load_config", return_value=self.cfg), patch.object(checks, "load_questions", return_value=questions), patch.object(checks, "connect_vpn") as connect:
                with self.assertRaises(ValueError):
                    checks.run_checks(api=False, browser=True)
                connect.assert_not_called()

    def test_google_only_check_does_not_mutate_study_config(self):
        selected = checks._select_browser_provider(self.cfg, "google_search")
        self.assertEqual(list(selected["browser"]["providers"]), ["google_search"])
        self.assertIn("chatgpt", self.cfg["browser"]["providers"])
        for provider in ("missing", "claude"):
            with self.subTest(provider=provider), patch.object(checks, "load_config", return_value=self.cfg), patch.object(checks, "connect_vpn") as connect:
                with self.assertRaises(ValueError):
                    checks.run_browser_checks(provider=provider)
                connect.assert_not_called()

    def test_google_only_prompt_check_records_both_languages_at_both_locations(self):
        with tempfile.TemporaryDirectory() as temp:
            self.cfg["results"]["checks_dir"] = temp
            def vpn(cfg, location, loc_cfg):
                return VpnState(location, location, "192.0.2.1", location, location, True, {})
            with patch.object(checks, "load_config", return_value=self.cfg), patch.object(checks, "load_questions", return_value=self.questions), patch.object(checks, "connect_vpn", side_effect=vpn), patch.object(checks, "disconnect_vpn"), patch.object(checks, "add_entry"), patch.object(checks, "collect_browser", return_value=BrowserResult("Overview", None, 1)) as collect:
                path, ok = checks.run_checks(api=False, browser=True, browser_provider="google_search",
                                             command="check-browser-prompt --provider google_search")
            self.assertTrue(ok)
            self.assertEqual(collect.call_count, 8)
            self.assertTrue(all(call.kwargs["provider"] == "google_search" for call in collect.call_args_list))
            record = json.loads(Path(path).read_text())
            self.assertEqual(record["command"], "check-browser-prompt --provider google_search")
            for location in record["locations"]:
                study = [item for item in location["browser"] if item.get("question_id") == "Q01"]
                self.assertEqual({item["language"] for item in study}, {"en", "he"})

    def test_study_only_checks_all_three_sources_without_location_diagnostics(self):
        with tempfile.TemporaryDirectory() as temp:
            self.cfg["results"]["checks_dir"] = temp
            def vpn(cfg, location, loc_cfg):
                return VpnState(location, location, "192.0.2.1", location, location, True, {})
            with patch.object(checks, "load_config", return_value=self.cfg), patch.object(checks, "load_questions", return_value=self.questions), patch.object(checks, "connect_vpn", side_effect=vpn), patch.object(checks, "disconnect_vpn"), patch.object(checks, "add_entry"), patch.object(checks, "collect_browser", return_value=BrowserResult("Answer", None, 1)) as collect:
                path, ok = checks.run_checks(api=False, browser=True, study_only=True,
                                             command="check-browser-prompt --study-only")
            self.assertTrue(ok)
            self.assertEqual(collect.call_count, 12)
            self.assertEqual({call.kwargs["provider"] for call in collect.call_args_list}, {"chatgpt", "gemini", "google_search"})
            self.assertEqual({call.kwargs["prompt"] for call in collect.call_args_list}, {"English question", "שאלה בעברית"})
            record = json.loads(Path(path).read_text())
            self.assertTrue(all(item.get("question_id") == "Q01" for location in record["locations"] for item in location["browser"]))

    def test_api_and_browser_receive_translations_and_save_metadata(self):
        with tempfile.TemporaryDirectory() as temp:
            self.cfg["results"]["checks_dir"] = temp
            def vpn(cfg, location, loc_cfg):
                return VpnState(location, location, "192.0.2.1", location, location, True, {})
            with patch.object(checks, "load_config", return_value=self.cfg), patch.object(checks, "load_questions", return_value=self.questions), patch.object(checks, "connect_vpn", side_effect=vpn), patch.object(checks, "disconnect_vpn"), patch.object(checks, "add_entry"), patch.object(checks, "collect_browser", return_value=BrowserResult("Browser answer", None, 1)) as browser, patch.dict(checks.API_ADAPTERS, {"anthropic": lambda prompt, cfg: ApiResult(prompt, "test", 1)}):
                path, ok = checks.run_checks(api=True, browser=True)
            self.assertTrue(ok)
            record = json.loads(Path(path).read_text())
            self.assertEqual(browser.call_count, 24)
            for location in record["locations"]:
                for collector in ("api", "browser"):
                    study = [p for p in location[collector] if p.get("question_id") == "Q01"]
                    self.assertEqual({p["language"] for p in study}, {"en", "he"})
                    for item in study:
                        self.assertEqual(item["prompt"], self.questions[1][item["language"]])


if __name__ == "__main__":
    unittest.main()
