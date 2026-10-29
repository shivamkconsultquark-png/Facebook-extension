"""Detection-engine behaviour on synthetic posts. These check rule logic and ranking —
they are not accuracy measurements."""
import pytest

from app.detection import engine
from app.detection.engine import ContentItem, analyze, category_for, combine


def score(text, urls=()):
    return analyze(ContentItem(text=text, urls=list(urls))).score


SCAMS = [
    ("prize_fee", "Congratulations! You have been selected as our lucky winner. Claim your prize now — "
                  "just pay a small delivery fee with a gift card.", []),
    ("fake_meta_notice", "Your page will be permanently deleted for violating our community standards. "
                         "Verify your account within 24 hours using the link below.",
     ["https://l.facebook.com/l.php?u=https%3A%2F%2Ffacebook-support-team.xyz%2Flogin"]),
    ("investment", "Invest $500 and earn $5000 in 48 hours! Guaranteed profit. WhatsApp me +1 555 123 4567", []),
    ("crypto_doubling", "Elon Musk is giving away 5000 BTC to celebrate! Send 0.1 BTC and get 0.2 back. Limited time only", []),
    ("credentials", "Meta Security Team: send us the 6-digit code we just texted you to confirm your account or it will be locked", []),
]

LEGIT = [
    ("personal", "Had a great weekend hiking with the family at the lake! Weather was perfect.", []),
    ("crypto_news", "Bitcoin hit a new high today. Here's my analysis of what's driving the market.",
     ["https://www.coindesk.com/markets/2026/10/01/bitcoin"]),
    ("marketplace_normal", "Selling my sofa, $150, pick up only in Leeds. Message me if interested.", []),
    ("business_promo", "Our bakery is open late this Friday — come try the new pumpkin bread!", ["https://www.facebook.com/bakery"]),
]


@pytest.mark.parametrize("name,text,urls", SCAMS, ids=[s[0] for s in SCAMS])
def test_known_scam_patterns_are_flagged(name, text, urls):
    s = score(text, urls)
    assert category_for(s) == "likely_scam", (name, s)


@pytest.mark.parametrize("name,text,urls", LEGIT, ids=[s[0] for s in LEGIT])
def test_ordinary_posts_are_not_flagged(name, text, urls):
    s = score(text, urls)
    assert category_for(s) == "likely_legitimate", (name, s)


def test_post_warning_about_scams_scores_lower_than_the_scam():
    scam = "You have won a prize! Claim your prize now — pay a small delivery fee with gift cards."
    warning = ("PSA: beware of posts saying you have won a prize and asking you to pay a delivery fee "
               "with gift cards. This is a scam, report it.")
    a = analyze(ContentItem(warning))
    assert a.warning_context
    assert a.score < score(scam)
    assert category_for(a.score) != "likely_scam"


def test_ambiguous_post_is_suspicious_not_scam():
    s = score("DM me on Telegram for the details, limited spots left!")
    assert category_for(s) == "suspicious", s


def test_empty_and_tiny_content_is_unable_to_verify():
    for text in ("", "   ", "ok", "​​"):
        a = analyze(ContentItem(text))
        assert a.status == "insufficient_content"
        assert a.score is None
        assert category_for(a.score) == "unable_to_verify"


def test_link_only_post_is_still_analysed():
    a = analyze(ContentItem("", ["http://192.168.10.5/paypal/login"]))
    assert a.status == "ok" and a.score >= 35


def test_zero_width_obfuscation_is_normalised():
    plain = score("Claim your prize now, pay a small fee to receive it")
    obfuscated = score("Cl​aim your pr​ize now, pay a sm​all fee to receive it")
    assert plain == obfuscated


def test_explanations_reference_signals():
    a = analyze(ContentItem("Send your seed phrase to our support team to unlock your wallet"))
    assert any(i["category"] == "credential_request" for i in a.indicators)
    assert "Credential" in a.reason


def test_combine_is_monotonic_and_bounded():
    assert combine({}) == 0
    assert combine({"a": 35}) == 35
    assert combine({"a": 35, "b": 30}) > 35
    assert combine({"a": 100, "b": 100}) == 100


def test_sensitivity_shifts_thresholds():
    assert category_for(40, "normal") == "suspicious"
    assert category_for(40, "low") == "likely_legitimate"
    assert category_for(62, "high") == "likely_scam"
    assert category_for(62, "bogus") == "suspicious"  # unknown -> normal


def test_llm_score_is_blended_with_rules(monkeypatch):
    monkeypatch.setattr(engine.llm, "available", lambda: True)
    monkeypatch.setattr(engine.llm, "classify", lambda *a, **k: {"score": 90, "reason": "Fake prize", "signals": ["prize claim"]})
    a = analyze(ContentItem("Hello friends, have a lovely day everyone"))
    assert a.scoring_method == "llm+rules"
    assert a.score == round(0.65 * 90)
    assert a.reason == "Fake prize"


def test_llm_failure_falls_back_to_rules(monkeypatch):
    monkeypatch.setattr(engine.llm, "available", lambda: True)
    monkeypatch.setattr(engine.llm, "classify", lambda *a, **k: None)
    a = analyze(ContentItem("Invest $500 and earn $5000 in 48 hours, guaranteed profit"))
    assert a.scoring_method == "rules" and a.score >= 35


def test_safe_browsing_match_floors_score(monkeypatch):
    monkeypatch.setattr(engine.reputation, "check_urls", lambda urls: {urls[0]: "SOCIAL_ENGINEERING"})
    a = analyze(ContentItem("Look at this", ["https://example.com/page"]))
    assert a.score >= 90


def test_content_hash_stable_and_sensitive():
    a = engine.content_hash(ContentItem("Hello  World", ["https://a.com"]), None)
    b = engine.content_hash(ContentItem("hello world", ["https://a.com"]), None)
    c = engine.content_hash(ContentItem("hello world", ["https://b.com"]), None)
    assert a == b != c


# Regression cases modelled on real feed posts the 1.0 rules missed (names kept generic).
FEED_REGRESSIONS = [
    ("Christian giveaway", "A big thank you to all my amazing fans for your love and support! I'm doing a giveaway "
     "for the first 100 people! Hurry up, participate, and stand a chance to ... See more", "likely_scam"),
    ("Saferecovery", "Scam Alert & Recovery Assistance H... See more", "suspicious"),
    (None, "National Fraud Division Government organisation. We investigate reports of fraud, scams, and "
     "financial crimes. Report Case Via DM", "suspicious"),
    ("The Cozy Coop", "COZY FALL GIVEAWAY! It's... See more", "likely_legitimate"),
    ("County Sheriff's Office", "BEWARE OF THIS NEWLY REPORTED SCAM D... See more", "likely_legitimate"),
    ("City Police Department", "Scam Advisory! Scammers are calling residents claiming to be from the fraud "
     "unit. Never send money. Report it to us at 360-555-0100.", "likely_legitimate"),
]


@pytest.mark.parametrize("author,text,expected", FEED_REGRESSIONS, ids=[c[0] or "no-author" for c in FEED_REGRESSIONS])
def test_feed_regressions(author, text, expected):
    a = analyze(ContentItem(text, [], author))
    assert category_for(a.score) == expected, (a.score, a.reason)


def test_recovery_scams_are_not_damped_by_warning_words():
    a = analyze(ContentItem("Scam alert! Were you scammed? Our recovery experts recover your lost funds. Report case via DM"))
    assert a.warning_context and category_for(a.score) != "likely_legitimate"


def test_facebook_see_more_controls_are_stripped():
    assert "See more" not in engine.normalise_text("Claim your prize… See more")
