\
from __future__ import annotations
import json
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
from .links import normalize_sources, sources_from_element, sources_from_text

@dataclass
class BrowserResult:
    response: str
    model_observed: str | None
    latency_ms: int
    status: str = "ok"
    error: str | None = None
    screenshot_path: str | None = None
    manual_intervention: bool = False
    attempts: int = 1  # 1 = no retry
    metadata: dict | None = None  # Google Search surface, query URL, and cited links (JSON)
    sources: list[dict] = field(default_factory=list)
    human_wait_ms: int | None = None  # time in CAPTCHA human-wait loops
    human_timeout_seconds: int | None = None  # configured wait budget when a wait ran

    def __post_init__(self):
        self.sources = normalize_sources([
            *self.sources, *((self.metadata or {}).get("sources") or []),
            *sources_from_text(self.response),
        ])

# Settings an app's entry in browser.providers may override, because apps
# differ in speed (logged-out Gemini takes about a minute, ChatGPT seconds).
PER_PROVIDER_SETTINGS = ("timeout_seconds", "retries", "prompt_wait_seconds", "pause_after_submit_seconds")

def provider_settings(browser_cfg: dict, provider_cfg: dict) -> dict:
    """browser_cfg with the app's own values for PER_PROVIDER_SETTINGS."""
    return {**browser_cfg, **{k: provider_cfg[k] for k in PER_PROVIDER_SETTINGS if k in provider_cfg}}

# Failures that a fresh browser may not repeat. A CAPTCHA or a login wall would
# just come back, so those are not retried.
RETRY_STATUSES = {"timeout", "ui_error", "error", "no_ai_overview"}

@dataclass
class BrowserCheckResult:
    provider: str
    url: str
    status: str
    latency_ms: int
    error: str | None = None
    screenshot_path: str | None = None
    manual_intervention: bool = False

PROVIDER_SELECTORS = {
    "google_search": {
        "prompt": ["textarea[name='q']", "input[name='q']"],
    },
    "chatgpt": {
        "prompt": [
            "#prompt-textarea",
            "div[contenteditable='true'][data-virtualkeyboard='true']",
            "div[contenteditable='true']",
            "textarea",
        ],
        "send": [
            "button[data-testid='send-button']",
            "button[aria-label*='Send']",
            "button[aria-label*='send']",
        ],
        "stop": [
            "button[data-testid='stop-button']",
            "button[aria-label*='Stop']",
            "button[aria-label*='stop']",
        ],
        "response": [
            # Page as of 2026-10-13: <li data-message-role="assistant"> holds the answer.
            "li[data-message-role='assistant'] [data-assistant-markdown-root]",
            "li[data-message-role='assistant']",
            # Older page.
            "[data-message-author-role='assistant']",
            "article[data-testid^='conversation-turn'] [data-message-author-role='assistant']",
        ],
    },
    "gemini": {
        "prompt": [
            "rich-textarea .ql-editor",
            "div[contenteditable='true'][role='textbox']",
            "div[contenteditable='true']",
            "textarea",
        ],
        "send": [
            "button.send-button",
            "button[data-test-id='send-button']",
            "button[aria-label='Send message' i]",
            "button[aria-label='Send' i]",
            "button[aria-label='Submit' i]",
        ],
        "stop": [
            "button[aria-label*='Stop']",
            "button[aria-label*='stop']",
            "button[aria-label*='Stop response']",
        ],
        "response": [
            # The answer text only. model-response itself also contains a hidden
            # screen-reader heading, "Gemini said", so it comes last.
            "model-response message-content",
            "message-content",
            ".model-response-text",
            ".markdown",
            "model-response",
        ],
    },
    "claude": {
        "prompt": [
            "div[contenteditable='true'][role='textbox']",
            "div[contenteditable='true']",
            "textarea",
        ],
        "send": [
            "button[aria-label*='Send']",
            "button[aria-label*='send']",
            "button[data-testid*='send']",
        ],
        "stop": [
            "button[aria-label*='Stop']",
            "button[aria-label*='stop']",
        ],
        "response": [
            "[data-testid='assistant-message']",
            "[data-is-streaming]",
            ".font-claude-response",
        ],
    },
}

