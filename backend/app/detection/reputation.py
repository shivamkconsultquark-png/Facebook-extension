"""Optional threat-intelligence lookup: Google Safe Browsing v4 Lookup API.

Only the fixed Google endpoint is contacted; post URLs are sent as data, never fetched.
Disabled unless SAFE_BROWSING_API_KEY is set.
"""
from __future__ import annotations

import logging

import httpx

from ..config import settings

log = logging.getLogger("fbshield.reputation")
ENDPOINT = "https://safebrowsing.googleapis.com/v4/threatMatches:find"


def available() -> bool:
    return bool(settings.safe_browsing_api_key)


def check_urls(urls: list[str]) -> dict[str, str]:
    """Return {url: threat_type} for URLs Google lists as unsafe. Empty on error/disabled."""
    if not available() or not urls:
        return {}
    body = {
        "client": {"clientId": "fb-scam-shield", "clientVersion": "1.0.0"},
        "threatInfo": {
            "threatTypes": ["MALWARE", "SOCIAL_ENGINEERING", "UNWANTED_SOFTWARE", "POTENTIALLY_HARMFUL_APPLICATION"],
            "platformTypes": ["ANY_PLATFORM"],
            "threatEntryTypes": ["URL"],
            "threatEntries": [{"url": u} for u in urls[:20]],
        },
    }
    try:
        r = httpx.post(ENDPOINT, params={"key": settings.safe_browsing_api_key}, json=body, timeout=5.0)
        r.raise_for_status()
        return {m["threat"]["url"]: m["threatType"] for m in r.json().get("matches", [])}
    except Exception as e:
        log.warning("Safe Browsing lookup failed: %s", e)
        return {}
