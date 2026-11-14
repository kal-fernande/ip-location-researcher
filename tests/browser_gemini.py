"""Offline Chromium checks: python -m unittest discover -s tests -p browser_gemini.py."""
import unittest

from playwright.sync_api import sync_playwright

from experiment.browser_collectors import _fill_prompt, _submit_prompt


class GeminiSubmissionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch(headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()

    def setUp(self):
        self.page = self.browser.new_page()
        self.page.route("**/*", lambda route: route.abort())
        self.addCleanup(self.page.close)

    def composer(self, *, enter=True, button=True):
        self.page.set_content('''
            <button aria-label="Send feedback" onclick="window.feedback++">Feedback</button>
            <rich-textarea><div class="ql-editor" contenteditable="true"></div></rich-textarea>
            <button class="send-button" disabled>Disabled duplicate</button>
            <button id="send" class="send-button" aria-label="שליחה" onclick="submit()">Send</button>
            <script>
            window.sent = []; window.feedback = 0;
            var editor = document.querySelector('.ql-editor');
            function submit() {
                window.sent.push(editor.innerText); editor.innerText = '';
                document.body.appendChild(document.createElement('user-query'));
            }
            </script>''')
        if enter:
            self.page.evaluate("editor.addEventListener('keydown', e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); submit(); } })")
        if not button:
            self.page.locator('#send').evaluate('el => el.remove()')
        return self.page.locator('.ql-editor')

    def test_enter_submits_exact_english_and_hebrew_prompts_once(self):
        for text in ('Exact question?', 'האם האמנה חלה?'):
            with self.subTest(prompt=text):
                editor = self.composer()
                _fill_prompt(editor, text)
                _submit_prompt(self.page, editor, 'gemini')
                self.assertEqual(self.page.evaluate('window.sent'), [text])
                self.assertEqual(self.page.evaluate('window.feedback'), 0)

    def test_enter_works_without_a_send_button(self):
        editor = self.composer(button=False)
        _fill_prompt(editor, 'Question')
        _submit_prompt(self.page, editor, 'gemini')
        self.assertEqual(self.page.evaluate('window.sent'), ['Question'])

    def test_hydration_replacing_editor_is_not_mistaken_for_submission(self):
        editor = self.composer(enter=False)
        self.page.evaluate("""editor.addEventListener('keydown', e => {
            if (e.key === 'Enter') {
                e.preventDefault();
                const replacement = editor.cloneNode(true);
                editor.replaceWith(replacement); editor = replacement;
            }
        })""")
        _fill_prompt(editor, 'Question')
        _submit_prompt(self.page, editor, 'gemini')
        self.assertEqual(self.page.evaluate('window.sent'), ['Question'])
        self.assertEqual(self.page.locator('user-query').count(), 1)

    def test_cleared_editor_without_a_submitted_turn_is_not_success(self):
        editor = self.composer(enter=False)
        self.page.evaluate("""editor.addEventListener('keydown', e => {
            if (e.key === 'Enter') { e.preventDefault(); editor.innerText = ''; }
        })""")
        _fill_prompt(editor, 'Question')
        with self.assertRaisesRegex(RuntimeError, 'could not be confirmed'):
            _submit_prompt(self.page, editor, 'gemini')
        self.assertEqual(self.page.evaluate('window.sent'), [])

    def test_send_fallback_skips_disabled_button_and_feedback(self):
        editor = self.composer(enter=False)
        _fill_prompt(editor, 'Question')
        _submit_prompt(self.page, editor, 'gemini')
        self.assertEqual([text.strip() for text in self.page.evaluate('window.sent')], ['Question'])
        self.assertEqual(self.page.evaluate('window.feedback'), 0)

    def test_unsubmitted_prompt_reports_failure_instead_of_waiting_for_answer(self):
        editor = self.composer(enter=False, button=False)
        _fill_prompt(editor, 'Question')
        with self.assertRaisesRegex(RuntimeError, 'Gemini did not submit'):
            _submit_prompt(self.page, editor, 'gemini')
        self.assertEqual(self.page.evaluate('window.sent'), [])
        self.assertEqual(self.page.evaluate('window.feedback'), 0)

    def test_response_start_does_not_cause_a_duplicate_even_if_editor_retains_text(self):
        editor = self.composer(enter=False)
        self.page.evaluate("""editor.addEventListener('keydown', e => {
            if (e.key === 'Enter') {
                e.preventDefault(); window.sent.push(editor.innerText);
                document.body.appendChild(document.createElement('model-response'));
            }
        })""")
        _fill_prompt(editor, 'Question')
        _submit_prompt(self.page, editor, 'gemini')
        self.assertEqual(self.page.evaluate('window.sent'), ['Question'])