# Status / error chrome that can sit unchanged while the turn is still open.
# Matched case-insensitively against the whole scraped response (after whitespace collapse).
NON_ANSWER_TEXTS = {
    "searching the web",
    "i seem to be encountering an error",
    "i'm having a hard time fulfilling your request",
    "i am having a hard time fulfilling your request",
    "something went wrong",
    "an error occurred",
}

CAPTCHA_MARKERS = [
    "captcha",
    "verify you are human",
    "verification required",
    "security check",
    "security verification",
    "checking your browser",
    "verify you are not a bot",
    "cloudflare",
]

LOGIN_MARKERS = [
    "log in",
    "login",
    "sign in",
    "continue with google",
    "continue with email",
]

def looks_like_non_answer(text: str) -> bool:
    """True when scraped text is empty or only known in-progress / error chrome.

    Used so "Searching the web" is not treated as a finished Gemini answer.
    """
    if text is None:
        return True
    collapsed = " ".join(text.split()).strip().lower()
    if not collapsed:
        return True
    # Strip trailing sentence punctuation for exact match against the marker set.
    bare = collapsed.rstrip(".…!")
    if bare in NON_ANSWER_TEXTS or collapsed in NON_ANSWER_TEXTS:
        return True
    # Short status line that starts with a marker (UI may add a spinner glyph).
    if len(collapsed) <= 80:
        for marker in NON_ANSWER_TEXTS:
            if collapsed == marker or collapsed.startswith(marker):
                return True
    return False

def response_turn_complete(text: str, generation_busy: bool) -> bool:
    """Whether the collector may accept `text` as the finished answer for this turn."""
    return bool(text) and not looks_like_non_answer(text) and not generation_busy

def _generation_busy(page, selectors: dict) -> bool:
    """True when a Stop control is visible — the app is still generating."""
    stop_selectors = selectors.get("stop") or []
    if not stop_selectors:
        return False
    return _first_visible(page, stop_selectors, timeout_ms=400) is not None

def _first_visible(page, selectors: list[str], timeout_ms: int = 1500):
    for sel in selectors:
        try:
            loc = page.locator(sel)
            if loc.count() and loc.first.is_visible(timeout=timeout_ms):
                return loc.first
        except Exception:
            continue
    return None

def _page_contains(page, markers: list[str]) -> bool:
    try:
        text = page.locator("body").inner_text(timeout=3000).lower()
    except Exception:
        return False
    return any(m in text for m in markers)

def _captcha_detected(page) -> bool:
    from urllib.parse import urlsplit
    url = urlsplit(page.url)
    if url.hostname in {"google.com", "www.google.com", "google.co.il", "www.google.co.il"}:
        # A solved challenge can leave Google's reCAPTCHA anchor in the DOM.
        # Search snippets can also mention CAPTCHA/Cloudflare. Neither means
        # a normal results page is still blocked. A loaded results page ends
        # the wait even if a stale challenge iframe remains after verification.
        try:
            results = page.locator("#search, #rso")
            if any(results.nth(i).is_visible() for i in range(results.count())):
                return False
            puzzle = page.locator("iframe[src*='recaptcha/api2/bframe']")
            if any(puzzle.nth(i).is_visible() for i in range(puzzle.count())):
                return True
        except Exception:
            pass
        if url.path.startswith("/sorry/"):
            return True
        if _page_contains(page, ["our systems have detected unusual traffic"]):
            return True
        try:
            anchor = page.locator("iframe[src*='recaptcha/api2/anchor']")
            return any(anchor.nth(i).is_visible() for i in range(anchor.count()))
        except Exception:
            return False
    if url.path.startswith("/sorry/"):
        return True
    if _page_contains(page, ["our systems have detected unusual traffic"]):
        return True
    if _page_contains(page, CAPTCHA_MARKERS):
        return True
    try:
        frame_urls = " ".join(frame.url.lower() for frame in page.frames)
        if "challenges.cloudflare.com" in frame_urls or "turnstile" in frame_urls:
            return True
    except Exception:
        pass
    for sel in [
        "iframe[src*='challenges.cloudflare.com']",
        "iframe[src*='turnstile']",
        "iframe[title*='challenge' i]",
        "iframe[title*='verification' i]",
        "iframe[src*='recaptcha/api2/anchor']",
    ]:
        try:
            loc = page.locator(sel)
            if loc.count() and loc.first.is_visible(timeout=500):
                return True
        except Exception:
            continue
    return False

