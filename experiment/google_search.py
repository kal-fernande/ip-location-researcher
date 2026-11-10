"""Collect the AI Overview from an ordinary, logged-out Google search.

No AI Mode, API, organic-result fallback, or location/language inference from
the prompt. The fixed browser locale determines hl; the VPN determines IP.
"""
from __future__ import annotations

import re
import time
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit, urlunsplit

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

from .browser_collectors import (
    BrowserResult, _captcha_detected, _dismiss_cookie_banners, _frontmost_app,
    _restore_focus, _safe_screenshot, _wait_for_human_captcha_clear, _save_browser_evidence,
    looks_like_non_answer,
)

# The interface language is fixed across conditions. Hebrew labels are also
# recognized in case Google renders a localized heading despite that setting.
OVERVIEW_LABEL = re.compile(r"^(AI Overview|AI overview|סקירה כללית של AI)$", re.I)
OVERVIEW_ANCESTOR = (
    "xpath=ancestor::*[@data-mcpr or @data-aquarium or "
    "@data-subtree='mfc' or @data-attrid='AIOverview'][1]"
)
# Read answer bodies only, within a positively identified overview. Never read
# #search, #rso, body, or general result snippets as the answer.
ANSWER_SELECTORS = ("[data-container-id='main-col']", ".mZJjHb", "[data-attrid='wa:/description']", ".LGOjhe", ".LT6XE")
CHROME_LINE = re.compile(
    r"^(AI Overview|Show more|Show less|Listen|Share|Feedback|"
    r"AI responses may include mistakes\.?|Learn more|"
    r"סקירה כללית של AI|הצגת עוד|הצגת פחות)$", re.I,
)


def build_search_url(base_url: str, prompt: str, locale: str) -> str:
    base = urlsplit(base_url)
    # Deliberately omit gl, uule, lr, udm and prior query parameters. Using a
    # country override would undermine the IP-location manipulation.
    return urlunsplit((base.scheme, base.netloc, "/search", urlencode({
        "q": prompt, "hl": locale.replace("_", "-").split("-")[0],
    }), ""))


def source_links(links: list[dict], page_url: str) -> list[dict]:
    """Keep unique external citations, resolving Google's redirect links."""
    sources = []
    seen = set()
    google_host = urlsplit(page_url).hostname or "www.google.com"
    google_domain = google_host.removeprefix("www.")
    for link in links:
        url = urljoin(page_url, link.get("url", ""))
        parsed = urlsplit(url)
        host = parsed.hostname or ""
        redirect = host in (google_host, google_domain) and parsed.path in {"/url", "/goto"}
        if redirect:
            params = parse_qs(parsed.query)
            target = (params.get("q") or params.get("url") or [""])[0]
            if target.startswith(("https://", "http://")):
                url = target
                parsed = urlsplit(url)
                host = parsed.hostname or ""
        if parsed.scheme not in ("https", "http") or not host:
            continue
        if (host == google_domain or host.endswith("." + google_domain)) and not redirect:
            continue
        if url in seen:
            continue
        seen.add(url)
        sources.append({"title": " ".join((link.get("title") or "").split()), "url": url})
    return sources


def final_sources(page, region) -> tuple[list[dict], dict]:
    # Open Google's source list without following any source or starting AI Mode.
    controls = region.get_by_role("button", name="Show all related results", exact=True)
    found = read = 0
    for index in range(controls.count()):
        control = controls.nth(index)
        if control.is_visible():
            found += 1
            try:
                control.click(timeout=3000)
                page.locator("[role='dialog'][aria-label='Related links'] a[href]").first.wait_for(state="attached", timeout=4000)
                read += 1
            except Exception:
                pass
            break
    links = region.locator("a[href]").evaluate_all(
        "elements => elements.map(a => ({title: a.innerText || a.getAttribute('aria-label') || '', url: a.href}))"
    )
    sources = source_links(links, page.url)
    captured_links = [dict(source) for source in sources]
    redirects = []
    for source in sources:
        original = source["url"]
        parsed = urlsplit(original)
        if parsed.hostname == urlsplit(page.url).hostname and parsed.path in {"/goto", "/url"}:
            try:
                response = page.request.get(original, max_redirects=0, timeout=5000)
                target = urljoin(original, response.headers.get("location", ""))
                destination = urlsplit(target)
                if destination.scheme in {"http", "https"} and destination.hostname != parsed.hostname:
                    source["url"] = target
                    redirects.append({"redirect_url": original, "source_url": target})
                response.dispose()
            except Exception:
                pass  # Preserve the captured redirect URL when it cannot be resolved.
    from .links import normalize_sources
    return normalize_sources(sources), {"controls_found": found, "controls_read": read,
                                       "complete": found == read, "redirects": redirects, "links": captured_links}


