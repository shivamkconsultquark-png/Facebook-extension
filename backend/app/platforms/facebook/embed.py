"""Can Facebook's embedded player play this video?

Some videos and reels only play on facebook.com (the owner turned embedding off, licensed music,
sponsored or non-public reels). Facebook's player then shows "Video unavailable". Its page only
contains a <video> element when the video can play, so one anonymous request tells the two apart.
"""
from __future__ import annotations

import threading
import time

import httpx

PLAYER = "https://www.facebook.com/plugins/video.php"
TTL = 3600  # seconds; a video can be deleted or have embedding turned off later
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")

_cache: dict[str, tuple[float, bool]] = {}
_lock = threading.Lock()


def playable(video_url: str) -> bool | None:
    """True / False, or None when Facebook couldn't be asked (the dashboard then just tries to play it)."""
    now = time.monotonic()
    with _lock:
        hit = _cache.get(video_url)
        if hit and now - hit[0] < TTL:
            return hit[1]
    try:
        r = httpx.get(PLAYER, params={"href": video_url, "show_text": "false", "width": "280"},
                      headers={"User-Agent": _UA, "Accept-Language": "en-US,en;q=0.9"},
                      timeout=10.0, follow_redirects=True)
    except httpx.HTTPError:
        return None
    if r.status_code != 200:
        return None
    ok = "<video" in r.text
    with _lock:
        if len(_cache) > 5000:
            _cache.clear()
        _cache[video_url] = (now, ok)
    return ok