def _safe_screenshot(page, path: Path) -> str | None:
    try:
        page.screenshot(path=str(path), full_page=True, timeout=5000)
        return str(path)
    except Exception:
        return None

def _dismiss_cookie_banners(page) -> None:
    # Reject first, so that every condition ends up with the same minimal cookies.
    # Banners appear in France (EU) but not in the US.
    labels = [
        r"^Reject non-essential",  # ChatGPT
        r"^Reject all",            # Google consent page, other sites
        r"^Reject cookies",
        r"^Accept all",
        r"^Accept cookies",
        r"^I agree",
    ]
    for label in labels:
        try:
            button = page.get_by_role("button", name=re.compile(label, re.I)).first
            if button.count() and button.is_visible(timeout=500):
                button.click(timeout=1000)
                return
        except Exception:
            continue

def _wait_for_prompt_or_blocker(page, selectors: list[str], timeout_seconds: int):
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        _dismiss_cookie_banners(page)
        if _captcha_detected(page):
            return None, "captcha"
        prompt_box = _first_visible(page, selectors, timeout_ms=1000)
        if prompt_box is not None:
            return prompt_box, "ready"
        time.sleep(1)

    status = "login_required" if _page_contains(page, LOGIN_MARKERS) else "ui_error"
    return None, status

def _frontmost_app() -> str | None:
    """Bundle id of the app in front (macOS only), so focus can be given back
    after a browser window opens."""
    if sys.platform != "darwin":
        return None
    try:
        asn = subprocess.run(["lsappinfo", "front"], capture_output=True, text=True, timeout=2).stdout.strip()
        out = subprocess.run(["lsappinfo", "info", "-only", "bundleid", asn],
                             capture_output=True, text=True, timeout=2).stdout
        m = re.search(r'"CFBundleIdentifier"="([^"]+)"', out)
        return m.group(1) if m else None
    except Exception:
        return None

def _restore_focus(bundle_id: str | None, browser_cfg: dict) -> None:
    """Reactivate the app that was in front before the browser opened."""
    if not bundle_id or not browser_cfg.get("stay_in_background", True):
        return
    try:
        subprocess.run(["open", "-b", bundle_id], capture_output=True, timeout=5)
    except Exception:
        pass

def _notify_human(provider: str, message: str) -> None:
    from .alerts import notify_human_action
    notify_human_action(provider, message)

def _wait_for_human_captcha(
    page,
    selectors: list[str],
    provider: str,
    browser_cfg: dict,
) -> tuple[object | None, int, int | None]:
    """Return (prompt_box_or_None, wait_ms, timeout_seconds_or_None)."""
    if browser_cfg.get("headless", False) or not browser_cfg.get("human_captcha_enabled", False):
        return None, 0, None

    timeout_seconds = int(browser_cfg.get("human_captcha_timeout_seconds", 300))
    notify_seconds = int(browser_cfg.get("human_captcha_notify_seconds", 30))
    deadline = time.time() + timeout_seconds
    next_notice = 0
    wait_started = time.perf_counter()
    try:
        page.bring_to_front()  # once: the person needs the window, but can still switch away
    except Exception:
        pass

    from .vpn import raise_if_aborted

    while time.time() < deadline:
        raise_if_aborted()
        if time.time() >= next_notice:
            _notify_human(
                provider,
                "complete the visible CAPTCHA/security check in the browser window. "
                "The script will resume automatically when the prompt box appears.",
            )
            next_notice = time.time() + notify_seconds
        _dismiss_cookie_banners(page)
        prompt_box = _first_visible(page, selectors, timeout_ms=1000)
        if prompt_box is not None:
            wait_ms = round((time.perf_counter() - wait_started) * 1000)
            return prompt_box, wait_ms, timeout_seconds
        time.sleep(1)

    wait_ms = round((time.perf_counter() - wait_started) * 1000)
    return None, wait_ms, timeout_seconds