def _overview_labels(page):
    labels = page.get_by_text(OVERVIEW_LABEL, exact=True)
    headings = []
    for index in range(labels.count()):
        label = labels.nth(index)
        # A conventional result titled "AI Overview" is not the AI module.
        if label.is_visible() and not label.locator("xpath=ancestor-or-self::*[self::a or self::h3]").count():
            headings.append(label)
    return headings


def overview_region(page):
    for label in _overview_labels(page):
        region = label.locator(OVERVIEW_ANCESTOR)
        if region.count() and region.first.is_visible():
            return region.first
    return None


def overview_snapshot(region, page_url: str) -> tuple[str, list[dict], str | None]:
    text, matched = "", None
    for selector in ANSWER_SELECTORS:
        bodies = region.locator(selector)
        chunks = []
        # Some layouts nest answer blocks with the same class. Read their
        # outermost text once, while preserving repeated separate paragraphs.
        outermost = bodies.evaluate_all(
            "nodes => nodes.map((node, index) => ({index, nested: nodes.some(other => other !== node && other.contains(node))})).filter(item => !item.nested).map(item => item.index)"
        )
        for index in outermost:
            body = bodies.nth(index)
            if body.is_visible():
                chunk = body.inner_text(timeout=1500).strip()
                if chunk:
                    chunks.append(chunk)
        if chunks:
            text, matched = "\n\n".join(chunks), selector
            break
    text = "\n".join(line for line in text.splitlines() if not CHROME_LINE.fullmatch(line.strip())).strip()
    links = region.locator("a[href]").evaluate_all(
        "elements => elements.map(a => ({title: a.innerText || a.getAttribute('aria-label') || '', url: a.href}))"
    )
    return text, source_links(links, page_url), matched


def _visible_any(page, selector: str) -> bool:
    matches = page.locator(selector)
    return any(matches.nth(i).is_visible() for i in range(matches.count()))


