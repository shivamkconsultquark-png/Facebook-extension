"""The collector against the 2026 home-feed layout (no role="feed" wrapper).

Synthetic markup modelled on the hooks seen in the live feed on 2026-10-27: [aria-posinset]
items directly under role=main, data-ad-rendering-role sections, hidden decoy text, counts
printed inside the Like/Comment/Share buttons, and a timestamp link whose real href only
appears on hover. Not copied from real accounts. Skips when no browser can be launched.
"""
import pytest

from app.config import settings
from app.platforms.facebook.scraper import _merge, _page_scripts

AVATAR = "https://scontent.xx.fbcdn.net/v/t39.30808-1/avatar.jpg"
PHOTO = "https://scontent.xx.fbcdn.net/v/t39.99422-6/photo.jpg"


def button(role, label, count):
    return (f'<div aria-label="{label}" role="button"><div><i></i><div data-ad-rendering-role="{role}"></div></div>'
            f'<div><span dir="auto">{count}</span></div></div>')


def post_2026(n, author, text, slug, pfbid, likes="8.4K", comments="236", shares="213", translated_original=None):
    original = (f'<div dir="auto" style="display:none">{translated_original}</div>' if translated_original else "")
    return (
        f'<div aria-posinset="{n}"><div>'
        f'<svg><image xlink:href="{AVATAR}" href="{AVATAR}"></image></svg>'
        f'<div data-ad-rendering-role="profile_name"><h4><a href="https://www.facebook.com/{slug}?__cft__[0]=AZ">{author}</a></h4></div>'
        # timestamp link: Facebook fills in the permalink on hover
        f'<a href="?__cft__[0]=AZ" class="ts" data-real="/{slug}/posts/{pfbid}">2h</a>'
        f'<div aria-hidden="true">{"<span dir=auto>Facebook</span>" * 5}</div>'
        f'<div data-ad-rendering-role="story_message"><div dir="auto">{text}</div></div>{original}'
        f'<a href="/photo/?fbid=1849365890525225&amp;set=a.1&amp;__cft__[0]=AZ"><img src="{PHOTO}" width="512" height="640" alt=""></a>'
        f'<div data-ad-rendering-role="title"><span dir="auto">Shivam</span></div>'
        f'<div data-ad-rendering-role="description"><span dir="auto">Kji5VQEhToXGZ1rckXHOYJ1x9X</span></div>'
        + button("like_button", "Like", likes) + button("comment_button", "Leave a comment", comments)
        + button("share_button", "Share, send this to friends or post it on your profile.", shares)
        + '</div></div>'
    )