def _wait_for_human_captcha_clear(
    page, provider: str, browser_cfg: dict,
) -> tuple[bool, int, int | None]:
    """Return (cleared, wait_ms, timeout_seconds_or_None)."""
    if browser_cfg.get("headless", False) or not browser_cfg.get("human_captcha_enabled", False):
        return False, 0, None

    from .vpn import raise_if_aborted

    timeout_seconds = int(browser_cfg.get("human_captcha_timeout_seconds", 300))
    notify_seconds = int(browser_cfg.get("human_captcha_notify_seconds", 30))
    deadline = time.time() + timeout_seconds
    next_notice = 0
    wait_started = time.perf_counter()
    try:
        page.bring_to_front()  # once: the person needs the window, but can still switch away
    except Exception:
        pass

    while time.time() < deadline:
        raise_if_aborted()
        if time.time() >= next_notice:
            _notify_human(
                provider,
                "complete the visible CAPTCHA/security check in the browser window. "
                "The script will resume automatically when verification clears.",
            )
            next_notice = time.time() + notify_seconds
        if not _captcha_detected(page):
            wait_ms = round((time.perf_counter() - wait_started) * 1000)
            return True, wait_ms, timeout_seconds
        time.sleep(1)

    wait_ms = round((time.perf_counter() - wait_started) * 1000)
    return False, wait_ms, timeout_seconds

def _fill_prompt(locator, prompt: str):
    tag = locator.evaluate("(el) => el.tagName.toLowerCase()")
    if tag == "textarea":
        locator.fill(prompt)
    else:
        locator.click()
        locator.fill(prompt)


def _submit_prompt(page, prompt_box, provider: str) -> None:
    selectors = PROVIDER_SELECTORS[provider]
    if provider != "gemini":
        send = _first_visible(page, selectors["send"], timeout_ms=1200)
        if send is not None:
            send.click()
        else:
            prompt_box.press("Enter")
        return

    # Gemini's rich editor can accept a fill before its Send control updates.
    # A real key event submits from the editor without relying on that control.
    responses_before = page.locator("model-response").count()
    queries_before = page.locator("user-query").count()
    original_text = prompt_box.inner_text().strip()

    def wait_for_submission():
        try:
            page.wait_for_function(
                """({responses, queries}) =>
                    document.querySelectorAll('user-query').length > queries ||
                    document.querySelectorAll('model-response').length > responses ||
                    [...document.querySelectorAll('button')].some(button =>
                        button.getAttribute('aria-label')?.toLowerCase().startsWith('stop') &&
                        button.getClientRects().length > 0)""",
                arg={"responses": responses_before, "queries": queries_before}, timeout=3000,
            )
            return True
        except PlaywrightTimeoutError:
            return False

    prompt_box.press("Enter")
    if wait_for_submission():
        return
    # Hydration replaces the editor even when the question was never sent.
    # Re-read the current locator; disappearance/clearing alone is not proof.
    if prompt_box.inner_text(timeout=5000).strip() != original_text:
        raise RuntimeError("Gemini submission could not be confirmed; the editor changed. Refusing to send a second question.")
    # Only fall back while the unsent text remains. Never match Send feedback,
    # or click a disabled control and spend the whole answer timeout waiting.
    enabled = [selector + ":not([disabled]):not([aria-disabled='true'])" for selector in selectors["send"]]
    send = _first_visible(page, enabled, timeout_ms=1200)
    if send is not None:
        send.click(timeout=5000)
        if wait_for_submission():
            return
    raise RuntimeError("Gemini did not submit the prompt: the text is still in the editor and no response started.")


def _latest_response(page, selectors: list[str]):
    for sel in selectors:
        try:
            loc = page.locator(sel)
            count = loc.count()
            if count:
                element = loc.nth(count - 1)
                text = element.inner_text(timeout=1500).strip()
                if text:
                    return element, text
        except Exception:
            continue
    return None, ""


def _latest_response_text(page, selectors: list[str]) -> str:
    return _latest_response(page, selectors)[1]


