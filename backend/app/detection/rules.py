"""Explainable rule-based signals, grouped into risk categories.

Each category produces its own 0-100 sub-score. Patterns are phrase-level (not single
topic words) so posts that merely *mention* crypto, money or giveaways are not penalised;
the original Twitter heuristic's "bitcoin/crypto = spam" keyword rule is intentionally
not carried over. A "warning / discussion" context damps the text categories, because
posts warning others about scams use the same vocabulary as the scams.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

RULESET_VERSION = "fb-rules-1.1.0"


@dataclass(frozen=True)
class Rule:
    category: str
    pattern: re.Pattern
    weight: int
    label: str


def _r(category: str, weight: int, label: str, *phrases: str) -> list[Rule]:
    return [Rule(category, re.compile(p, re.I), weight, label) for p in phrases]


RULES: list[Rule] = [
    # --- requests for money / payment outside normal channels
    *_r("payment_request", 35, "Asks for payment by gift card, crypto or wire",
        r"\b(pay|send|payment|deposit)\b.{0,40}\b(gift ?cards?|itunes card|steam card|google play card|bitcoin|btc|usdt|crypto|western union|moneygram|wire transfer)\b",
        r"\b(gift ?cards?|usdt|bitcoin|btc)\b.{0,30}\b(only|accepted|as payment)\b"),
    *_r("payment_request", 25, "Asks for an upfront / processing / release fee",
        r"\b(processing|delivery|shipping|release|clearance|registration|activation|transfer)\s+fee\b",
        r"\bpay (a )?small fee\b", r"\bfee (to|before you) (claim|receive|release)\b"),
    *_r("payment_request", 15, "Asks to send money via a payment app",
        r"\b(send|pay)\b.{0,25}\b(cash ?app|zelle|venmo|paypal (friends|f&f)|gcash)\b"),

    # --- requests for credentials / sensitive data
    *_r("credential_request", 40, "Asks for passwords, codes or card details",
        r"\b(send|share|give|enter|provide|confirm)\b.{0,30}\b(password|passcode|otp|one[- ]time code|verification code|6[- ]digit code|pin|cvv|card number|seed phrase|recovery phrase|private key)\b",
        r"\b(seed|recovery) phrase\b", r"\bprivate key\b.{0,20}\b(wallet|send|share)\b"),
    *_r("credential_request", 30, "Fake account-security warning",
        r"\byour (page|account) (will be|has been) (disabled|suspended|deleted|restricted|permanently)\b",
        r"\b(violat(ed|ing)|breach(ed)?) (our )?(community standards|policies|copyright|terms)\b.{0,80}\b(verify|appeal|confirm|click|link)\b",
        r"\b(verify|confirm) your (account|identity|page)\b.{0,40}\b(within|or|before|link)\b"),
    *_r("credential_request", 15, "Asks for personal identity documents",
        r"\b(send|upload|share)\b.{0,25}\b(ssn|social security|passport|id card|driver'?s licen[cs]e|bank (login|details))\b"),

    # --- urgency / threats / manipulation
    *_r("urgency", 12, "High-pressure urgency",
        r"\b(act|claim|respond|hurry|register) (now|fast|immediately|today)\b",
        r"\b(within|in) (the next )?\d{1,2} ?(hours?|hrs|minutes|mins)\b",
        r"\blimited (slots?|spots?|time only|offer)\b", r"\bonly \d+ (slots?|spots?|left)\b",
        r"\blast chance\b", r"\bdon'?t miss (this|out)\b", r"\bhurry( up)?\b"),
    *_r("urgency", 18, "Threatens loss or legal action",
        r"\b(will be|get) (permanently )?(deleted|banned|arrested|blocked|locked)\b",
        r"\blegal action\b.{0,40}\b(unless|if you don'?t)\b"),

    # --- giveaways / prizes / lottery
    *_r("giveaway", 30, "Prize, lottery or giveaway claim",
        r"\byou(?:'ve| have)? (been selected|won|are (a|the|our) (lucky )?winner)\b",
        r"\b(congratulations|congrats)\b.{0,60}\b(won|winner|selected|prize|reward)\b",
        r"\b(claim|collect) your (prize|reward|winnings|gift|money)\b",
        r"\b(lottery|jackpot|sweepstakes?) (winner|claim|agent)\b"),
    *_r("giveaway", 20, "Engagement-bait giveaway",
        r"\b(like|share|comment)\b.{0,30}\b(and|&|\+)\b.{0,30}\b(share|comment|type|follow)\b.{0,60}\b(win|chance|receive|giveaway|free)\b",
        r"\bgiving away\b.{0,60}\b(to (the )?first|everyone who|to anyone who)\b",
        r"\bfree (iphone|ps5|car|motorhome|caravan|rv|laptop|grocery|gift card)\b"),
    *_r("giveaway", 22, "Giveaway with a first-N / chance-to-win hook",
        r"\bgiveaway\b.{0,40}\b(first \d+|for the first)\b",
        r"\bfirst \d+ (people|persons|comments|followers|to (comment|share|respond))\b",
        r"\bstand a chance to (win|receive|get|be)\b"),
    *_r("giveaway", 20, "Fan-page giveaway (common celebrity impersonation)",
        r"\b(thank you|thanks)\b.{0,15}\b(my|our)\b.{0,25}\b(fans|followers|supporters)\b.{0,160}\b(giveaway|giving away|gift|reward|win|bless)"),
    # Real businesses run giveaways too, so the bare word is only a weak signal.
    *_r("giveaway", 8, "Giveaway announcement", r"\bgive ?away\b"),

    # --- fake investment / crypto schemes
    *_r("investment", 35, "Guaranteed or unrealistic returns",
        r"\bguaranteed (profit|returns?|income|payout)\b",
        r"\b(double|triple) your (money|investment|bitcoin|crypto)\b",
        r"\b\d{2,4} ?% (profit|return|roi)\b.{0,30}\b(daily|weekly|per (day|week)|in \d+ (days|hours))\b",
        r"\b(invest|investing)\b.{0,30}\$?\d[\d,]*.{0,30}\b(earn|get|receive|make)\b.{0,15}\$?\d[\d,]*.{0,25}\b(in|within|after)\s+\d+\s*(hours?|days?)\b",
        r"\brisk[- ]free (investment|trading|profit)\b"),
    *_r("investment", 35, "Crypto doubling scheme",
        r"\bsend\b.{0,30}\b(btc|eth|usdt|bitcoin|ethereum|crypto|sol)\b.{0,40}\b(get|receive)\b.{0,25}\b(back|double|2x|twice)\b",
        r"\bgiving away\b.{0,30}\b\d[\d,.]*\s*(btc|eth|bitcoin|ethereum|usdt)\b"),
    *_r("investment", 22, "Account-manager / recovery scheme",
        r"\b(account manager|trading expert|forex mentor|binary options)\b.{0,60}\b(dm|inbox|message|whatsapp|telegram)\b",
        r"\b(recover|get back) (your )?(lost|stolen|scammed) (funds|money|crypto|bitcoin)\b",
        r"\btestimon(y|ial)\b.{0,80}\b(invested|withdrew|payout|profit)\b"),
    *_r("investment", 15, "Get-rich-quick pitch",
        r"\b(work from home|passive income)\b.{0,60}\b(\$\d|per (day|week)|daily)\b",
        r"\bmake \$?\d[\d,]* (a|per) (day|week)\b"),

    # --- impersonation of brands, officials, public figures
    *_r("impersonation", 25, "Claims to be an official support / security team",
        r"\b(meta|facebook) (support|security|business|help) (team|center|centre)\b",
        r"\bofficial (support|admin|team|agent|account)\b.{0,40}\b(contact|message|dm|click|link)\b",
        r"\bpage (verification|review) team\b"),
    *_r("impersonation", 20, "Presents itself as a fraud / cyber-crime authority",
        r"\b(fraud|cyber ?crimes?|scam|anti[- ]?scam) (enforcement |investigation |recovery )?(division|unit|department|bureau|desk|agency|commission|center|centre)\b"),
    *_r("impersonation", 20, "Celebrity / public-figure endorsement claim",
        r"\b(elon musk|mrbeast|mr beast|oprah|bill gates|warren buffett|jeff bezos|cristiano ronaldo)\b.{0,80}\b(giv\w*|invest\w*|offer\w*|reward\w*|free|crypto|platform|bitcoin)\b"),

    # --- fund-recovery / fake-authority schemes. These hide behind scam-awareness wording,
    # so (like links) they are exempt from the warning-context damping.
    *_r("recovery", 40, "Offers to recover lost or scammed money",
        r"\b(recover|retrieve|get back)\b.{0,25}\b(lost|stolen|scammed|your)\b.{0,15}\b(funds?|money|crypto|bitcoin|investments?|assets?|wallet)\b",
        r"\b(funds?|money|crypto|asset|scam|bitcoin|wallet) recovery\b",
        r"\brecovery (assistance|service|expert|agent|specialist|team|company|hacker)\b"),
    *_r("recovery", 30, "Asks victims to report a case privately",
        r"\b(report|file|submit|open)\b.{0,20}\b(case|complaint|claim)\b.{0,15}\b(via|by|through|in|on)\b.{0,6}\b(dm|inbox|messenger|message|pm|whatsapp|telegram)\b",
        r"\b(victims?|been scammed|got scammed)\b.{0,60}\b(dm|inbox|message|contact|reach out to)\b.{0,6}\b(us|me|our team)\b"),

    # --- moving the victim off-platform
    # Weighted above urgency: moving victims off-platform is among the most consistent
    # markers of Facebook investment, romance and marketplace scams.
    *_r("off_platform", 28, "Pushes contact to WhatsApp / Telegram / DMs",
        r"\b(dm|inbox|message|text|contact|whatsapp|telegram)\b.{0,20}\b(me|us)\b.{0,40}\b(whatsapp|telegram|signal|wickr)\b",
        r"\b(whatsapp|telegram)\b.{0,10}[:\-]?\s*\+?\d[\d\s\-]{7,}",
        r"\bt\.me/\w+", r"\bwa\.me/\d+"),
    *_r("off_platform", 10, "Inbox-me-for-details pattern",
        r"\b(inbox|dm|pm) (me|us) (for|to) (more )?(details|info|how|the link|join)\b"),

    # --- marketplace / rental / romance patterns common on Facebook
    *_r("marketplace", 20, "Deposit before viewing / shipping-only sale",
        r"\b(deposit|pay)\b.{0,30}\b(before|to) (view|viewing|see|hold|reserve)\b",
        r"\b(i am|i'm) (currently )?(out of (town|the country)|deployed|overseas)\b.{0,80}\b(ship|courier|deposit|keys)\b"),
]

# Context that suggests the post is *about* scams (warning others, news), not one itself.
WARNING_CONTEXT = re.compile(
    r"\b(beware|be careful|watch out|warning|psa|heads up|don'?t fall for|do not fall for|"
    r"is a scam|was a scam|this scam|scammers?|(scam|fraud) (alert|advisory|warning)|report(ed)? (it|this|them)|"
    r"never (send|share|give)|police (say|said|warn)|according to (police|the ftc|officials))\b",
    re.I,
)

CATEGORY_LABELS = {
    "payment_request": "Payment request",
    "credential_request": "Credential / data request",
    "urgency": "Urgency & pressure",
    "giveaway": "Prize / giveaway",
    "investment": "Investment scheme",
    "impersonation": "Impersonation",
    "off_platform": "Off-platform contact",
    "marketplace": "Marketplace pattern",
    "recovery": "Fund-recovery scheme",
    "author": "Suspicious page name",
    "links": "Suspicious links",
}

# Categories whose evidence is not weakened when a post talks *about* scams.
UNDAMPED_CATEGORIES = {"recovery", "author", "links"}

# Signals from the author / page display name.
AUTHOR_RULES: list[Rule] = [
    *_r("author", 35, "Page name advertises fund recovery", r"recover|retriev|refund ?(agent|service|team)"),
    *_r("author", 25, "Page name looks like a giveaway / prize page",
        r"\b(give ?aways?|lottery|jackpot|sweepstakes?|prizes?|grants?|winners?)\b"),
    *_r("author", 25, "Page name imitates an authority or support team",
        r"\b(fraud|cyber ?crimes?|scam)\b.{0,15}\b(division|unit|department|bureau|desk|agency|center|centre)\b",
        r"\b(support|help|security|verification|recovery) (team|center|centre|desk)\b"),
]


@dataclass
class RuleHit:
    category: str
    weight: int
    label: str
    evidence: str


def match_rules(text: str) -> list[RuleHit]:
    # Each pattern counts once; distinct patterns under one label are independent evidence.
    hits = []
    for rule in RULES:
        m = rule.pattern.search(text)
        if m:
            hits.append(RuleHit(rule.category, rule.weight, rule.label, m.group(0)[:80]))
    return hits


def match_author(author_name: str | None) -> list[RuleHit]:
    if not author_name:
        return []
    return [RuleHit(r.category, r.weight, r.label, author_name[:80])
            for r in AUTHOR_RULES if r.pattern.search(author_name)]


def is_warning_context(text: str) -> bool:
    return bool(WARNING_CONTEXT.search(text))