FIXTURE = "<div role='main'>" + "".join([
    post_2026(1, "Prize Desk", "Congratulations, you won a car! Pay the delivery fee to claim it", "prize.desk", "pfbid02AbC"),
    '<div aria-posinset="2"><div><h3><a href="/reel/?s=ifu">Reels</a></h3><a href="/reel/1355960770935290/">'
    '<img src="https://scontent.xx.fbcdn.net/v/t15/reel.jpg" width="300"></a></div></div>',
    post_2026(3, "Rail News", "Rail expansion in Chhattisgarh, a gift of development", "rail.news", "pfbid07XyZ",
              translated_original="छत्तीसगढ़ में रेल विस्तार"),
    # Translated post: Facebook renders the message block twice plus translation controls.
    '<div aria-posinset="5"><div><div data-ad-rendering-role="profile_name"><h4><a href="/ganpati.page">Ganpati Page</a></h4></div>'
    '<div data-ad-rendering-role="story_message"><div dir="auto">Ganpati Bappa Morya! Blessings to everyone today.</div>'
    '<span> · See original · Rate this translation</span></div>'
    '<div data-ad-rendering-role="story_message"><div dir="auto">Ganpati Bappa Morya! Blessings to everyone today.</div></div>'
    '</div></div>',
    # Multi-photo post: 5 tiles, "+3" over the 5th (= 7 photos), a video element inside the album,
    # and a comment preview (role=article "Comment by …").
    '<div aria-posinset="6"><div><div data-ad-rendering-role="profile_name"><h4><a href="/album.page">Album Page</a></h4></div>'
    '<div data-ad-rendering-role="story_message"><div dir="auto">Photos from our community event yesterday</div></div>'
    + "".join(f'<a href="/photo/?fbid=10{i}&amp;set=pcb.555&amp;__cft__[0]=AZ"><img src="https://scontent.xx.fbcdn.net/v/t39.99422-6/tile{i}.jpg" width="250"></a>' for i in range(5))
    + '<div>+3</div><video></video>'
    '<div data-commentid="1"><div role="article" aria-label="Comment by Someone a day ago">'
    '<span dir="auto">Someone</span><a href="/album.page/posts/pfbid0X?comment_id=1"><span dir="auto">1d</span></a>'
    '<div dir="auto">DM me on WhatsApp for crypto profits</div>'
    '<img src="https://scontent.xx.fbcdn.net/v/t39.30808-6/comment-photo.jpg" width="200"></div></div>'
    '</div></div>',
    # Reel in the feed: a video with a /reel/ permalink.
    '<div aria-posinset="7"><div><div data-ad-rendering-role="profile_name"><h4><a href="/clips.page">Clips Page</a></h4></div>'
    '<div data-ad-rendering-role="story_message"><div dir="auto">Watch this quick kitchen tip</div></div>'
    '<a href="/reel/1844423296575048/"><video poster="https://scontent.xx.fbcdn.net/v/t15.5256-10/poster.jpg"></video></a>'
    '</div></div>',
    # Photo post with a zero-size <video> placeholder: must stay a photo post.
    '<div aria-posinset="8"><div><div data-ad-rendering-role="profile_name"><h4><a href="/score.page">Score Page</a></h4></div>'
    '<div data-ad-rendering-role="story_message"><div dir="auto">India won the third match by six wickets</div></div>'
    '<a href="/photo/?fbid=77"><img src="https://scontent.xx.fbcdn.net/v/t39.30808-6/score.jpg" width="500"></a>'
    '<video style="width:0;height:0"></video></div></div>',
    '<div aria-posinset="4"><div><h2>No more posts</h2><span dir="auto">Add more friends to see more posts in your Feed.</span></div></div>',
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
        # Like Facebook: hovering the timestamp link swaps in the real permalink.
        pg.evaluate("""() => document.querySelectorAll('a.ts').forEach(a => a.addEventListener('mouseover',
            () => a.setAttribute('href', a.dataset.real)))""")
        yield pg
        browser.close()


def collect(page):
    return {p["post"]["author_name"]: p for p in page.evaluate("n => window.FBShieldCollect(n)", 50)["posts"]}


def test_finds_posts_without_a_role_feed_wrapper_and_skips_non_post_units(page):
    # Reels carousel and "No more posts" skipped
    assert set(collect(page)) == {"Prize Desk", "Rail News", "Ganpati Page", "Album Page", "Clips Page", "Score Page"}


def test_multi_photo_post_keeps_every_tile_and_the_plus_count(page):
    album = collect(page)["Album Page"]
    assert album["media_type"] == "photo"
    assert album["media_urls"] == [f"https://scontent.xx.fbcdn.net/v/t39.99422-6/tile{i}.jpg" for i in range(5)]
    # "+3" covers the 5th tile: 4 + 3 = 7 photos, 5 stored, 2 left for the photo viewer.
    assert album["media_more"] == 2 and album["media_url"] == album["media_urls"][0]
    assert album["album_url"] == "https://www.facebook.com/photo/?fbid=100&set=pcb.555"  # tracking removed
    assert album["video_url"] is None


def test_hidden_video_placeholder_does_not_make_a_photo_post_a_video(page):
    score = collect(page)["Score Page"]
    assert score["media_type"] == "photo" and score["video_url"] is None


def test_feed_reel_has_a_playable_video_link(page):
    assert collect(page)["Clips Page"]["video_url"] == "https://www.facebook.com/reel/1844423296575048/"


def test_comment_preview_is_not_part_of_the_post(page):
    album = collect(page)["Album Page"]
    assert album["post"]["text"] == "Photos from our community event yesterday"
    assert not any("comment-photo" in u for u in album["media_urls"])


def test_reel_in_feed_is_tagged_with_its_poster(page):
    clip = collect(page)["Clips Page"]
    assert clip["media_type"] == "reel"
    assert clip["media_urls"] == ["https://scontent.xx.fbcdn.net/v/t15.5256-10/poster.jpg"]
    assert clip["post"]["post_id"] == "1844423296575048"


def test_translated_message_rendered_twice_is_kept_once(page):
    assert collect(page)["Ganpati Page"]["post"]["text"] == "Ganpati Bappa Morya! Blessings to everyone today."


def test_reads_author_avatar_image_and_button_counts(page):
    prize = collect(page)["Prize Desk"]
    assert prize["post"]["author_url"] == "https://www.facebook.com/prize.desk"
    assert prize["author_image"] == AVATAR
    assert prize["media_url"] == PHOTO
    assert (prize["reactions_count"], prize["comments_count"], prize["shares_count"]) == ("8.4K", "236", "213")


def test_text_excludes_hidden_decoys_and_untranslated_original(page):
    posts = collect(page)
    assert posts["Prize Desk"]["post"]["text"] == "Congratulations, you won a car! Pay the delivery fee to claim it"
    rail = posts["Rail News"]["post"]["text"]
    assert "Facebook" not in rail and "Shivam" not in rail and "छत्तीसगढ़" not in rail


def test_reveal_turns_photo_id_into_the_post_permalink(page):
    before = collect(page)["Prize Desk"]["post"]
    assert before["post_id"] == "1849365890525225"  # only the photo link is known before hover
    page.evaluate("() => window.FBShieldReveal()")
    after = collect(page)["Prize Desk"]["post"]
    assert after["post_id"] == "pfbid02AbC"
    assert after["permalink"] == "https://www.facebook.com/prize.desk/posts/pfbid02AbC"  # tracking params removed


def test_merge_treats_photo_id_and_permalink_sightings_as_one_post():
    base = {"author_name": "A", "text": "On the occasion of the festival I visited the pandal in Bandra."}
    first = {"post": {**base, "post_id": "1849365890525225", "permalink": None}, "media_url": PHOTO,
             "reactions_count": None}
    later = {"post": {**base, "text": base["text"] + " Full story.", "post_id": "pfbid02AbC",
                      "permalink": "https://www.facebook.com/a/posts/pfbid02AbC"}, "media_url": None,
             "reactions_count": "21.1K"}
    merged = {}
    _merge(merged, [first])
    _merge(merged, [later])
    assert len(merged) == 1
    (m,) = merged.values()
    assert m["post"]["post_id"] == "pfbid02AbC" and m["post"]["text"].endswith("Full story.")
    assert m["media_url"] == PHOTO and m["reactions_count"] == "21.1K"
