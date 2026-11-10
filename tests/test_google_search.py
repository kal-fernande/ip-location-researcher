import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from experiment import browser_collectors as browser
from experiment.google_search import build_search_url, source_links


class GoogleSearchTests(unittest.TestCase):
    def test_exact_prompt_and_fixed_locale_without_country_or_ai_mode_override(self):
        for prompt in ("Does the Convention apply? & why", "האם האמנה חלה? #1"):
            url = build_search_url("https://www.google.com/search?gl=il&udm=50&uule=old", prompt, "en-US")
            self.assertEqual(urlsplit(url).path, "/search")
            self.assertEqual(parse_qs(urlsplit(url).query), {"q": [prompt], "hl": ["en"]})

    def test_citations_keep_urls_and_unicode_without_organic_or_google_navigation(self):
        links = [
            {"title": " Wikipedia ", "url": "/url?q=https%3A%2F%2Fen.wikipedia.org%2Fwiki%2FFourth_Geneva_Convention&sa=U"},
            {"title": "duplicate", "url": "https://en.wikipedia.org/wiki/Fourth_Geneva_Convention"},
            {"title": " מקור ", "url": "https://example.org/עברית"},
            {"title": "AI Mode", "url": "/search?udm=50&q=test"},
            {"title": "Help", "url": "https://support.google.com/websearch/answer/1"},
            {"title": "unsafe", "url": "javascript:void(0)"},
        ]
        self.assertEqual(source_links(links, "https://www.google.com/search?q=test"), [
            {"title": "Wikipedia", "url": "https://en.wikipedia.org/wiki/Fourth_Geneva_Convention"},
            {"title": "מקור", "url": "https://example.org/עברית"},
        ])

    def test_google_unusual_traffic_without_captcha_widget_is_detected(self):
        self.assertTrue(browser._captcha_detected(SimpleNamespace(url="https://www.google.com/sorry/index")))
        with patch.object(browser, "_page_contains", return_value=True):
            self.assertTrue(browser._captcha_detected(SimpleNamespace(url="https://www.google.com/search?q=test")))

    def test_opaque_google_source_redirect_is_preserved_instead_of_discarded(self):
        self.assertEqual(source_links([
            {"title": "Wikipedia", "url": "/goto?url=opaque-source-token"},
            {"title": "Navigation", "url": "/search?q=next"},
        ], "https://www.google.com/search?q=test"), [
            {"title": "Wikipedia", "url": "https://www.google.com/goto?url=opaque-source-token"},
        ])

    def test_google_dispatch_preserves_retry_and_metadata(self):
        result = browser.BrowserResult("Answer", None, 1, metadata={"surface": "ai_overview", "sources": []})
        with TemporaryDirectory() as temp, patch("experiment.google_search.collect_google_search", side_effect=[
            browser.BrowserResult("", None, 1, status="timeout", error="loading"), result,
        ]) as collect:
            actual = browser.collect_browser("google_search", "Question", {"retries": 1},
                                             {"url": "https://www.google.com/search"}, "en-US", "UTC",
                                             Path(temp), "test")
        self.assertEqual(actual.status, "ok")
        self.assertEqual(actual.attempts, 2)
        self.assertEqual(actual.metadata["surface"], "ai_overview")
        self.assertIn("attempt 1: timeout", actual.error)
        self.assertEqual(collect.call_count, 2)

    def test_missing_overview_is_retried_and_success_preserves_attempt_metadata(self):
        result = browser.BrowserResult("Answer", None, 1, metadata={"surface": "ai_overview", "sources": []})
        with TemporaryDirectory() as temp, patch(
            "experiment.google_search.collect_google_search",
            side_effect=[
                browser.BrowserResult("", None, 1, status="no_ai_overview", error="no overview"),
                result,
            ],
        ) as collect:
            actual = browser.collect_browser("google_search", "Question", {"retries": 1},
                                             {"url": "https://www.google.com/search"}, "en-US", "UTC",
                                             Path(temp), "test")
        self.assertEqual(actual.status, "ok")
        self.assertEqual(actual.attempts, 2)
        self.assertIn("attempt 1: no_ai_overview", actual.error)
        self.assertEqual(collect.call_count, 2)

    def test_missing_overview_fails_after_retry_but_captcha_is_not_retried(self):
        with TemporaryDirectory() as temp, patch(
            "experiment.google_search.collect_google_search",
            return_value=browser.BrowserResult("", None, 1, status="no_ai_overview", error="No overview"),
        ) as collect:
            result = browser.collect_browser("google_search", "Question", {"retries": 1},
                                             {"url": "https://www.google.com/search"}, "en-US", "UTC",
                                             Path(temp), "test")
        self.assertEqual(result.status, "no_ai_overview")
        self.assertEqual(result.attempts, 2)
        self.assertEqual(collect.call_count, 2)

        with TemporaryDirectory() as temp, patch(
            "experiment.google_search.collect_google_search",
            return_value=browser.BrowserResult("", None, 1, status="captcha"),
        ) as collect:
            result = browser.collect_browser("google_search", "Question", {"retries": 1},
                                             {"url": "https://www.google.com/search"}, "en-US", "UTC",
                                             Path(temp), "test")
        self.assertEqual(result.status, "captcha")
        self.assertEqual(result.attempts, 1)
        self.assertEqual(collect.call_count, 1)