def _response_sources(provider: str, element, page, citation_capture=None):
    sources = sources_from_element(element, page.url)
    found = read = 0
    failures = []
    groups = []
    if provider == "chatgpt":
        # Some ChatGPT layouts expose citations as popovers containing source
        # buttons, with no href. Read each button's real navigation target
        # without visiting the publisher or adding unrelated page links.
        controls = element.locator("[data-d-component='popover-trigger'][aria-haspopup='dialog'][aria-controls]")
        found = controls.count()
        network = citation_capture.for_element(element) if citation_capture is not None else None
        if network is not None:
            groups = network["groups"]
            read = sum(group["complete"] for group in groups)
            sources.extend(source for group in groups for source in group["sources"])
            return normalize_sources(sources), {
                "method": "browser_response", "format": "chatgpt-conversation-source-v2",
                "controls_found": found, "controls_read": read,
                "complete": read == len(groups) == found,
                "message_id": network["message_id"], "revision": network["revision"],
                "groups": groups,
            }
        for index in range(found):
            control = controls.nth(index)
            try:
                control.hover(timeout=3000)
                panel = page.locator("[id=" + json.dumps(control.get_attribute("aria-controls")) + "]")
                panel.wait_for(state="visible", timeout=4000)
                links = sources_from_element(panel, page.url)
                buttons = panel.get_by_role("button", name=re.compile(r"^Open "))
                all_buttons_read = True
                for button_index in range(buttons.count()):
                    page.mouse.move(0, 0)
                    control.hover(timeout=3000)
                    panel.wait_for(state="visible", timeout=4000)
                    button = buttons.nth(button_index)
                    title = button.get_attribute("aria-label") or ""
                    urls = []
                    def capture_navigation(route):
                        if route.request.is_navigation_request():
                            urls.append(route.request.url)
                            route.abort()
                        else:
                            route.fallback()
                    page.context.route("**/*", capture_navigation)
                    popup = None
                    try:
                        with page.expect_popup(timeout=3000) as opened:
                            button.click(timeout=3000)
                        popup = opened.value
                        # The popup event follows its initial navigation request,
                        # which is intercepted above rather than sent to the source.
                        targets = normalize_sources([{"title": title.removeprefix("Open "), "url": url} for url in urls])
                        links.extend(targets)
                        all_buttons_read = all_buttons_read and bool(targets)
                    except Exception as error:
                        all_buttons_read = False
                        failures.append({"control": index, "source_button": button_index,
                                         "error": f"{type(error).__name__}: {str(error)[:500]}"})
                    finally:
                        if popup is not None:
                            popup.close()
                        page.context.unroute("**/*", capture_navigation)
                sources.extend(links)
                groups.append({"id": f"popover-{index}", "kind": "popover", "sources": normalize_sources(links),
                               "complete": bool(links) and all_buttons_read})
                if links and all_buttons_read:
                    read += 1
            except Exception as error:
                failures.append({"control": index, "error": f"{type(error).__name__}: {str(error)[:500]}"})
                continue
    if provider == "gemini":
        # Gemini keeps citation URLs out of the DOM until a source chip opens.
        chips = element.locator("source-inline-chip")
        found = chips.count()
        for index in range(found):
            chip = chips.nth(index)
            button = chip.locator("button").first
            try:
                button.hover(timeout=3000)
                chip.locator("inline-source-card a[href]").first.wait_for(state="attached", timeout=4000)
                links = sources_from_element(chip, page.url)
                if links:
                    sources.extend(links)
                    read += 1
                    groups.append({"id": f"inline-{index}", "kind": "inline", "sources": links, "complete": True})
            except Exception as error:
                failures.append({"control": index, "error": f"{type(error).__name__}: {str(error)[:500]}"})
                continue
    if provider in {"chatgpt", "gemini"}:
        page.mouse.move(0, 0)
    coverage = {"controls_found": found, "controls_read": read, "complete": found == read}
    if groups:
        coverage["groups"] = groups
    if citation_capture is not None:
        coverage["browser_response"] = {"responses_seen": citation_capture.response_count,
                                        "messages_seen": len(citation_capture.messages),
                                        "errors": citation_capture.errors}
    if failures:
        coverage["failures"] = failures
    return normalize_sources(sources), coverage


