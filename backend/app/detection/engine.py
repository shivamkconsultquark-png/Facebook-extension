"""Platform-agnostic scam-risk engine.

Scoring strategy (documented in docs/DETECTION.md):
  1. Rule signals give a 0-100 sub-score per category (sum of matched rule weights, capped).
     In a warning/discussion context the text categories are multiplied by 0.4.
  2. Link findings give the "links" sub-score (max per-URL risk).
  3. rules_score = 100 * (1 - prod(1 - sub/100))  — independent-evidence combination, so one
     moderate signal stays "suspicious" and several strong ones reach "likely scam".
  4. With an LLM available: score = 0.65 * llm + 0.35 * rules_score.
  5. A Safe Browsing match floors the score at 90.
Scores are risk estimates, not calibrated probabilities, so no confidence value is reported.
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import asdict, dataclass, field

from ..config import settings
from . import llm, reputation
from .rules import (CATEGORY_LABELS, RULESET_VERSION, UNDAMPED_CATEGORIES, is_warning_context,
                    match_author, match_rules)
from .urls import analyze_urls, urls_from_text

WARNING_DAMPING = 0.4
LLM_WEIGHT = 0.65

# (suspicious_from, likely_scam_from) — normal matches the Twitter page's 35 / 70 bands.
SENSITIVITY_THRESHOLDS = {"low": (45, 80), "normal": (35, 70), "high": (25, 60)}
MIN_TEXT_CHARS = 15


@dataclass
class ContentItem:
    text: str = ""
    urls: list[str] = field(default_factory=list)
    author_name: str | None = None


@dataclass
class Analysis:
    status: str                      # ok | insufficient_content
    score: int | None
    scoring_method: str              # rules | llm+rules | none
    reason: str
    indicators: list[dict]
    category_scores: dict[str, int]
    link_domains: list[str]
    engine_version: str
    warning_context: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


# Facebook's own truncation / translation controls, which the extension may capture.
_FB_CONTROLS = re.compile(r"(?:…|\.\.\.)?\s*\b(?:See more|See less|See translation|Rate this translation)\b", re.I)


def normalise_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "")
    text = _FB_CONTROLS.sub(" ", text)
    # Strip zero-width characters scammers use to dodge keyword filters.
    text = re.sub(r"[​-‏⁠﻿]", "", text)
    return re.sub(r"\s+", " ", text).strip()


def engine_version() -> str:
    return RULESET_VERSION + (f"+llm:{settings.gemini_model}" if llm.available() else "")


def content_hash(item: ContentItem, instructions: str | None) -> str:
    payload = json.dumps({
        "t": normalise_text(item.text).lower(),
        "u": sorted(set(item.urls)),
        "a": (item.author_name or "").strip().lower(),
        "i": hashlib.sha256((instructions or "").encode()).hexdigest() if llm.available() else "",
    }, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()


def category_for(score: int | None, sensitivity: str = "normal") -> str:
    if score is None:
        return "unable_to_verify"
    suspicious, scam = SENSITIVITY_THRESHOLDS.get(sensitivity, SENSITIVITY_THRESHOLDS["normal"])
    if score >= scam:
        return "likely_scam"
    if score >= suspicious:
        return "suspicious"
    return "likely_legitimate"


def combine(sub_scores: dict[str, int]) -> int:
    remaining = 1.0
    for s in sub_scores.values():
        remaining *= 1 - min(max(s, 0), 100) / 100
    return round(100 * (1 - remaining))


def analyze(item: ContentItem, instructions: str | None = None, use_llm: bool = True) -> Analysis:
    text = normalise_text(item.text)
    all_urls = list(dict.fromkeys([*item.urls, *urls_from_text(text)]))
    url_findings = analyze_urls(all_urls)
    domains = sorted({f.domain for f in url_findings if f.domain})
    version = engine_version()

    if len(text) < MIN_TEXT_CHARS and not url_findings:
        return Analysis("insufficient_content", None, "none",
                        "Not enough visible text or links to assess", [], {}, domains, version)

    warning = is_warning_context(text)
    sub: dict[str, int] = {}
    indicators: list[dict] = []
    for hit in [*match_rules(text), *match_author(item.author_name)]:
        sub[hit.category] = min(100, sub.get(hit.category, 0) + hit.weight)
        indicators.append({"category": hit.category, "label": hit.label,
                           "weight": hit.weight, "evidence": hit.evidence})
    if warning:
        sub = {k: v if k in UNDAMPED_CATEGORIES else round(v * WARNING_DAMPING) for k, v in sub.items()}

    risky_links = [f for f in url_findings if f.risk > 0]
    if risky_links:
        sub["links"] = max(f.risk for f in risky_links)
        for f in sorted(risky_links, key=lambda f: -f.risk)[:3]:
            indicators.append({"category": "links", "label": "; ".join(f.reasons),
                               "weight": f.risk, "evidence": f.domain})

    threats = reputation.check_urls([f.url for f in url_findings])
    for url, threat in threats.items():
        indicators.append({"category": "links", "label": f"Google Safe Browsing lists this link ({threat})",
                           "weight": 100, "evidence": url[:80]})
        sub["links"] = 100

    rules_score = combine(sub)
    score, method = rules_score, "rules"
    llm_result = llm.classify(text, domains, item.author_name, instructions) if use_llm else None
    if llm_result is not None:
        score = round(LLM_WEIGHT * llm_result["score"] + (1 - LLM_WEIGHT) * rules_score)
        method = "llm+rules"
        for s in llm_result["signals"]:
            indicators.append({"category": "ai", "label": s, "weight": 0, "evidence": ""})
    if threats:
        score = max(score, 90)

    reason = _reason(indicators, llm_result, warning, score)
    return Analysis("ok", int(score), method, reason, indicators,
                    {k: int(v) for k, v in sub.items()}, domains, version, warning)


def _reason(indicators: list[dict], llm_result: dict | None, warning: bool, score: int) -> str:
    if llm_result and llm_result.get("reason"):
        return llm_result["reason"][:200]
    strongest = sorted((i for i in indicators if i["category"] != "ai"), key=lambda i: -i["weight"])
    if not strongest:
        return "No common scam indicators found"
    labels = list(dict.fromkeys(CATEGORY_LABELS.get(i["category"], i["category"]) for i in strongest))
    prefix = "Discusses scams; " if warning else ""
    return (prefix + ", ".join(labels[:3]))[:200]
