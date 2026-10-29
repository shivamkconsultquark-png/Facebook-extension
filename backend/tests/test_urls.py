from app.detection.urls import analyze_url, lookalike_brand, registrable_domain, unwrap, urls_from_text


def test_unwraps_facebook_redirect():
    wrapped = "https://l.facebook.com/l.php?u=https%3A%2F%2Fevil.example%2Fx&h=AT0"
    assert unwrap(wrapped) == "https://evil.example/x"


def test_registrable_domain():
    assert registrable_domain("a.b.example.co.uk") == "example.co.uk"
    assert registrable_domain("www.paypal.com") == "paypal.com"


def test_lookalikes_detected():
    assert lookalike_brand("paypa1.com") == "paypal"
    assert lookalike_brand("faceb00k-login.net") == "facebook"
    assert lookalike_brand("paypal.secure-account.com") == "paypal"
    assert lookalike_brand("www.paypal.com") is None
    assert lookalike_brand("m.facebook.com") is None
    assert lookalike_brand("example.org") is None
    # ordinary names that merely contain a brand are not look-alikes
    assert lookalike_brand("applesauce.com") is None
    assert lookalike_brand("apple-pie-recipes.com") is None
    assert lookalike_brand("amazonia-travel.org") is None
    assert lookalike_brand("apple-support-id.com") == "apple"
    assert lookalike_brand("apples.com") is None
    assert lookalike_brand("paypai.com") == "paypal"


def test_risky_url_features():
    f = analyze_url("http://192.168.1.1/login")
    assert f.risk >= 35 and any("IP" in r for r in f.reasons)
    assert analyze_url("https://xn--pypal-4ve.com/").risk >= 30
    assert analyze_url("https://bit.ly/3abc").risk > 0
    assert analyze_url("https://user@evil.example/").risk >= 30


def test_ordinary_urls_have_no_risk():
    assert analyze_url("https://www.bbc.co.uk/news/world").risk == 0
    assert analyze_url("https://www.facebook.com/groups/123/posts/456").risk == 0


def test_non_http_and_garbage_ignored():
    assert analyze_url("javascript:alert(1)") is None
    assert analyze_url("mailto:a@b.com") is None
    assert analyze_url("not a url") is None


def test_urls_from_text_finds_bare_domains():
    found = urls_from_text("Claim at paypa1-secure.xyz/claim or visit https://bit.ly/x")
    assert "https://bit.ly/x" in found
    assert any("paypa1-secure.xyz" in u for u in found)