def _save_browser_evidence(result, page, element, *, provider, prompt, browser_cfg,
                           screenshot_dir, observation_id, answer_complete=False):
    from .evidence import capture_evidence
    metadata = dict(result.metadata or {})
    metadata["answer_complete"] = answer_complete
    metadata["evidence"] = capture_evidence(
        page, element, provider=provider, prompt=prompt, response=result.response,
        sources=result.sources, status=result.status, source_capture=metadata.get("source_capture"),
        browser_cfg=browser_cfg, screenshot_dir=screenshot_dir, observation_id=observation_id,
    )
    if metadata["evidence"]["status"] == "error" and result.status == "ok":
        result.status = "ui_error"
        result.error = "Browser evidence could not be saved or validated; the answer has been retained."
    result.metadata = metadata
    return result

def check_browser_provider(
    provider: str,
    browser_cfg: dict,
    provider_cfg: dict,
    locale: str,
    timezone_id: str,
    screenshot_dir: Path,
    observation_id: str,
) -> BrowserCheckResult:
    browser_cfg = provider_settings(browser_cfg, provider_cfg)
    started = time.perf_counter()
    screenshot_dir.mkdir(parents=True, exist_ok=True)
    selectors = PROVIDER_SELECTORS[provider]
    timeout_ms = int(browser_cfg.get("timeout_seconds", 120) * 1000)
    url = provider_cfg["url"]
    prompt_wait_seconds = int(browser_cfg.get("prompt_wait_seconds", 20))

    try:
        front_app = _frontmost_app()
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=browser_cfg.get("headless", False))
            context = browser.new_context(
                locale=locale,
                timezone_id=timezone_id,
                permissions=[],
            )
            page = context.new_page()
            _restore_focus(front_app, browser_cfg)
            page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            _restore_focus(front_app, browser_cfg)

            prompt_box, status = _wait_for_prompt_or_blocker(
                page,
                selectors["prompt"],
                prompt_wait_seconds,
            )
            manual_intervention = False
            if prompt_box is None and status == "captcha":
                prompt_box, _, _ = _wait_for_human_captcha(
                    page, selectors["prompt"], provider, browser_cfg,
                )
                manual_intervention = prompt_box is not None
            if prompt_box is None:
                shot = screenshot_dir / f"{observation_id}_{status}.png"
                screenshot_path = None
                if browser_cfg.get("screenshot_failures", True):
                    screenshot_path = _safe_screenshot(page, shot)
                context.close()
                browser.close()
                return BrowserCheckResult(
                    provider,
                    url,
                    status,
                    round((time.perf_counter() - started) * 1000),
                    error=(
                        "CAPTCHA / human verification detected; human intervention timed out or is disabled."
                        if status == "captcha"
                        else "No usable prompt box found."
                    ),
                    screenshot_path=screenshot_path,
                    manual_intervention=manual_intervention,
                )

            context.close()
            browser.close()
            return BrowserCheckResult(
                provider,
                url,
                "ready",
                round((time.perf_counter() - started) * 1000),
                manual_intervention=manual_intervention,
            )

    except PlaywrightTimeoutError as e:
        return BrowserCheckResult(
            provider,
            url,
            "timeout",
            round((time.perf_counter() - started) * 1000),
            error=str(e),
        )
    except Exception as e:
        return BrowserCheckResult(
            provider,
            url,
            "error",
            round((time.perf_counter() - started) * 1000),
            error=str(e),
        )

