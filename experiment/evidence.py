"""Uniform, local evidence bundles for browser observations and offline checks."""
from __future__ import annotations

import hashlib
import html
import json
import re
import csv
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

from .config import ROOT
from .links import normalize_sources, observation_sources, sources_from_text

FILES = ("page.html", "page.mhtml", "page.png", "answer.html", "answer.txt",
         "observation.json", "source-evidence.json", "review.html")


def _path_string(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return str(path.resolve())


def _json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


class _Anchors(HTMLParser):
    def __init__(self):
        super().__init__()
        self.sources = []
        self.chatgpt_controls = 0
        self.gemini_controls = 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "a":
            self.sources.append({"url": attrs.get("href")})
        if attrs.get("data-d-component") == "popover-trigger" and attrs.get("aria-haspopup") == "dialog" and attrs.get("aria-controls"):
            self.chatgpt_controls += 1
        if tag == "source-inline-chip":
            self.gemini_controls += 1


def _support_urls(answer_html, response, capture, page_url):
    anchors = _Anchors()
    anchors.feed(answer_html)
    sources = normalize_sources(anchors.sources, page_url) + sources_from_text(response)
    for group in capture.get("groups", []):
        sources.extend(normalize_sources(group.get("sources")))
    sources.extend(normalize_sources(capture.get("links")))
    supported = {source["url"] for source in sources}
    for redirect in capture.get("redirects", []):
        if redirect.get("redirect_url") in supported:
            supported.add(redirect.get("source_url"))
    return supported


def _review(record):
    esc = html.escape
    sources = "\n".join(
        f'<li><a href="{esc(source["url"], quote=True)}" rel="noreferrer">{esc(source["title"] or source["url"])}</a><br><code>{esc(source["url"])}</code></li>'
        for source in record["sources"]
    ) or "<li>No source URLs were captured.</li>"
    capture = record["source_capture"]
    coverage = f'{capture.get("controls_read", "unknown")} / {capture.get("controls_found", "unknown")}'
    return f'''<!doctype html>
<html lang="en"><meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; img-src 'self' data:; base-uri 'none'; form-action 'none'">
<title>Saved observation: {esc(record['provider'])}</title>
<style>body{{font:17px/1.55 system-ui,sans-serif;max-width:1000px;margin:36px auto;padding:0 22px;color:#19212c;background:#fff}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;border:1px solid #ccd3dd;padding:18px;border-radius:8px;font:inherit}}code{{overflow-wrap:anywhere;font-size:14px}}li{{margin:14px 0}}a{{color:#1757ad}}.meta{{color:#4a5565}}</style>
<h1>Saved {esc(record['provider'])} observation</h1>
<p class="meta">{esc(record['captured_at'])} · {esc(record['status'])} · citation controls: {coverage}</p>
<p>This is an extracted review copy. Open the original page archive or screenshot to compare it with the captured website.</p>
<p><a href="page.mhtml">Original page archive (Chrome/Chromium)</a> · <a href="page.png">Screenshot</a> · <a href="page.html" download>Download raw rendered HTML</a> · <a href="observation.json">Observation JSON</a></p>
<h2>Question</h2><pre id="prompt" dir="auto">{esc(record['prompt'])}</pre>
<h2>Recorded answer</h2><pre id="answer" dir="auto">{esc(record['response'])}</pre>
<h2>Captured sources</h2><ol>{sources}</ol>
<p>Source links open the publisher's website and require internet access. The archive preserves the captured page state; it does not resume the original chat or create a new answer.</p>
</html>'''


def capture_evidence(page, element, *, provider, prompt, response, sources, status,
                     source_capture, browser_cfg, screenshot_dir, observation_id):
    if not browser_cfg.get("save_evidence", True):
        return {"version": 1, "status": "disabled"}
    root = Path(browser_cfg.get("evidence_dir") or (screenshot_dir / "evidence"))
    name = re.sub(r"[^A-Za-z0-9_.-]", "_", observation_id)
    directory = root / name
    result = {"version": 1, "status": "error", "directory": _path_string(directory),
              "manifest": _path_string(directory / "manifest.json"),
              "review_html": _path_string(directory / "review.html"),
              "archive": _path_string(directory / "page.mhtml"),
              "screenshot": _path_string(directory / "page.png"), "errors": []}
    try:
        directory.mkdir(parents=True, exist_ok=False)
        answer_html = element.evaluate("el => el.outerHTML") if element is not None else ""
        record = {"version": 1, "observation_id": observation_id, "provider": provider,
                  "captured_at": datetime.now(timezone.utc).isoformat(), "page_url": page.url,
                  "prompt": prompt, "response": response, "sources": normalize_sources(sources),
                  "status": status, "source_capture": source_capture or {}}
        (directory / "page.html").write_text(page.content(), encoding="utf-8")
        (directory / "answer.html").write_text(answer_html, encoding="utf-8")
        (directory / "answer.txt").write_text(response, encoding="utf-8")
        _json(directory / "observation.json", record)
        _json(directory / "source-evidence.json", {"page_url": page.url, "capture": source_capture or {}})
        (directory / "review.html").write_text(_review(record), encoding="utf-8")
        session = page.context.new_cdp_session(page)
        try:
            archive = session.send("Page.captureSnapshot", {"format": "mhtml"})["data"]
            (directory / "page.mhtml").write_bytes(archive.encode("utf-8"))
        finally:
            session.detach()
        page.screenshot(path=str(directory / "page.png"), full_page=True, timeout=15000)
        manifest = {"version": 1, "provider": provider, "observation_id": observation_id,
                    "files": {name: hashlib.sha256((directory / name).read_bytes()).hexdigest() for name in FILES}}
        _json(directory / "manifest.json", manifest)
        errors = validate_evidence(result)
        if errors:
            result["errors"] = errors
        else:
            result["status"] = "ok"
    except Exception as error:
        result["errors"].append(f"{type(error).__name__}: {str(error)[:300]}")
    return result


def validate_evidence(evidence: dict, observation: dict | None = None) -> list[str]:
    if not evidence.get("manifest"):
        return ["No saved evidence manifest (capture disabled or unavailable)"]
    path = Path(evidence["manifest"])
    path = path if path.is_absolute() else ROOT / path
    errors = []
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
        if manifest.get("version") != 1 or set(manifest.get("files", {})) != set(FILES):
            return ["Unsupported or incomplete evidence manifest"]
        directory = path.parent
        for name in FILES:
            target = directory / name
            if not target.is_file() or hashlib.sha256(target.read_bytes()).hexdigest() != manifest["files"][name]:
                errors.append(f"Missing or changed file: {name}")
        if errors:
            return errors
        saved = json.loads((directory / "observation.json").read_text(encoding="utf-8"))
        proof = json.loads((directory / "source-evidence.json").read_text(encoding="utf-8"))
        if manifest["provider"] != saved["provider"] or manifest["observation_id"] != saved["observation_id"]:
            errors.append("Manifest belongs to another observation")
        if proof["page_url"] != saved["page_url"] or proof["capture"] != saved["source_capture"]:
            errors.append("Source evidence differs from the captured observation")
        if (directory / "answer.txt").read_text(encoding="utf-8") != saved["response"]:
            errors.append("Answer text differs from the saved observation")
        answer_html = (directory / "answer.html").read_text(encoding="utf-8")
        supported = _support_urls(answer_html,
                                  saved["response"], proof["capture"], proof["page_url"])
        missing = [s["url"] for s in saved["sources"] if s["url"] not in supported]
        if missing:
            errors.append(f"{len(missing)} source URL(s) have no captured citation/answer evidence")
        if observation is not None:
            for key in ("provider", "prompt", "response"):
                if observation.get(key) != saved[key]:
                    errors.append(f"Exported {key} differs from captured observation")
            if {s["url"] for s in observation_sources(observation)} != {s["url"] for s in saved["sources"]}:
                errors.append("Exported source URLs differ from captured observation")
            expected = observation.get("observation_id")
            identifiers = {expected, f"{expected}_attempt{observation.get('attempts', 1)}"}
            if expected and saved["observation_id"] not in identifiers:
                errors.append("Evidence belongs to another observation")
        capture = saved["source_capture"]
        if saved["status"] == "ok" and capture.get("complete") is not True:
            errors.append("Source-control coverage is incomplete or unknown")
        if saved["status"] == "ok" and saved["provider"] in {"chatgpt", "gemini"}:
            markup = _Anchors()
            markup.feed(answer_html)
            controls = markup.chatgpt_controls if saved["provider"] == "chatgpt" else markup.gemini_controls
            if controls != capture.get("controls_found"):
                errors.append("Captured answer markup has a different citation-control count")
    except (OSError, ValueError, KeyError, TypeError) as error:
        errors.append(f"Evidence cannot be read: {type(error).__name__}")
    return errors


def validate_run(path: Path, csv_path: Path | None = None) -> dict:
    record = json.loads(path.read_text(encoding="utf-8"))
    if csv_path is None and path.parent.name == "runs":
        csv_path = path.parent.parent / "results.csv"
    exports = {}
    export_errors = []
    if csv_path is not None:
        try:
            with csv_path.open(newline="", encoding="utf-8") as stream:
                exports["CSV"] = list(csv.DictReader(stream))
            workbook = csv_path.with_suffix(".xlsx")
            if workbook.exists():
                import pandas as pd
                exports["Excel"] = pd.read_excel(workbook, dtype=str, keep_default_na=False).to_dict("records")
        except (OSError, ValueError) as error:
            export_errors.append(f"Saved table cannot be read: {type(error).__name__}")
    results = []
    for location in record.get("locations", []):
        for observation in location.get("browser", []):
            evidence = (observation.get("metadata") or {}).get("evidence") or {}
            errors = validate_evidence(evidence, observation)
            errors.extend(export_errors)
            for name, rows in exports.items():
                matches = [row for row in rows if row.get("observation_id") == observation.get("observation_id")]
                if len(matches) != 1:
                    errors.append(f"{name}: expected one row for this observation id")
                    continue
                row = matches[0]
                if any(row.get(key) != observation.get(key) for key in ("provider", "prompt", "response", "status")):
                    errors.append(f"{name}: answer fields differ from run JSON")
                if {s["url"] for s in observation_sources(row)} != {s["url"] for s in observation_sources(observation)}:
                    errors.append(f"{name}: source URLs differ from run JSON")
            if evidence.get("status") != "ok":
                errors.append("Evidence capture did not complete")
            if observation.get("status") != "ok":
                errors.append("Collection status: " + str(observation.get("status")))
            results.append({"location": location["location"], "provider": observation["provider"],
                            "prompt_id": observation.get("prompt_id"), "ok": not errors, "errors": errors})
    return {"ok": bool(results) and all(item["ok"] for item in results),
            "observations": len(results), "results": results}
