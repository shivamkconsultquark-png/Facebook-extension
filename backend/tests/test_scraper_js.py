"""The in-page collector, run in a real Chrome (Playwright) against synthetic Facebook-like markup.

The fixture imitates the semantic hooks the extractor relies on (role=feed, aria-posinset,
role=article, data-ad-rendering-role, dir=auto, l.facebook.com link shims). It is synthetic,
not copied from real accounts. Skips when no Chrome/Chromium can be launched.
"""
import pytest

from app.config import settings
from app.platforms.facebook.scraper import _merge, _page_scripts


def post_html(n, author, text, links=(), path=None, comments=(), see_more=False, image=None):
    link_html = "".join(f'<a href="https://l.facebook.com/l.php?u={l}&amp;h=AT1">{l}</a>' for l in links)
    permalink = f'<a href="{path}?__cft__[0]=x">2h</a>' if path else ""
    more = '<div role="button">See more</div>' if see_more else ""
    img = f'<img src="{image}" width="500" height="300" alt="May be an image of text that says WIN A CAR">' if image else ""
    com = "".join(f'<ul><li><div role="article" aria-label="Comment by X"><div dir="auto">{c}</div></div></li></ul>' for c in comments)
    return (f'<div aria-posinset="{n}"><div role="article"><h3><a href="/{author.replace(" ", ".")}">{author}</a></h3>{permalink}'
            f'<div data-ad-rendering-role="story_message"><div dir="auto">{text}</div>{more}</div>{link_html}{img}'
            f'<span>12 comments</span><span>3 shares</span>{com}</div></div>')


FIXTURE = "<div role='feed'>" + "".join([
    post_html(1, "Prize Desk", "You have won! Claim at the link", links=["https://claim-prize.xyz/win"],
              path="/prize.desk/posts/pfbid02AbC", comments=["comment text"], image="https://scontent.xx.fbcdn.net/v/a.jpg"),
    post_html(2, "Jane Doe", "Lovely weekend at the lake", see_more=True),
    '<div aria-posinset="3"><div role="article"></div></div>',  # still-loading shell
]) + "</div>"


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
        # The "See more" button expands the text when clicked, like Facebook's.
        pg.evaluate("""() => document.querySelectorAll('[role=button]').forEach(b => b.addEventListener('click',
            () => { b.previousElementSibling.textContent += ' and the full story continues here'; b.remove(); }))""")
        yield pg
        browser.close()


def test_collects_posts_with_text_links_media_and_counts(page):
    res = page.evaluate("n => window.FBShieldCollect(n)", 50)
    assert res["found"] == 3
    posts = {p["post"]["author_name"]: p for p in res["posts"]}
    assert set(posts) == {"Prize Desk", "Jane Doe"}  # loading shell skipped
    prize = posts["Prize Desk"]
    assert prize["post"]["urls"] == ["https://claim-prize.xyz/win"]
    assert prize["post"]["post_id"] == "pfbid02AbC"
    assert "comment text" not in prize["post"]["text"]
    assert "WIN A CAR" in prize["post"]["text"]  # image description captured
    assert prize["media_url"] == "https://scontent.xx.fbcdn.net/v/a.jpg"
    assert prize["comments_count"] == "12" and prize["shares_count"] == "3"


def test_see_more_is_expanded_before_extraction(page):
    res = page.evaluate("n => window.FBShieldCollect(n)", 50)
    jane = next(p for p in res["posts"] if p["post"]["author_name"] == "Jane Doe")
    assert jane["post"]["text"].endswith("the full story continues here")


def test_merge_keeps_longest_text_across_scrolls():
    short = {"post": {"post_id": None, "permalink": None, "author_name": "A", "text": "Hello there friends"}}
    long = {"post": {"post_id": None, "permalink": None, "author_name": "A", "text": "Hello there friends, full text"}}
    merged = {}
    _merge(merged, [short])
    _merge(merged, [long])
    _merge(merged, [short])
    assert [m["post"]["text"] for m in merged.values()] == ["Hello there friends, full text"]
