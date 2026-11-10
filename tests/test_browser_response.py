"""Unit tests for browser end-of-turn heuristics (no Playwright)."""
from __future__ import annotations
import unittest

from experiment.browser_collectors import looks_like_non_answer, response_turn_complete


class LooksLikeNonAnswerTests(unittest.TestCase):
    def test_searching_the_web(self):
        self.assertTrue(looks_like_non_answer("Searching the web"))
        self.assertTrue(looks_like_non_answer("  searching the web  "))
        self.assertTrue(looks_like_non_answer("Searching the web."))

    def test_gemini_error_boilerplate(self):
        self.assertTrue(looks_like_non_answer("I seem to be encountering an error"))
        self.assertTrue(
            looks_like_non_answer("I'm having a hard time fulfilling your request")
        )

    def test_empty(self):
        self.assertTrue(looks_like_non_answer(""))
        self.assertTrue(looks_like_non_answer("   \n"))
        self.assertTrue(looks_like_non_answer(None))

    def test_real_answer_not_rejected(self):
        self.assertFalse(
            looks_like_non_answer(
                "Yes. The prevailing position of the international community "
                "is that the Fourth Geneva Convention applies."
            )
        )
        self.assertFalse(
            looks_like_non_answer(
                "שאלה זו לגבי החלת אמנת ז'נבה הרביעית על יהודה ושומרון "
                "היא נושא משפטי ובינלאומי מרכזי."
            )
        )

    def test_answer_mentioning_search_not_rejected(self):
        # A long answer that happens to discuss web search should still count.
        text = (
            "The model may appear to be searching the web while it gathers "
            "sources, but the legal consensus is that the Convention applies."
        )
        self.assertFalse(looks_like_non_answer(text))


class ResponseTurnCompleteTests(unittest.TestCase):
    def test_busy_blocks_accept(self):
        self.assertFalse(response_turn_complete("A real paragraph of answer text.", True))

    def test_non_answer_blocks_accept(self):
        self.assertFalse(response_turn_complete("Searching the web", False))

    def test_ready_answer(self):
        self.assertTrue(
            response_turn_complete("Yes. The Convention applies under prevailing law.", False)
        )


if __name__ == "__main__":
    unittest.main()
