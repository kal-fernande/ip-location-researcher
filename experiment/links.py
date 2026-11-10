"""Preserve source URLs separately from verbatim answer text."""
from __future__ import annotations

import json
import re
from urllib.parse import urljoin, urlsplit

MARKDOWN_LINK = re.compile(r"\[([^\]]*)\]\((https?://(?:[^\s()]|\([^()]*\))+)(?:\s+\"[^\"]*\")?\)")
BARE_URL = re.compile(r"https?://[^\s<>\"\x00-\x1f]+")


def normalize_sources(sources, base_url: str = "") -> list[dict]:
    """Keep the first label for each HTTP(S) URL; ignore page fragments/actions."""
    if isinstance(sources, str):
        try:
            sources = json.loads(sources)
        except (ValueError, TypeError):
            return []
    normalized, seen = [], set()
    for source in sources or []:
        if not isinstance(source, dict):
            continue
        url = str(source.get("url") or "").strip()
        if not url or url.startswith("#"):
            continue
        url = urljoin(base_url, url)
        try:
            parsed = urlsplit(url)
            if parsed.scheme not in ("http", "https") or not parsed.hostname:
                continue
        except ValueError:
            continue
        if url in seen:
            continue
        seen.add(url)
        normalized.append({"title": " ".join(str(source.get("title") or "").split()), "url": url})
    return normalized


def sources_from_text(text: str) -> list[dict]:
    """Recover URLs actually present in Markdown/plain text, without guessing citations."""
    text = text or ""
    markdown = list(MARKDOWN_LINK.finditer(text))
    sources = [{"title": match[1], "url": match[2]} for match in markdown]
    for match in BARE_URL.finditer(text):
        if any(link.start() <= match.start() < link.end() for link in markdown):
            continue
        url = match[0].rstrip(".,;:!?]}")
        while url.endswith(")") and url.count(")") > url.count("("):
            url = url[:-1]
        sources.append({"title": "", "url": url})
    return normalize_sources(sources)


def observation_sources(observation: dict) -> list[dict]:
    """Read new results and older Google metadata without changing saved answers."""
    metadata = observation.get("metadata") or {}
    return normalize_sources([
        *normalize_sources(observation.get("sources")),
        *normalize_sources(metadata.get("sources")),
        *normalize_sources([{"url": url} for url in (observation.get("source_urls") or "").splitlines()]),
        *sources_from_text(observation.get("response", "")),
    ])


def sources_from_element(element, page_url: str) -> list[dict]:
    anchors = element.locator("a[href]").evaluate_all(
        "elements => elements.map(a => ({title: a.innerText || a.getAttribute('aria-label') || '', url: a.getAttribute('href')}))"
    )
    return normalize_sources(anchors, page_url)
