import base64
import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from experiment.chatgpt_citations import ChatGPTCitationCapture, parse_citation_stream
from experiment import browser_collectors as browser


def record(message="answer", revision=1, components=None, mode="append"):
    renderer = {"appData": {"opGenui": {"componentResults": components or {}}}}
    return {"version": 2, "type": "content", "messageId": message, "revision": revision,
            "mode": mode, "references": [{"messageId": message, "modelDilV2": renderer}]}


def cite(url="https://example.org/cited"):
    return {"componentName": "Cite", "state": {"items": [{"url": url, "title": "מקור"}]}}


class CitationStreamTests(unittest.TestCase):
    def test_reads_final_declared_components_without_search_results_or_tokens(self):
        event = record(components={"citation": cite(), "link": {
            "componentName": "Link", "state": {"url": "https://example.org/direct", "title": "Direct"}},
            "unrelated": {"componentName": "Image", "state": {"url": "https://example.org/image"}},
        })
        event["searchSources"] = {"url": "https://example.org/uncited"}
        event["approvedUrls"] = ["https://example.org/unused"]
        body = '\n'.join([json.dumps({"type": "control", "resumeToken": "not-source-data"}), json.dumps(event)])
        parsed = parse_citation_stream(body)["answer"]
        self.assertEqual([s["url"] for g in parsed["groups"] for s in g["sources"]],
                         ["https://example.org/cited", "https://example.org/direct"])
        self.assertNotIn("not-source-data", json.dumps(parsed))

    def test_later_component_snapshot_replaces_removed_citations(self):
        body = '\n'.join(json.dumps(x) for x in [
            record(revision=1, components={"old": cite("https://example.org/old")}),
            record(revision=2, components={"new": cite("https://example.org/new")}),
        ])
        self.assertEqual(parse_citation_stream(body)["answer"]["groups"][0]["id"], "new")

    def test_other_messages_are_kept_separate_and_replacement_clears_old_state(self):
        body = '\n'.join(json.dumps(x) for x in [record(components={"c": cite()}),
            record(message="other", components={"other": cite("https://example.org/other")}),
            {"type": "content", "messageId": "answer", "mode": "replace", "markdown": "New answer"},
        ])
        self.assertNotIn("answer", parse_citation_stream(body))
        self.assertIn("other", parse_citation_stream(body))

    def test_unsafe_or_missing_url_is_incomplete(self):
        event = record(components={"c": cite("javascript:alert(1)")})
        group = parse_citation_stream(json.dumps(event))["answer"]["groups"][0]
        self.assertFalse(group["complete"])
        self.assertEqual(group["sources"], [])

    def test_observer_filters_responses_and_matches_the_displayed_message(self):
        page = Mock()
        capture = ChatGPTCitationCapture(page)
        response = SimpleNamespace(status=200, headers={"content-type": "application/vnd.openai.conversation-source+ndjson"},
                                   text=lambda: json.dumps(record(components={"c": cite()})))
        request = SimpleNamespace(url="https://chatgpt.com/unauth-mweb/conversation/updates", response=lambda: response)
        capture._finished(request)
        element = Mock(); element.evaluate.return_value = "answer"
        self.assertEqual(capture.for_element(element)["message_id"], "answer")
        element.evaluate.return_value = "another-message"
        self.assertIsNone(capture.for_element(element))
        ignored = SimpleNamespace(url="https://chatgpt.com/ces/v1/t", response=Mock())
        capture._finished(ignored)
        ignored.response.assert_not_called()

    def test_partial_citations_do_not_replace_a_finished_answer_with_a_retry(self):
        result = browser.BrowserResult("Finished answer", None, 1, status="ui_error",
                                       metadata={"source_capture": {"complete": False}})
        with patch.object(browser, "_collect_browser_once", return_value=result) as collect:
            actual = browser.collect_browser("chatgpt", "Question", {"retries": 1}, {}, "en-US", "UTC", None, "id")
        collect.assert_called_once()
        self.assertIs(actual, result)

    def test_open_connection_delivers_citations_before_request_finished(self):
        page = Mock()
        page.context.new_cdp_session.return_value.send.return_value = {"bufferedData": ""}
        capture = ChatGPTCitationCapture(page)
        capture._response_received({"requestId": "stream", "response": {
            "url": "https://chatgpt.com/unauth-mweb/conversation/updates", "status": 200,
            "mimeType": "application/vnd.openai.conversation-source+ndjson"}})
        data = (json.dumps(record(components={"c": cite()}), ensure_ascii=False) + '\n').encode()
        split = data.index('מקור'.encode()) + 1
        for chunk in [data[:split], data[split:]]:
            capture._data_received({"requestId": "stream", "data": base64.b64encode(chunk).decode()})
        self.assertTrue(capture.messages["answer"]["groups"][0]["complete"])
        self.assertEqual(capture.messages["answer"]["groups"][0]["sources"][0]["title"], "מקור")
        replacement = (json.dumps({"type": "content", "messageId": "answer", "mode": "replace"}) + '\n').encode()
        capture._data_received({"requestId": "stream", "data": base64.b64encode(replacement).decode()})
        self.assertNotIn("answer", capture.messages)


if __name__ == "__main__":
    unittest.main()
