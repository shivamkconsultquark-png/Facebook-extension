"""Facebook-specific request shapes and identity rules. Everything Facebook-only lives here;
the detection engine never sees these types (it gets a ContentItem)."""
from __future__ import annotations

import hashlib
import re
from urllib.parse import parse_qs, urlsplit

from pydantic import BaseModel, Field, field_validator

from ...config import settings
from ...detection.engine import ContentItem, normalise_text

FACEBOOK_HOSTS = {"facebook.com", "www.facebook.com", "web.facebook.com", "m.facebook.com", "mbasic.facebook.com"}
_POST_ID_RE = re.compile(r"^[A-Za-z0-9_.:\-]{1,120}$")
# Facebook UI controls that end up inside captured post text.
_FB_CONTROLS = re.compile(r"(?:…|\.\.\.)?[ \t]*\b(?:See more|See less|See translation|See original|Rate this translation)\b", re.I)


def clean_post_text(text: str) -> str:
    """Strip Facebook's 'See more' / translation controls, keeping line breaks."""
    return re.sub(r"[ \t]{2,}", " ", _FB_CONTROLS.sub("", text or "")).strip()


class FacebookPost(BaseModel):
    """What the content script sends for one feed post (the minimum needed to score it)."""
    client_id: str = Field(..., min_length=1, max_length=64, description="Temporary id to match the response to the DOM node")
    post_id: str | None = Field(None, max_length=120, description="Facebook post id, if visible in a permalink")
    permalink: str | None = Field(None, max_length=1000)
    text: str = Field("", max_length=20_000)
    urls: list[str] = Field(default_factory=list, max_length=50)
    author_name: str | None = Field(None, max_length=255)
    author_url: str | None = Field(None, max_length=1000)
    is_sponsored: bool = False

    @field_validator("text")
    @classmethod
    def _trim_text(cls, v: str) -> str:
        return clean_post_text(v)[: settings.max_text_chars]

    @field_validator("urls")
    @classmethod
    def _clean_urls(cls, v: list[str]) -> list[str]:
        out = []
        for u in v[:20]:
            u = (u or "").strip()[:2000]
            if u.lower().startswith(("http://", "https://")):
                out.append(u)
        return out

    @field_validator("post_id")
    @classmethod
    def _check_post_id(cls, v: str | None) -> str | None:
        return v if v and _POST_ID_RE.match(v) else None

    @field_validator("permalink", "author_url")
    @classmethod
    def _facebook_only(cls, v: str | None) -> str | None:
        """Only keep links that point at Facebook itself; anything else is dropped."""
        if not v:
            return None
        try:
            parts = urlsplit(v)
        except ValueError:
            return None
        if parts.scheme != "https" or (parts.hostname or "").lower() not in FACEBOOK_HOSTS:
            return None
        keep = {k: q for k, q in parse_qs(parts.query).items() if k in ("story_fbid", "id", "fbid", "v")}
        query = "&".join(f"{k}={q[0]}" for k, q in sorted(keep.items()))
        return f"https://www.facebook.com{parts.path}" + (f"?{query}" if query else "")

    def post_key(self) -> str:
        """Stable identity for dedup: FB post id if known, else a hash of permalink or content."""
        if self.post_id:
            return f"fb:{self.post_id}"
        # Opening text only: the rest changes once "See more" is expanded.
        basis = self.permalink or f"{(self.author_name or '').lower()}|{normalise_text(self.text).lower()[:120]}"
        return "h:" + hashlib.sha256(basis.encode()).hexdigest()[:40]

    def to_content(self) -> ContentItem:
        return ContentItem(text=self.text, urls=self.urls, author_name=self.author_name)
