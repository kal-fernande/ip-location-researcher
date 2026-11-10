\
from __future__ import annotations
import re
from difflib import SequenceMatcher
from pathlib import Path
import pandas as pd

from itertools import combinations

from .config import load_config, resolve_path

# Locations and their focus terms come from `locations:` in config.yaml, so
# changing or adding a country needs no change here.

def normalize(text: str) -> str:
    text = (text or "").lower()
    text = re.sub(r"\s+", " ", text)
    return text.strip()

def similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, normalize(a), normalize(b)).ratio()

def term_score(text: str, terms: list[str]) -> int:
    t = normalize(text)
    return sum(t.count(term) for term in terms)

def inferred_focus(text: str, focus_terms: dict[str, list[str]]) -> str:
    """The location whose focus terms the answer mentions most; "ambiguous" if
    none, "mixed" if several tie."""
    scores = {loc: term_score(text, terms) for loc, terms in focus_terms.items()}
    best = max(scores.values(), default=0)
    if best == 0:
        return "ambiguous"
    leaders = [loc for loc, score in scores.items() if score == best]
    return leaders[0] if len(leaders) == 1 else "mixed"

def _canonical_provider(row_provider: str) -> str:
    return {
        "openai": "chatgpt",
        "anthropic": "claude",
        "gemini": "gemini",
        "chatgpt": "chatgpt",
        "claude": "claude",
    }.get(row_provider, row_provider)

def analyze() -> None:
    from .study import apply_active_study

    cfg = apply_active_study()
    csv_path = resolve_path(cfg["results"]["csv_path"])
    out_dir = resolve_path(cfg["results"]["analysis_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)

    if not csv_path.exists():
        raise SystemExit(f"No results found at {csv_path}")

    df = pd.read_csv(csv_path).fillna("")
    df["canonical_provider"] = df["provider"].map(_canonical_provider)
    locations = list(cfg["locations"])
    focus_terms = {loc: [t.lower() for t in lc.get("focus_terms", [])] for loc, lc in cfg["locations"].items()}
    df["focus"] = df["response"].map(lambda text: inferred_focus(text, focus_terms))
    ok = df[df["status"] == "ok"].copy()

    # 1) LOCATION EFFECT: each pair of configured locations, within the same
    # collector/provider/language/day.
    location_pairs = []
    group_cols = ["day", "question_id", "collector", "canonical_provider", "language"]
    for keys, g in ok.groupby(group_cols):
        by_loc = {row["vpn_location"]: row for _, row in g.iterrows()}
        for loc_a, loc_b in combinations(locations, 2):
            if loc_a not in by_loc or loc_b not in by_loc:
                continue
            a, b = by_loc[loc_a], by_loc[loc_b]
            location_pairs.append({
                **dict(zip(group_cols, keys)),
                "location_a": loc_a,
                "location_b": loc_b,
                "response_a": a["response"],
                "response_b": b["response"],
                "text_similarity": similarity(a["response"], b["response"]),
                "focus_a": a["focus"],
                "focus_b": b["focus"],
                "focus_changed": a["focus"] != b["focus"],
                "model_a": a["model_observed"],
                "model_b": b["model_observed"],
            })
    loc_df = pd.DataFrame(location_pairs)
    loc_df.to_csv(out_dir / "location_pairs.csv", index=False)

    # 2) COLLECTION-METHOD EFFECT: API vs browser at the same VPN location.
    method_pairs = []
    method_group = ["day", "question_id", "canonical_provider", "language", "vpn_location"]
    for keys, g in ok.groupby(method_group):
        by_collector = {row["collector"]: row for _, row in g.iterrows()}
        if "api" not in by_collector or "browser" not in by_collector:
            continue
        a, b = by_collector["api"], by_collector["browser"]
        method_pairs.append({
            **dict(zip(method_group, keys)),
            "api_response": a["response"],
            "browser_response": b["response"],
            "text_similarity": similarity(a["response"], b["response"]),
            "api_focus": a["focus"],
            "browser_focus": b["focus"],
            "focus_changed": a["focus"] != b["focus"],
            "api_model": a["model_observed"],
            "browser_model": b["model_observed"],
        })
    method_df = pd.DataFrame(method_pairs)
    method_df.to_csv(out_dir / "method_pairs.csv", index=False)

    # 3) Summaries.
    summaries = []
    if not loc_df.empty:
        for keys, g in loc_df.groupby(["location_a", "location_b", "collector", "canonical_provider", "language"]):
            summaries.append({
                "comparison": f"{keys[0]}_vs_{keys[1]}",
                "collector": keys[2],
                "provider": keys[3],
                "language": keys[4],
                "n_pairs": len(g),
                "mean_text_similarity": round(g["text_similarity"].mean(), 4),
                "focus_change_rate": round(g["focus_changed"].mean(), 4),
            })
    if not method_df.empty:
        for keys, g in method_df.groupby(["canonical_provider", "language", "vpn_location"]):
            summaries.append({
                "comparison": "API_vs_browser",
                "collector": "cross_method",
                "provider": keys[0],
                "language": keys[1],
                "vpn_location": keys[2],
                "n_pairs": len(g),
                "mean_text_similarity": round(g["text_similarity"].mean(), 4),
                "focus_change_rate": round(g["focus_changed"].mean(), 4),
            })

    summary_df = pd.DataFrame(summaries)
    summary_df.to_csv(out_dir / "summary.csv", index=False)

    status_summary = (
        df.groupby(["collector", "provider", "status"])
          .size()
          .reset_index(name="count")
    )
    status_summary.to_csv(out_dir / "status_summary.csv", index=False)

    report_lines = [
        "IP LOCATION EXPERIMENT — AUTOMATED SUMMARY",
        "=" * 48,
        f"Total rows: {len(df)}",
        f"Successful rows: {len(ok)}",
        "",
        "Status counts:",
        status_summary.to_string(index=False),
        "",
        "NOTE:",
        "- text_similarity is descriptive, not a causal test.",
        "- browser vs API can differ because the consumer app may use a different model/configuration.",
        "- focus classification counts each location's focus_terms from config.yaml; manually validate it.",
        "- with only one sample per condition/day, stochastic model variation remains a confound.",
    ]
    if not summary_df.empty:
        report_lines += ["", "Pairwise summary:", summary_df.to_string(index=False)]
    (out_dir / "report.txt").write_text("\n".join(report_lines), encoding="utf-8")
    print("\n".join(report_lines))
