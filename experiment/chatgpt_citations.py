"""Read citation data delivered to ChatGPT's logged-out browser.

Only declared Cite/Link components for the displayed assistant message count
as sources. Search results, URL allowlists, scripts, and transport tokens are
not copied or evaluated.
"""
from __future__ import annotations

import base64
import json
from urllib.parse import urlsplit

from .links import normalize_sources


def _components(renderer):
    if not isinstance(renderer, dict):
        return None
    app_data = renderer.get("appData")
    genui = app_data.get("opGenui") if isinstance(app_data, dict) else None
    return genui.get("componentResults") if isinstance(genui, dict) else None


def parse_citation_stream(body: str) -> dict:
    messages = {}
    for line in body.splitlines():
        try:
            record = json.loads(line)
        except (TypeError, ValueError):
            continue
        if not isinstance(record, dict) or record.get("type") != "content":
            continue
        message_id = record.get("messageId")
        if not isinstance(message_id, str) or not message_id:
            continue
        if record.get("mode") == "replace":
            messages.pop(message_id, None)
        renderers = [record.get("dil")]
        for reference in record.get("references") or []:
            if isinstance(reference, dict) and reference.get("messageId", message_id) == message_id:
                renderers.append(reference.get("modelDilV2"))
        for renderer in renderers:
            components = _components(renderer)
            if not isinstance(components, dict):
                continue
            groups = []
            for component_id, component in components.items():
                if not isinstance(component, dict):
                    continue
                kind = component.get("componentName")
                if kind not in {"Cite", "Link"}:
                    continue
                state = component.get("state") or {}
                items = state.get("items") if kind == "Cite" else [state]
                items = items if isinstance(items, list) else []
                sources = normalize_sources([
                    {"url": item.get("url"), "title": item.get("title") or item.get("source_label")}
                    for item in items if isinstance(item, dict)
                ])
                groups.append({"id": component_id, "kind": kind, "sources": sources,
                               "complete": bool(items) and all(
                                   isinstance(item, dict) and bool(normalize_sources([{"url": item.get("url")}]))
                                   for item in items
                               )})
            messages[message_id] = {"message_id": message_id, "revision": record.get("revision"),
                                    "groups": groups}
    return messages


class ChatGPTCitationCapture:
    def __init__(self, page):
        self.messages = {}
        self.errors = []
        self.response_count = 0
        self._buffers = {}
        self._stream_messages = {}
        self._starting = set()
        page.context.on("requestfinished", self._finished)
        # ChatGPT can leave its response connection open after the answer has
        # finished rendering. Observe received chunks without waiting for the
        # connection to close or changing any browser request/response.
        try:
            self._cdp = page.context.new_cdp_session(page)
            self._cdp.send("Network.enable")
            self._cdp.on("Network.responseReceived", self._response_received)
            self._cdp.on("Network.dataReceived", self._data_received)
        except Exception:
            self.errors.append("Chromium citation-stream observation is unavailable")

    @staticmethod
    def _matches(url):
        parsed = urlsplit(url)
        return parsed.hostname in {"chatgpt.com", "www.chatgpt.com"} and parsed.path == "/unauth-mweb/conversation/updates"

    def _response_received(self, event):
        response = event.get("response") or {}
        if not self._matches(response.get("url", "")) or response.get("status") != 200:
            return
        if "application/vnd.openai.conversation-source+ndjson" not in response.get("mimeType", ""):
            return
        request_id = event["requestId"]
        self.response_count += 1
        self._buffers[request_id] = b""
        self._starting.add(request_id)
        try:
            result = self._cdp.send("Network.streamResourceContent", {"requestId": request_id})
            self._buffers[request_id] = base64.b64decode(result.get("bufferedData", "")) + self._buffers[request_id]
            self._update(request_id, self._buffers[request_id].decode("utf-8", errors="ignore"))
        except Exception:
            self.errors.append("Chromium citation response could not be streamed")
        finally:
            self._starting.discard(request_id)

    def _data_received(self, event):
        request_id = event.get("requestId")
        if request_id not in self._buffers or not event.get("data"):
            return
        self._buffers[request_id] += base64.b64decode(event["data"])
        if request_id not in self._starting:
            self._update(request_id, self._buffers[request_id].decode("utf-8", errors="ignore"))

    def _update(self, stream_id, body):
        self._stream_messages[stream_id] = parse_citation_stream(body)
        self.messages = {}
        for messages in self._stream_messages.values():
            self.messages.update(messages)

    def _finished(self, request):
        if not self._matches(request.url):
            return
        self.response_count = max(self.response_count, 1)
        try:
            response = request.response()
            if response is None or response.status != 200:
                return
            if "application/vnd.openai.conversation-source+ndjson" not in response.headers.get("content-type", ""):
                return
            self._update("finished:" + request.url, response.text())
        except Exception as error:
            self.errors.append(f"{type(error).__name__}: citation response could not be read")

    def for_element(self, element):
        message_id = element.evaluate("""el => {
            const message = el.closest('[data-message-role="assistant"], [data-message-author-role="assistant"]');
            return message?.id || message?.getAttribute('data-message-id') || null;
        }""")
        return self.messages.get(message_id)
