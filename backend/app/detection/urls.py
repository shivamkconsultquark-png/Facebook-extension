"""Static URL / domain risk analysis.

SSRF-safe by construction: URLs are only *parsed*. Nothing here opens a connection to a
URL taken from a post. (Reputation lookups go to a fixed API host — see reputation.py.)
"""
from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass, field
from urllib.parse import parse_qs, unquote, urlsplit

# Redirect wrappers whose real destination is in a query parameter.
REDIRECT_WRAPPERS = {
    "l.facebook.com": "u", "lm.facebook.com": "u", "l.messenger.com": "u",
    "l.instagram.com": "u", "www.google.com": "q", "t.co": None,
}

SHORTENERS = {
    "bit.ly", "tinyurl.com", "t.ly", "goo.gl", "ow.ly", "is.gd", "buff.ly", "cutt.ly",
    "rb.gy", "shorturl.at", "rebrand.ly", "tiny.cc", "s.id", "v.gd", "lnkd.in", "t.me",
}

# TLDs that are disproportionately abused in phishing (cheap/free registration). A weak
# signal on its own — only adds a small amount.
RISKY_TLDS = {
    "zip", "mov", "top", "xyz", "click", "link", "work", "rest", "cyou", "icu", "buzz",
    "gq", "cf", "ml", "tk", "ga", "live", "shop", "loan", "win", "bid", "monster", "sbs",
}

# Brands commonly impersonated in Facebook scams -> their legitimate registrable domains.
BRAND_DOMAINS = {
    "facebook": {"facebook.com", "fb.com", "fb.me", "meta.com", "facebookmail.com"},
    "meta": {"meta.com", "facebook.com", "fb.com"},
    "instagram": {"instagram.com"},
    "whatsapp": {"whatsapp.com", "wa.me"},
    "paypal": {"paypal.com", "paypal.me"},
    "amazon": {"amazon.com", "amazon.co.uk", "amazon.in", "amazon.de", "amzn.to"},
    "apple": {"apple.com", "icloud.com"},
    "microsoft": {"microsoft.com", "live.com", "outlook.com"},
    "google": {"google.com", "youtube.com", "youtu.be"},
    "netflix": {"netflix.com"},
    "binance": {"binance.com"},
    "coinbase": {"coinbase.com"},
    "metamask": {"metamask.io"},
    "tesla": {"tesla.com"},
    "dhl": {"dhl.com"},
    "fedex": {"fedex.com"},
    "usps": {"usps.com"},
    "walmart": {"walmart.com"},
}

CREDENTIAL_PATH_WORDS = ("login", "signin", "sign-in", "verify", "verification", "account-",
                         "secure", "unlock", "recover", "appeal", "confirm", "wallet", "claim")

# Hosts that are user-content platforms: a link here says little about the post.
NEUTRAL_HOSTS = {"facebook.com", "fb.com", "fb.watch", "instagram.com", "youtube.com", "youtu.be"}

_HOMOGLYPHS = str.maketrans({"0": "o", "1": "l", "3": "e", "5": "s", "4": "a", "7": "t", "@": "a"})
_TWO_PART_SUFFIXES = {"co.uk", "com.au", "co.in", "com.br", "co.jp", "com.ng", "co.za", "com.mx", "com.ph"}


@dataclass
class UrlFinding:
    url: str
    domain: str
    risk: int = 0
    reasons: list[str] = field(default_factory=list)


def registrable_domain(host: str) -> str:
    parts = host.lower().strip(".").split(".")
    if len(parts) >= 3 and ".".join(parts[-2:]) in _TWO_PART_SUFFIXES:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:]) if len(parts) >= 2 else host.lower()


def unwrap(url: str) -> str:
    """Follow known redirect wrappers by reading their query string (no network)."""
    for _ in range(3):
        try:
            parts = urlsplit(url)
        except ValueError:
            return url
        host = (parts.hostname or "").lower()
        param = REDIRECT_WRAPPERS.get(host)
        if param and host != "t.co":
            target = parse_qs(parts.query).get(param, [None])[0]
            if target and target.startswith(("http://", "https://")):
                url = unquote(target)
                continue
        break
    return url


def _edit_distance(a: str, b: str) -> int:
    if abs(len(a) - len(b)) > 2:
        return 99
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


