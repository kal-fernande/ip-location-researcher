"""Opt-in Chromium tests using synthetic HTML, with all network requests intercepted.

Run: .venv/bin/python -m unittest discover -s tests -p 'browser_google_search.py' -v
These verify the real collector and DOM extraction, not Google's live markup.
"""
from contextlib import contextmanager
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from playwright.sync_api import sync_playwright

from experiment import google_search as google

FIXTURE = (Path(__file__).parent / "fixtures/google_ai_overview.html").read_text()
NO_OVERVIEW = '<html><body><main id="search"><div id="rso"><p>ORGANIC_SNIPPET</p></div></main></body></html>'


class GoogleDOMTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.playwright = sync_playwright().start()

    @classmethod
    def tearDownClass(cls):
        cls.playwright.stop()

    def setUp(self):
        self.browser = self.playwright.chromium.launch(headless=True)
        self.addCleanup(self.browser.close)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.html = FIXTURE
        self.chatgpt_stream = None
        self.sleep_hook = lambda: None
        self.clock = 0
        real_new_context = self.browser.new_context
        self.contexts = []

        def new_context(**kwargs):
            context = real_new_context(**kwargs)
            def fulfill(route):
                if self.chatgpt_stream is not None and "/unauth-mweb/conversation/updates" in route.request.url:
                    route.fulfill(status=200, content_type="application/vnd.openai.conversation-source+ndjson",
                                  headers={"Access-Control-Allow-Origin": "*"}, body=self.chatgpt_stream)
                else:
                    route.fulfill(status=200, content_type="text/html; charset=utf-8", body=self.html)
            context.route("**/*", fulfill)
            self.contexts.append(context)
            return context

        @contextmanager
        def fake_playwright():
            yield SimpleNamespace(chromium=SimpleNamespace(launch=lambda **kwargs: self.browser))

        def sleep(seconds):
            self.clock += seconds
            self.sleep_hook()

        for target, name, value in (
            (self.browser, "new_context", new_context),
            (google, "sync_playwright", fake_playwright),
            (google, "_frontmost_app", lambda: None),
            (google, "_restore_focus", lambda *args: None),
            (google, "time", SimpleNamespace(monotonic=lambda: self.clock, sleep=sleep,
                                             time=lambda: self.clock, perf_counter=time.perf_counter)),
        ):
            mock = patch.object(target, name, value)
            mock.start()
            self.addCleanup(mock.stop)

    def collect(self, prompt="Exact question?"):
        return google.collect_google_search(
            prompt, {"headless": True, "timeout_seconds": 14, "human_captcha_enabled": False,
                     "screenshot_failures": True},
            {"url": "https://www.google.com/search"}, "en-US", "UTC", self.root, "fixture",
        )

    def assert_offline_evidence(self, result):
        from experiment.evidence import validate_evidence
        evidence = result.metadata["evidence"]
        self.assertEqual(evidence["status"], "ok", evidence.get("errors"))
        self.assertEqual(validate_evidence(evidence), [])
        reviewer = self.playwright.chromium.launch(headless=True)
        try:
            context = reviewer.new_context(offline=True, java_script_enabled=False)
            page = context.new_page()
            page.goto(Path(evidence["review_html"]).as_uri())
            self.assertEqual(page.locator("#answer").inner_text(), result.response)
            page.goto(Path(evidence["archive"]).as_uri())
            visible = " ".join(page.locator("body").inner_text().split())
            self.assertIn(" ".join(result.response.split())[:60], visible)
        finally:
            reviewer.close()

    def test_expands_answer_and_keeps_only_overview_citations(self):
        result = self.collect()
        self.assertEqual(result.status, "ok")
        self.assertIn("complete expanded answer", result.response)
        self.assertNotIn("Show more", result.response)
        self.assertNotIn("ORGANIC", result.response)
        self.assertEqual([s["url"] for s in result.metadata["sources"]], [
            "https://en.wikipedia.org/wiki/Fourth_Geneva_Convention", "https://example.org/source",
        ])
        self.assertTrue(result.metadata["expanded"])
        self.assertIsNone(result.model_observed)
        self.assertEqual(len(self.browser.contexts), 0)
        self.assert_offline_evidence(result)

    def test_hebrew_text_and_alternate_container(self):
        self.html = '''<main id="search"><div data-subtree="mfc"><h2>סקירה כללית של AI</h2>
          <div data-attrid="wa:/description"><p>זו תשובה מלאה בעברית.</p></div></div></main>'''
        result = self.collect("האם האמנה חלה?")
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.response, "זו תשובה מלאה בעברית.")
        self.assertIn("hl=en", result.metadata["search_url"])

    def test_no_overview_never_substitutes_organic_results(self):
        self.html = NO_OVERVIEW
        result = self.collect()
        self.assertEqual(result.status, "no_ai_overview")
        self.assertEqual(result.response, "")
        self.assertTrue(Path(result.screenshot_path).exists())

    def test_cleared_verification_with_lingering_anchor_and_captcha_snippets_is_not_blocked(self):
        self.html = '''<main id="search"><div id="rso">A result about CAPTCHA and Cloudflare.</div></main>
          <iframe src="/recaptcha/api2/anchor" srcdoc="&lt;p&gt;Verified&lt;/p&gt;"></iframe>
          <iframe src="/recaptcha/api2/bframe" srcdoc="&lt;p&gt;Expired puzzle&lt;/p&gt;"></iframe>'''
        result = self.collect()
        self.assertEqual(result.status, "no_ai_overview")
        self.assertEqual(result.response, "")

    def test_verification_page_without_widget_is_captcha(self):
        self.html = '<body>Our systems have detected unusual traffic from your computer network.</body>'
        result = self.collect()
        self.assertEqual(result.status, "captcha")
        self.assertEqual(result.response, "")
        self.assertTrue(Path(result.screenshot_path).exists())
        self.assertEqual(len(self.browser.contexts), 0)

    def test_cleared_google_verification_retains_human_wait_metadata(self):
        with patch.object(google, "_captcha_detected", side_effect=[True] + [False] * 20), patch.object(
            google, "_wait_for_human_captcha_clear", return_value=(True, 1250, 300),
        ):
            result = self.collect()
        self.assertEqual(result.status, "ok")
        self.assertTrue(result.manual_intervention)
        self.assertEqual(result.human_wait_ms, 1250)
        self.assertEqual(result.human_timeout_seconds, 300)

    def test_known_heading_with_unknown_container_is_ui_error(self):
        self.html = '<main id="search"><section><h1>AI Overview</h1><p>Unknown markup</p></section></main>'
        self.assertEqual(self.collect().status, "ui_error")

    def test_source_cards_without_answer_are_not_accepted(self):
        self.html = '<main id="search"><div data-mcpr="x"><h1>AI Overview</h1><a href="https://example.org">Source snippet only</a></div></main>'
        self.assertEqual(self.collect().status, "ui_error")

    def test_organic_result_titled_ai_overview_is_not_an_overview(self):
        self.html = '<main id="search"><div data-mcpr="x"><h3><a href="https://example.org">AI Overview</a></h3><div class="LGOjhe">An organic snippet.</div></div></main>'
        result = self.collect()
        self.assertEqual(result.status, "no_ai_overview")
        self.assertEqual(result.response, "")

    def test_nested_answer_blocks_are_not_duplicated(self):
        self.html = '<main id="search"><div data-mcpr="x"><h1>AI Overview</h1><div class="LT6XE">First paragraph.<div class="LT6XE">Second paragraph.</div></div></div></main>'
        result = self.collect()
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.response.count("Second paragraph."), 1)

    def test_google_keeps_only_main_answer_and_reads_related_source_urls(self):
        self.html = '''<main id="search"><div data-mcpr="x"><h1>AI Overview</h1><div class="LT6XE">
          <div data-container-id="main-col">Actual AI answer.</div>
          <div data-container-id="rhs-col"><a href="/goto?url=opaque-token">Wikipedia</a>Source card snippet must not enter answer.</div>
          <div role="button" aria-label="Show all related results" onclick="document.querySelector('#related').innerHTML='&lt;a href=https://example.org/related&gt;Related source&lt;/a&gt;'">Show all</div>
          <div role="dialog" aria-label="Related links" id="related"></div>
          <footer>AI responses may include mistakes. Learn more</footer></div></div></main>'''
        result = self.collect()
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.response, "Actual AI answer.")
        self.assertEqual({source["url"] for source in result.sources}, {
            "https://www.google.com/goto?url=opaque-token", "https://example.org/related",
        })
        self.assertTrue(result.metadata["source_capture"]["complete"])

    def test_busy_generation_is_not_accepted_as_finished(self):
        self.html = '<main id="search"><div data-mcpr="x"><h1>AI Overview</h1><div class="LT6XE" aria-busy="true">Partial answer</div></div></main>'
        self.assertEqual(self.collect().status, "timeout")

    def test_failed_expansion_is_not_saved_as_success(self):
        self.html = FIXTURE.replace("document.getElementById('continuation').hidden=false; this.remove()", "void(0)")
        self.assertEqual(self.collect().status, "timeout")

    def test_late_overview_is_waited_for(self):
        self.html = NO_OVERVIEW
        def reveal():
            if self.clock == 2:
                self.contexts[0].pages[0].set_content(FIXTURE)
        self.sleep_hook = reveal
        self.assertEqual(self.collect().status, "ok")

    def test_loading_results_are_timeout_not_missing_overview(self):
        self.html = '<main id="search"><div role="progressbar">Loading</div></main>'
        self.assertEqual(self.collect().status, "timeout")

    def collect_chat_fixture(self, provider):
        from experiment import browser_collectors as browser
        if provider == "gemini":
            # These source-extraction fixtures already contain an answer.
            # Model a submitted user turn when Enter clears the composer.
            self.html += """<script>document.querySelector('.ql-editor').addEventListener('keydown', e => {
                if (e.key === 'Enter') {
                    e.preventDefault(); e.target.innerText = '';
                    document.body.appendChild(document.createElement('user-query'));
                }
            });</script>"""
        with patch.object(browser, "sync_playwright", google.sync_playwright), patch.object(browser, "time", google.time), patch.object(browser, "_frontmost_app", return_value=None), patch.object(browser, "_restore_focus"):
            return browser._collect_browser_once(
                provider, "Exact question", {"headless": True, "timeout_seconds": 14,
                                             "human_captcha_enabled": False},
                {"url": "https://example.org/chat"}, "en-US", "UTC", self.root, "chat-fixture",
            )

    def test_chatgpt_captures_links_only_from_latest_answer_before_browser_closes(self):
        self.html = '''<textarea id="prompt-textarea"></textarea><button data-testid="send-button">Send</button>
          <a href="https://example.org/outside">Navigation</a>
          <li data-message-role="assistant"><div data-assistant-markdown-root>Old answer <a href="https://example.org/old">Old source</a></div></li>
          <li data-message-role="assistant"><div data-assistant-markdown-root><p>New answer בעברית.</p>
          <a href="https://en.wikipedia.org/wiki/Example">Wikipedia</a>
          <a href="https://example.org/new?a=1&amp;b=2">Source</a>
          <a href="#footnote">1</a></div></li>'''
        result = self.collect_chat_fixture("chatgpt")
        self.assertEqual(result.status, "ok")
        self.assertIn("New answer בעברית.", result.response)
        self.assertNotIn("Old answer", result.response)
        self.assertEqual(result.sources, [
            {"title": "Wikipedia", "url": "https://en.wikipedia.org/wiki/Example"},
            {"title": "Source", "url": "https://example.org/new?a=1&b=2"},
        ])
        self.assertEqual(len(self.browser.contexts), 0)
        self.assert_offline_evidence(result)

    def test_gemini_captures_inline_links_with_unicode_labels(self):
        self.html = '''<rich-textarea><div class="ql-editor" contenteditable="true"></div></rich-textarea>
          <button aria-label="Send">Send</button>
          <model-response><message-content><p>תשובה.</p><a href="https://example.org/עברית">מקור</a>
          <a href="javascript:void(0)">Share</a></message-content></model-response>'''
        result = self.collect_chat_fixture("gemini")
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.sources, [{"title": "מקור", "url": "https://example.org/עברית"}])
        self.assert_offline_evidence(result)

    def test_chatgpt_reads_citation_button_targets_without_visiting_publishers(self):
        self.html = '''<textarea id="prompt-textarea"></textarea><button data-testid="send-button">Send</button>
          <li data-message-role="assistant"><div data-assistant-markdown-root>Answer.
          <span role="button" data-d-component="popover-trigger" aria-haspopup="dialog" aria-controls="citation:one"
           onmouseenter="document.getElementById('citation:one').hidden=false">Sources +1</span>
          <div id="citation:one" role="dialog" hidden>
          <button aria-label="Open First source" onclick="window.open('https://example.org/one','_blank')">First</button>
          <button aria-label="Open Second source" onclick="window.open('https://example.org/two','_blank')">Second</button>
          </div></div></li>'''
        result = self.collect_chat_fixture("chatgpt")
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.sources, [
            {"title": "First source", "url": "https://example.org/one"},
            {"title": "Second source", "url": "https://example.org/two"},
        ])
        capture = result.metadata["source_capture"]
        self.assertEqual((capture["controls_found"], capture["controls_read"], capture["complete"]), (1, 1, True))
        self.assertEqual(len(self.browser.contexts), 0)

    def test_chatgpt_reads_browser_response_citations_without_clicking_source_controls(self):
        import json
        self.html = '''<textarea id="prompt-textarea"></textarea><button data-testid="send-button">Send</button>
          <li id="answer" data-message-role="assistant"><div data-assistant-markdown-root>Exact answer בעברית.
          <span data-d-component="popover-trigger" aria-haspopup="dialog" aria-controls="never-opened">Source +1</span>
          </div></li><script>fetch('https://chatgpt.com/unauth-mweb/conversation/updates')</script>'''
        self.chatgpt_stream = json.dumps({"version": 2, "type": "content", "messageId": "answer", "revision": 1,
            "references": [{"messageId": "answer", "modelDilV2": {"appData": {"opGenui": {"componentResults": {
                "citation": {"componentName": "Cite", "state": {"items": [
                    {"url": "https://example.org/one", "title": "First"},
                    {"url": "https://example.org/two", "title": "Second"},
                ]}},
            }}}}}]})
        result = self.collect_chat_fixture("chatgpt")
        self.assertEqual(result.status, "ok")
        self.assertEqual([source["url"] for source in result.sources], ["https://example.org/one", "https://example.org/two"])
        self.assertEqual(result.metadata["source_capture"]["method"], "browser_response")
        self.assertTrue(result.metadata["source_capture"]["complete"])
        self.assertIn("Exact answer בעברית.", result.response)

    def test_gemini_opens_each_citation_chip_before_reading_urls(self):
        self.html = '''<rich-textarea><div class="ql-editor" contenteditable="true"></div></rich-textarea>
          <button aria-label="Send">Send</button><model-response><message-content><p>Answer.</p>
          <source-inline-chip><button onmouseenter="this.parentElement.querySelector('inline-source-card').innerHTML='&lt;a href=https://example.org/one&gt;First&lt;/a&gt;'">First</button><inline-source-card></inline-source-card></source-inline-chip>
          <source-inline-chip><button onmouseenter="this.parentElement.querySelector('inline-source-card').innerHTML='&lt;a href=https://example.org/two&gt;Second&lt;/a&gt;'">Second</button><inline-source-card></inline-source-card></source-inline-chip>
          </message-content></model-response>'''
        result = self.collect_chat_fixture("gemini")
        self.assertEqual(result.status, "ok")
        self.assertEqual([source["url"] for source in result.sources], ["https://example.org/one", "https://example.org/two"])
        capture = result.metadata["source_capture"]
        self.assertEqual((capture["controls_found"], capture["controls_read"], capture["complete"]), (2, 2, True))

    def test_unreadable_chatgpt_citation_keeps_partial_sources_and_reports_failure(self):
        self.html = '''<textarea id="prompt-textarea"></textarea><button data-testid="send-button">Send</button>
          <li data-message-role="assistant"><div data-assistant-markdown-root>Answer.
          <span role="button" data-d-component="popover-trigger" aria-haspopup="dialog" aria-controls="citations"
           onmouseenter="document.getElementById('citations').hidden=false">Sources</span>
          <div id="citations" role="dialog" hidden>
          <button aria-label="Open First" onclick="window.open('https://example.org/saved')">First</button>
          <button aria-label="Open Broken">Broken</button></div></div></li>'''
        result = self.collect_chat_fixture("chatgpt")
        self.assertEqual(result.status, "ui_error")
        self.assertEqual(result.sources, [{"title": "First", "url": "https://example.org/saved"}])
        self.assertFalse(result.metadata["source_capture"]["complete"])
        failure = result.metadata["source_capture"]["failures"][0]
        self.assertEqual((failure["control"], failure["source_button"]), (0, 1))
        self.assertIn("TimeoutError", failure["error"])
        self.assertIn("incomplete", result.error)

    def test_partial_chatgpt_answer_retains_links_when_generation_times_out(self):
        self.html = '''<textarea id="prompt-textarea"></textarea><button data-testid="send-button">Send</button>
          <button data-testid="stop-button">Stop</button>
          <li data-message-role="assistant"><div data-assistant-markdown-root>Partial answer.
          <a href="https://example.org/partial">Source</a></div></li>'''
        result = self.collect_chat_fixture("chatgpt")
        self.assertEqual(result.status, "timeout")
        self.assertIn("Partial answer.", result.response)
        self.assertEqual(result.sources, [{"title": "Source", "url": "https://example.org/partial"}])
