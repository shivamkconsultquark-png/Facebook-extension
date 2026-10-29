"""_album_photos / _fetch_albums against a simulated Facebook photo viewer (served through a
Playwright route, so nothing reaches facebook.com). Skips when no browser can be launched."""
import pytest

from app.config import settings
from app.platforms.facebook import scraper

PHOTOS = [f"https://scontent.xx.fbcdn.net/v/t39.30808-6/album{i}.jpg" for i in range(7)]
TINY_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d4944415478da63f8ffff3f0005fe02fea7d6a4"
    "0000000049454e44ae426082")

VIEWER = """<html><body>
<img id="p" src="%s" style="width:900px;height:600px">
<div id="next" aria-label="Next photo" role="button">next</div>
<script>
  const imgs = %s, el = document.getElementById('p');
  document.getElementById('next').addEventListener('click', () =>
    setTimeout(() => { el.src = imgs[(imgs.indexOf(el.src) + 1) %% imgs.length]; }, 120));
</script>
</body></html>"""


@pytest.fixture(scope="module")
def context():
    pw = pytest.importorskip("playwright.sync_api")
    with pw.sync_playwright() as p:
        try:
            kwargs = {"headless": True}
            if settings.scraper_browser_channel:
                kwargs["channel"] = settings.scraper_browser_channel
            browser = p.chromium.launch(**kwargs)
        except Exception as e:
            pytest.skip(f"no browser available: {e}")
        ctx = browser.new_context()
        import json
        ctx.route("https://www.facebook.com/photo/**", lambda r: r.fulfill(
            content_type="text/html", body=VIEWER % (PHOTOS[0], json.dumps(PHOTOS))))
        ctx.route("https://scontent.xx.fbcdn.net/**", lambda r: r.fulfill(content_type="image/png", body=TINY_PNG))
        yield ctx
        browser.close()


def test_walks_the_viewer_until_it_wraps_around(context):
    photos, complete = scraper._album_photos(context, "https://www.facebook.com/photo/?fbid=1&set=pcb.9", expected=7)
    assert photos == PHOTOS and complete


def test_fetch_albums_replaces_feed_tiles_and_skips_known_albums(context, monkeypatch):
    feed_item = {"post": {"post_id": "pfbidALBUM"}, "media_urls": PHOTOS[:5], "media_url": PHOTOS[0],
                 "media_more": 2, "album_url": "https://www.facebook.com/photo/?fbid=1&set=pcb.9"}
    known = {"post": {"post_id": "pfbidDONE"}, "media_urls": PHOTOS[:5], "media_more": 2,
             "album_url": "https://www.facebook.com/photo/?fbid=2&set=pcb.8"}
    scraper._fetch_albums(context, [feed_item, known], skip={"pfbidDONE"})
    assert feed_item["media_urls"] == PHOTOS and feed_item["media_more"] == 0 and feed_item["album_complete"]
    assert known["media_urls"] == PHOTOS[:5] and "album_complete" not in known  # not reopened