PHISH_AFFIXES = ("login", "signin", "secure", "security", "support", "verify", "account", "help",
                 "wallet", "official", "team", "recovery", "claim", "gift", "promo", "appeal", "service")


def lookalike_brand(host: str) -> str | None:
    """Return the impersonated brand if host looks like, but is not, a brand's domain.

    Flags: homoglyph/typo variants (paypa1.com), brand + phishing word (faceb00k-login.net),
    and brand as a subdomain of an unrelated domain (paypal.secure-account.com). A brand
    merely contained in an ordinary name (applesauce.com) is not flagged.
    """
    host = host.lower()
    reg = registrable_domain(host)
    label = reg.split(".")[0].translate(_HOMOGLYPHS)
    squashed = label.replace("-", "")
    sub_labels = [t.translate(_HOMOGLYPHS) for t in host[: -len(reg)].strip(".").split(".") if t]
    for brand, legit in BRAND_DOMAINS.items():
        if reg in legit:
            return None
        # one-character substitution typos (paypai.com); insertions would hit real words like apples.com
        if squashed == brand or (len(brand) >= 6 and len(squashed) == len(brand) and _edit_distance(squashed, brand) == 1):
            return brand
        if len(brand) >= 4 and brand in squashed:
            rest = squashed.replace(brand, "", 1)
            if any(a in rest for a in PHISH_AFFIXES):
                return brand
        if len(brand) >= 4 and any(brand == t.replace("-", "") or t.startswith(brand + "-") for t in sub_labels):
            return brand
    return None


def analyze_url(raw_url: str) -> UrlFinding | None:
    url = unwrap(raw_url.strip())
    try:
        parts = urlsplit(url)
    except ValueError:
        return UrlFinding(url=url[:300], domain="", risk=25, reasons=["Malformed URL"])
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return None
    host = parts.hostname.lower()
    finding = UrlFinding(url=url[:500], domain=registrable_domain(host))

    def add(points: int, reason: str):
        finding.risk += points
        finding.reasons.append(reason)

    try:
        ipaddress.ip_address(host)
        add(35, "Link points to a raw IP address")
    except ValueError:
        pass
    if "xn--" in host:
        add(30, "Internationalised (punycode) domain — possible look-alike")
    if parts.username or "@" in parts.netloc:
        add(30, "URL hides its real destination with '@'")
    if host in SHORTENERS or finding.domain in SHORTENERS:
        add(15, "URL shortener hides the destination")
    tld = host.rsplit(".", 1)[-1]
    if tld in RISKY_TLDS:
        add(12, f"Frequently abused .{tld} domain")
    if host.count(".") >= 4:
        add(10, "Unusually deep subdomain chain")
    if parts.scheme == "http":
        add(8, "Not using HTTPS")
    brand = lookalike_brand(host)
    if brand:
        add(45, f"Domain imitates {brand.title()} but is not an official {brand.title()} domain")
    path = (parts.path + "?" + parts.query).lower()
    if any(w in path for w in CREDENTIAL_PATH_WORDS) and finding.domain not in NEUTRAL_HOSTS:
        add(12, "Link path asks to log in / verify / claim")
    if len(url) > 200:
        add(5, "Very long URL")
    finding.risk = min(finding.risk, 100)
    return finding


def analyze_urls(urls: list[str]) -> list[UrlFinding]:
    seen, out = set(), []
    for u in urls:
        f = analyze_url(u)
        if f and f.url not in seen:
            seen.add(f.url)
            out.append(f)
    return out


URL_IN_TEXT = re.compile(r"\b(?:https?://|www\.)[^\s<>\"']+", re.I)
BARE_DOMAIN_IN_TEXT = re.compile(r"\b[a-z0-9][a-z0-9\-]{1,62}\.(?:" + "|".join(sorted(RISKY_TLDS | {"com", "net", "org", "io", "co", "info", "me"})) + r")(?:/[^\s]*)?\b", re.I)


def urls_from_text(text: str) -> list[str]:
    found = [m if m.lower().startswith("http") else "https://" + m for m in URL_IN_TEXT.findall(text or "")]
    for m in BARE_DOMAIN_IN_TEXT.findall(text or ""):
        cand = "https://" + m
        if not any(m.lower() in f.lower() for f in found):
            found.append(cand)
    return found