def collect_browser(
    provider: str,
    prompt: str,
    browser_cfg: dict,
    provider_cfg: dict,
    locale: str,
    timezone_id: str,
    screenshot_dir: Path,
    observation_id: str,
) -> BrowserResult:
    """Ask one question in a fresh browser. On a retryable failure, ask again in
    another fresh browser, up to browser.retries times. `attempts` records how many
    browsers were used; the error field keeps the earlier failures."""
    browser_cfg = provider_settings(browser_cfg, provider_cfg)
    retries = int(browser_cfg.get("retries", 1))
    earlier = []
    for attempt in range(1, retries + 2):
        result = _collect_browser_once(
            provider, prompt, browser_cfg, provider_cfg, locale, timezone_id, screenshot_dir,
            observation_id if attempt == 1 else f"{observation_id}_attempt{attempt}",
        )
        result.attempts = attempt
        capture = (result.metadata or {}).get("source_capture")
        if result.response and ((capture is not None and not capture.get("complete", False))
                                or (result.metadata or {}).get("answer_complete")):
            # A finished answer with unreadable citations must be preserved.
            # Re-asking would replace the study observation with another answer.
            break
        if result.status not in RETRY_STATUSES or attempt == retries + 1:
            break
        first_line = (result.error or "").splitlines()[0] if result.error else ""
        earlier.append(f"attempt {attempt}: {result.status}: {first_line}")
        print(f"RETRY {provider}: attempt {attempt} ended with {result.status}", flush=True)
    if earlier:
        result.error = "; ".join(earlier + ([f"attempt {result.attempts}: {result.error}"] if result.error else []))
    return result

