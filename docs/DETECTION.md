# Detection engine

The engine code is `backend/app/detection/` and has no Facebook dependency. Its input is `ContentItem(text, urls, author_name)` and its output is an `Analysis`. Facebook-specific code (`app/platforms/facebook/`) only builds that input.

## Signals

| Category | Examples (phrase-level patterns, not single keywords) |
|---|---|
| `payment_request` | pay by gift card / crypto / wire; "processing/release fee"; Cash App/Zelle to strangers |
| `credential_request` | send password / OTP / 6-digit code / seed phrase; fake "your page will be disabled… verify" notices |
| `urgency` | "within 24 hours", "only 3 spots left", threats of deletion or arrest |
| `giveaway` | "you've been selected/won", "claim your prize", like-share-comment-to-win |
| `investment` | guaranteed returns, "invest $X earn $Y in N hours", crypto doubling, fund-recovery and "account manager" pitches |
| `impersonation` | "Meta Support Team", "official admin… click", celebrity-endorsed crypto |
| `off_platform` | move to WhatsApp/Telegram, phone numbers, t.me / wa.me links |
| `marketplace` | deposit before viewing; "I'm overseas, I'll ship the keys" |
| `links` | (urls.py) raw-IP hosts, punycode, `user@host` tricks, shorteners, abused TLDs, look-alike brand domains (homoglyph/typo/brand + "login"/"support"/…), credential words in the path, plain http |

URLs are **parsed only, never fetched**, so there is no SSRF exposure. `l.facebook.com/l.php?u=` wrappers are unwrapped locally. The optional Google Safe Browsing lookup sends URLs to Google's fixed API endpoint and never visits them.

**Discussion damping.** Posts that warn about or report on scams ("beware", "PSA", "this is a scam", "police said") get their text-category sub-scores multiplied by 0.4. Link signals are not damped.

**Obfuscation.** Text is NFKC-normalised and zero-width characters are stripped before matching.

## Scoring

1. Category sub-score = sum of matched pattern weights, capped at 100 (each pattern counts once).
2. `links` sub-score = highest per-URL risk. A Safe Browsing match sets it to 100.
3. `rules_score = 100 × (1 − ∏(1 − sub/100))`. Independent evidence accumulates: one moderate signal stays "suspicious", while several strong ones reach "likely scam".
4. If Gemini is configured: `score = 0.65 × llm + 0.35 × rules_score`. Models are tried in order, `GEMINI_MODEL` then `GEMINI_FALLBACK_MODELS`. A model that times out, is overloaded, is over quota (429) or is unavailable is skipped for 5 minutes. If every model fails, the score falls back to rules only and `scoring_method` says so. That result is not cached, so the next scrape retries Gemini. Posts are scored `LLM_WORKERS` (default 4) at a time.
5. A Safe Browsing match floors the final score at 90.
6. Category is decided from the score using the user's sensitivity: Normal 35/70 (the Twitter page's bands), Low 45/80, High 25/60.
7. If there is less than 15 characters of text and no links, the result is `unable_to_verify` with `score: null`, and the scraper does not store the post.
8. Page-name signals (`AUTHOR_RULES`) and fund-recovery signals are never damped by warning language, because recovery scams hide behind scam-awareness wording.

The weights in `rules.py` are hand-set starting points, **not fitted to data**, and no accuracy is claimed. `RULESET_VERSION` and the LLM model are recorded with every analysis (`engine_version`, also stored in `model_versions`).

## Improving it

- **Measure first.** `fb_feedback` records `verdict`, `score_at_time`, `category_at_time`, `engine_version`, and the reported excerpt. Precision on flagged posts ≈ 1 − false_positive / flagged-with-feedback. Feedback is self-selected, so for honest numbers, label a random sample of `fb_post_analyses` by hand.
- **Training a model.** Use only data you are licensed to use: your users' consented feedback, or public research corpora whose licences allow it. You need labelled posts (scam / not scam) that include hard negatives (scam warnings, crypto news, normal marketplace listings). Train a text classifier, for example a fine-tuned multilingual transformer, on a held-out split stratified by time. Report PR-AUC and precision at the operating thresholds. Calibrate it (isotonic/Platt) before exposing a `confidence`. Deploy it as another signal in `engine.analyze`, alongside the existing rules and LLM, and bump `RULESET_VERSION`.
- Rules, prompt and model can all change without touching the scraper or the dashboard, which only render `spam_score`, `spam_reason` and `category`. After a change, bump `RULESET_VERSION` and run `python rescore.py`.