def collect_google_search(
    prompt: str, browser_cfg: dict, provider_cfg: dict, locale: str,
    timezone_id: str, screenshot_dir: Path, observation_id: str,
) -> BrowserResult:
    started = time.perf_counter()
    timeout = float(browser_cfg.get("timeout_seconds", 120))
    url = build_search_url(provider_cfg["url"], prompt, locale)
    metadata = {"surface": "ai_overview", "search_url": url,
                "final_url": url, "overview_found": False, "sources": []}
    screenshot_dir.mkdir(parents=True, exist_ok=True)
    page = None
    region = None
    manual = False
    human_wait_ms = 0
    human_timeout_seconds = None

    def finish(status, response="", error=None):
        shot = None
        if page is not None:
            metadata["final_url"] = page.url
            if status != "ok" and browser_cfg.get("screenshot_failures", True):
                shot = _safe_screenshot(page, screenshot_dir / f"{observation_id}_{status}.png")
        result = BrowserResult(
            response, None, round((time.perf_counter() - started) * 1000),
            status=status, error=error, screenshot_path=shot,
            manual_intervention=manual, metadata=metadata,
            sources=metadata["sources"],
            human_wait_ms=human_wait_ms or None,
            human_timeout_seconds=human_timeout_seconds,
        )
        element = None
        if region is not None and metadata.get("extraction_selector"):
            try:
                candidates = region.locator(metadata["extraction_selector"])
                if candidates.count():
                    element = candidates.first
            except Exception:
                pass  # A closed/crashed page must retain its original collection error.
        return _save_browser_evidence(result, page, element, provider="google_search", prompt=prompt,
                                      browser_cfg=browser_cfg, screenshot_dir=screenshot_dir,
                                      observation_id=observation_id, answer_complete=bool(response))

    try:
        front_app = _frontmost_app()
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=browser_cfg.get("headless", False))
            try:
                context = browser.new_context(locale=locale, timezone_id=timezone_id, permissions=[])
                page = context.new_page()
                _restore_focus(front_app, browser_cfg)
                page.goto(url, wait_until="domcontentloaded", timeout=int(timeout * 1000))
                _restore_focus(front_app, browser_cfg)
                deadline = time.monotonic() + timeout
                last = ""
                stable = 0
                expansion_attempted = False
                while time.monotonic() < deadline:
                    _dismiss_cookie_banners(page)
                    if _captcha_detected(page):
                        cleared, wait_ms, timeout_s = _wait_for_human_captcha_clear(page, "google_search", browser_cfg)
                        human_wait_ms += wait_ms
                        if timeout_s is not None:
                            human_timeout_seconds = timeout_s
                        if not cleared:
                            return finish("captcha", error="Google Search verification/unusual traffic blocked the query; human intervention timed out or is disabled.")
                        manual = True
                        deadline = time.monotonic() + timeout
                    if urlsplit(page.url).hostname == "accounts.google.com":
                        return finish("login_required", error="Google Search redirected to sign-in.")
                    current_url = urlsplit(page.url)
                    if current_url.path.startswith("/aimode") or parse_qs(current_url.query).get("udm") == ["50"]:
                        return finish("ui_error", error="Google navigated to AI Mode instead of an AI Overview; that surface is not part of this collection.")
                    region = overview_region(page)
                    if region is not None:
                        metadata["overview_found"] = True
                        more = region.get_by_role("button", name=re.compile(r"^(Show more|הצגת עוד)$", re.I))
                        has_more = any(more.nth(i).is_visible() for i in range(more.count()))
                        if has_more and not expansion_attempted:
                            next(more.nth(i) for i in range(more.count()) if more.nth(i).is_visible()).click(timeout=3000)
                            expansion_attempted = True
                            metadata["expanded"] = True
                            stable = 0
                            time.sleep(2)
                            continue
                        text, sources, selector = overview_snapshot(region, page.url)
                        metadata.update(sources=sources, extraction_selector=selector)
                        busy = _visible_any(region, "[aria-busy='true'], [role='progressbar']")
                        pending = " ".join(text.lower().split()).rstrip(".…") in {"thinking", "generating", "searching"}
                        if text and not looks_like_non_answer(text) and not pending and not busy and not has_more:
                            stable = stable + 1 if text == last else 0
                            if stable >= 3:
                                sources, capture = final_sources(page, region)
                                metadata.update(sources=sources, source_capture=capture)
                                if not capture["complete"]:
                                    return finish("ui_error", text, "Google's related-source list could not be read; citation URLs may be incomplete.")
                                return finish("ok", text)
                        else:
                            stable = 0
                        last = text
                    time.sleep(2)

                if metadata["overview_found"]:
                    if not metadata.get("extraction_selector"):
                        return finish("ui_error", error="AI Overview container found, but its answer body could not be identified; inspect the screenshot/selectors.")
                    return finish("timeout", error="An AI Overview appeared, but a complete, expanded, stable answer could not be extracted.")
                # A visible heading with unknown structure means a selector
                # problem, not evidence that Google supplied no overview.
                if _overview_labels(page):
                    return finish("ui_error", error="AI Overview heading found, but its supported container was missing; inspect the screenshot/selectors.")
                if _visible_any(page, "#search, #rso"):
                    if _visible_any(page, "#search [aria-busy='true'], #search [role='progressbar']"):
                        return finish("timeout", error="Google Search was still loading when the wait period ended.")
                    return finish("no_ai_overview", error="Google Search loaded, but no supported AI Overview appeared within the wait period. Organic results were not substituted.")
                return finish("ui_error", error="Google Search results and AI Overview could not be identified; inspect the screenshot.")
            except PlaywrightTimeoutError as error:
                return finish("timeout", error=str(error))
            except Exception as error:
                return finish("error", error=str(error))
            finally:
                browser.close()
    except Exception as error:
        return finish("error", error=str(error))