def _collect_browser_once(
    provider: str,
    prompt: str,
    browser_cfg: dict,
    provider_cfg: dict,
    locale: str,
    timezone_id: str,
    screenshot_dir: Path,
    observation_id: str,
) -> BrowserResult:
    if provider == "google_search":
        from .google_search import collect_google_search
        return collect_google_search(
            prompt, browser_cfg, provider_cfg, locale, timezone_id,
            screenshot_dir, observation_id,
        )
    started = time.perf_counter()
    screenshot_dir.mkdir(parents=True, exist_ok=True)
    selectors = PROVIDER_SELECTORS[provider]
    timeout_ms = int(browser_cfg.get("timeout_seconds", 120) * 1000)
    prompt_wait_seconds = int(browser_cfg.get("prompt_wait_seconds", 20))
    page = None

    def save(result, element=None, answer_complete=False):
        return _save_browser_evidence(result, page, element, provider=provider, prompt=prompt,
                                     browser_cfg=browser_cfg, screenshot_dir=screenshot_dir,
                                     observation_id=observation_id, answer_complete=answer_complete)

    try:
        front_app = _frontmost_app()
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=browser_cfg.get("headless", False))
            context = browser.new_context(
                locale=locale,
                timezone_id=timezone_id,
                permissions=[],
            )
            page = context.new_page()
            citation_capture = None
            if provider == "chatgpt":
                from .chatgpt_citations import ChatGPTCitationCapture
                citation_capture = ChatGPTCitationCapture(page)
            _restore_focus(front_app, browser_cfg)
            page.goto(provider_cfg["url"], wait_until="domcontentloaded", timeout=timeout_ms)
            _restore_focus(front_app, browser_cfg)

            prompt_box, status = _wait_for_prompt_or_blocker(
                page,
                selectors["prompt"],
                prompt_wait_seconds,
            )
            manual_intervention = False
            human_wait_ms = 0
            human_timeout_seconds = None
            if prompt_box is None and status == "captcha":
                prompt_box, wait_ms, timeout_s = _wait_for_human_captcha(
                    page, selectors["prompt"], provider, browser_cfg,
                )
                human_wait_ms += wait_ms
                if timeout_s is not None:
                    human_timeout_seconds = timeout_s
                manual_intervention = prompt_box is not None
            if prompt_box is None:
                shot = screenshot_dir / f"{observation_id}_{status}.png"
                screenshot_path = None
                if browser_cfg.get("screenshot_failures", True):
                    screenshot_path = _safe_screenshot(page, shot)
                result = save(BrowserResult(
                    "", None,
                    round((time.perf_counter() - started) * 1000),
                    status=status,
                    error=(
                        "CAPTCHA / human verification detected; human intervention timed out or is disabled."
                        if status == "captcha"
                        else "No usable prompt box found."
                    ),
                    screenshot_path=screenshot_path,
                    manual_intervention=manual_intervention,
                    human_wait_ms=human_wait_ms or None,
                    human_timeout_seconds=human_timeout_seconds,
                ))
                context.close()
                browser.close()
                return result

            _dismiss_cookie_banners(page)  # a late banner can cover the prompt box
            _fill_prompt(prompt_box, prompt)
            _submit_prompt(page, prompt_box, provider)

            time.sleep(browser_cfg.get("pause_after_submit_seconds", 2))

            deadline = time.time() + browser_cfg.get("timeout_seconds", 120)
            last = ""
            last_element = None
            stable_count = 0
            while time.time() < deadline:
                if _captcha_detected(page):
                    cleared, wait_ms, timeout_s = _wait_for_human_captcha_clear(
                        page, provider, browser_cfg,
                    )
                    human_wait_ms += wait_ms
                    if timeout_s is not None:
                        human_timeout_seconds = timeout_s
                    if cleared:
                        manual_intervention = True
                        deadline = time.time() + browser_cfg.get("timeout_seconds", 120)
                    else:
                        shot = screenshot_dir / f"{observation_id}_captcha.png"
                        screenshot_path = None
                        if browser_cfg.get("screenshot_failures", True):
                            screenshot_path = _safe_screenshot(page, shot)
                        result = save(BrowserResult(
                            "", None,
                            round((time.perf_counter() - started) * 1000),
                            status="captcha",
                            error="CAPTCHA / human verification detected after submit; human intervention timed out or is disabled.",
                            screenshot_path=screenshot_path,
                            manual_intervention=manual_intervention,
                            human_wait_ms=human_wait_ms or None,
                            human_timeout_seconds=human_timeout_seconds,
                        ))
                        context.close()
                        browser.close()
                        return result

                response_element, current = _latest_response(page, selectors["response"])
                last_element = response_element
                busy = _generation_busy(page, selectors)
                # Status chrome ("Searching the web") can sit unchanged; do not
                # count it toward stability or accept it as an answer.
                if looks_like_non_answer(current) or busy:
                    stable_count = 0
                    last = current
                elif current and current == last:
                    stable_count += 1
                else:
                    stable_count = 0
                    last = current

                if response_turn_complete(current, busy) and stable_count >= 2:
                    sources, source_capture = _response_sources(provider, response_element, page, citation_capture)
                    result = save(BrowserResult(
                        current,
                        None,  # consumer-app model is intentionally not guessed
                        round((time.perf_counter() - started) * 1000),
                        manual_intervention=manual_intervention,
                        sources=sources,
                        metadata={"source_capture": source_capture},
                        status="ok" if source_capture["complete"] else "ui_error",
                        error=None if source_capture["complete"] else "Some citation controls could not be read; source URLs are incomplete.",
                        human_wait_ms=human_wait_ms or None,
                        human_timeout_seconds=human_timeout_seconds,
                    ), response_element, answer_complete=True)
                    context.close()
                    browser.close()
                    return result
                time.sleep(2)

            shot = screenshot_dir / f"{observation_id}_timeout.png"
            screenshot_path = None
            if browser_cfg.get("screenshot_failures", True):
                screenshot_path = _safe_screenshot(page, shot)
            sources = sources_from_element(last_element, page.url) if last_element is not None else []
            if looks_like_non_answer(last):
                preview = " ".join(last.split())[:80] if last else ""
                error = (
                    f"Timed out; response stayed on non-answer status text"
                    + (f": {preview}" if preview else ".")
                )
            else:
                error = "Timed out waiting for a stable finished response."
            result = save(BrowserResult(
                last,  # keep status chrome in the field for debugging; status is timeout
                None,
                round((time.perf_counter() - started) * 1000),
                status="timeout",
                error=error,
                screenshot_path=screenshot_path,
                manual_intervention=manual_intervention,
                sources=sources,
                human_wait_ms=human_wait_ms or None,
                human_timeout_seconds=human_timeout_seconds,
            ), last_element)
            context.close()
            browser.close()
            return result

    except PlaywrightTimeoutError as e:
        return save(BrowserResult(
            "", None,
            round((time.perf_counter() - started) * 1000),
            status="timeout",
            error=str(e),
        ))
    except Exception as e:
        return save(BrowserResult(
            "", None,
            round((time.perf_counter() - started) * 1000),
            status="error",
            error=str(e),
        ))
