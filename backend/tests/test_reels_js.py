"""FBShieldCollectReels against synthetic markup modelled on the Reels tab (2026-10-27):
a [data-video-id] card with the video poster, an owner link "<name>, see owner profile",
the caption, and Like / Comment / Share buttons carrying the counts. Not copied from real
accounts. Skips when no browser can be launched."""
import pytest

from app.config import settings
from app.platforms.facebook.scraper import _page_scripts

POSTER = "https://scontent.xx.fbcdn.net/v/t15.5256-10/reel-poster.jpg"
AVATAR = "https://scontent.xx.fbcdn.net/v/t39.30808-1/owner.jpg"


def reel_card(video_id, owner, caption, likes, comments, shares, with_details=True):
    details = (
        f'<a aria-label="See owner profile" href="/profile.php?id=615903&amp;sk=reels_tab&amp;__cft__[0]=AZ">'
        f'<svg><image href="{AVATAR}"></image></svg></a>'
        f'<h2><a aria-label="{owner}, see owner profile" href="/profile.php?id=615903&amp;sk=reels_tab">{owner}</a></h2>'
        '<span aria-label="Shared with Public" role="button"><img src="https://static.xx.fbcdn.net/x.webp"></span>'
        '<div aria-label="Follow" role="button">Follow</div>'
        f'<span dir="auto"><div>{caption} <a href="/reel/hashtag/?q=%23viral">#viral</a>'
        ' <a href="https://l.facebook.com/l.php?u=https%3A%2F%2Fwin-free-phone.top%2Fclaim&amp;h=AT1">link</a></div></span>'
    ) if with_details else ""
    return (
        f'<div class="card"><div data-index="0" data-video-id="{video_id}"><video poster="{POSTER}"></video>{details}</div>'
        f'<div aria-label="Like" role="button"><span dir="auto">{likes}</span></div>'
        f'<div aria-label="Comment" role="button"><span dir="auto">{comments}</span></div>'
        f'<div aria-label="Share, {shares} shares" role="button"><span dir="auto">{shares}</span></div></div>'
    )


FIXTURE = ("<div role='main'>"
           + reel_card("2755324664851956", "Sifat Ali", "Free phone giveaway, tap the link", "201K", "542", "942")
           + reel_card("1089523094058666", "Preloaded", "", "1", "1", "1", with_details=False)
           + "</div>")


@pytest.fixture(scope="module")
def page():
    pw = pytest.importorskip("playwright.sync_api")
    with pw.sync_playwright() as p:
        try:
            kwargs = {"headless": True}
            if settings.scraper_browser_channel:
                kwargs["channel"] = settings.scraper_browser_channel
            browser = p.chromium.launch(**kwargs)
        except Exception as e:
            pytest.skip(f"no browser available: {e}")
        pg = browser.new_page()
        pg.set_content(f"<html><body>{FIXTURE}</body></html>")
        pg.add_script_tag(content=_page_scripts())
        yield pg
        browser.close()


def test_reads_the_playing_reel_and_skips_preloaded_cards(page):
    reels = page.evaluate("() => window.FBShieldCollectReels()")["posts"]
    assert len(reels) == 1
    r = reels[0]
    assert r["media_type"] == "reel" and r["media_urls"] == [POSTER]
    assert r["post"]["post_id"] == "2755324664851956"
    assert r["post"]["permalink"] == "https://www.facebook.com/reel/2755324664851956"
    assert r["post"]["author_name"] == "Sifat Ali"
    assert r["post"]["author_url"] == "https://www.facebook.com/profile.php?id=615903"
    assert r["author_image"] == AVATAR
    assert r["post"]["text"].startswith("Free phone giveaway, tap the link #viral")
    assert r["post"]["urls"] == ["https://win-free-phone.top/claim"]
    # Share count from the Share button, not the "Shared with Public" icon.
    assert (r["reactions_count"], r["comments_count"], r["shares_count"]) == ("201K", "542", "942")
