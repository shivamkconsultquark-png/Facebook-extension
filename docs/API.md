# API contract

Every endpoint mirrors one on the Twitter page. All need `Authorization: Bearer <token>`, except `/health`, `/health/live`, `/auth/register` and `/auth/login`.

## Health (no auth)
- `GET /health/live` → `{"status": "ok", "version": "1.1.0"}`. The process answers; the database isn't touched. Used by the supervisor to detect a hung backend.
- `GET /health` → 200 when ready, 503 otherwise. Fields: `status` (`ok`/`degraded`), `version`, `database`, `migrations_pending` (list, or `null` if the DB is unreachable), `config_problems`, `scrapers` (`{running, dead_workers}`), `disk_free_mb`, `warnings`, `llm_enabled`, `safe_browsing_enabled`, `engine_version`, `time`. Earlier fields are unchanged; the new ones were only added. Errors use `{"detail": "..."}`. Validation errors use `{"detail": "Invalid request", "errors": [{loc, msg}]}` and never echo the submitted values. Bodies over 512 KB return 413.

| Facebook | Twitter equivalent | Used by |
|---|---|---|
| `GET /facebook/feed` | `GET /twitter/feed` | FacebookInsights timeline |
| `GET /facebook/config` | `GET /twitter/config` | stats strip, Sync Details, Spam Analysis |
| `GET /facebook/browser-scraper/status` | `GET /twitter/browser-scraper/status` | header chip (10 s poll), scraper page (5 s poll) |
| `POST /facebook/browser-scraper/start` | `POST /twitter/browser-scraper/start` | **Start Auto-Scraper** |
| `POST /facebook/browser-scraper/stop` | `POST /twitter/browser-scraper/stop` | **Stop Auto-Scraper** |
| `POST /facebook/browser-scraper/switch-account` | `POST /twitter/browser-scraper/switch-account` | **Switch Facebook Account** |
| `POST /facebook/browser-scraper/import-session` | `POST /twitter/browser-scraper/import-session` | **Import Session** |
| `POST /facebook/browser-scraper/login-window` | (Start's login window) | **Log in to Facebook** |
| `GET/POST /facebook/settings/llm-instructions` | same path under `/twitter` | **Spam Score LLM Instructions** |

## Auth (standalone mode)
| Method | Path | Body | Returns |
|---|---|---|---|
| POST | `/auth/register` | `{email, password(≥8), label?}` | 201 `{token, user}` · 409 if exists · 403 if `ALLOW_REGISTRATION=false` |
| POST | `/auth/login` | same | `{token, user}` · 401 · 429 (10 attempts / 5 min per email) |
| GET | `/auth/me` | | `{id, email}` |
| POST | `/auth/logout` | | revokes the current token |

## Browser scraper
`GET /facebook/browser-scraper/status`
```json
{
  "running": true, "scraping": false, "needs_login": false,
  "message": "✅ Running — scraping your Facebook feed every 5 minutes",
  "error": null,
  "next_run_at": "2026-10-27T08:15:00Z", "last_run": "2026-10-27T08:10:04Z",
  "cycles": 3, "last_count": 41, "total_scraped": 97,
  "interval_seconds": 300,
  "fb_account": "Jane Doe",
  "fb_account_url": "https://www.facebook.com/profile.php?id=1000…",
  "login_window_open": false, "login_window_available": true,
  "ai_scoring": {"enabled": true, "model": "gemini-flash-lite-latest", "working": true, "last_error": null}
}
```
`ai_scoring.working` is `null` until the first Gemini call in this process, and `false` when the latest call failed (`last_error` says why; posts are then scored by rules only).
The Twitter version's `x_account` is `fb_account` here, plus `fb_account_url`, since Facebook names aren't handles.

- `POST start` `{interval_seconds}`: must be one of the dropdown values (120 … 86400), otherwise 422. Without a linked account it opens the login window, or returns 400 if `SCRAPER_ALLOW_LOGIN_WINDOW=false`.
- `POST stop`, `POST switch-account`: return the new status. Switch-account also deletes the stored session.
- `POST import-session` `{cookies}`: takes the Cookie-Editor JSON list, a `{name: value}` object, or a `name=value; …` string. The cookies are checked by loading facebook.com headless. Always returns 200 with `{ok, detail}`, like the Twitter endpoint; `ok: false` carries a user-facing reason.
- `POST login-window`: opens a visible Chrome window on facebook.com/login on the machine running the backend, and waits up to `SCRAPER_LOGIN_TIMEOUT` seconds.

Scrapers that were running resume when the backend restarts (state is kept in `user_settings.scraper_state`).

## Feed
`GET /facebook/feed?page=&limit=(≤50)&category=(clean|suspicious|spam or likely_legitimate|likely_scam)&search=&date_from=YYYY-MM-DD&date_to=YYYY-MM-DD&hide_above=0-100&sort=(newest|oldest|score_desc|score_asc)&media=(all|posts|videos|reels)`

`date_from` and `date_to` must be real calendar dates, and `date_from` must be on or before `date_to`. Otherwise the request returns 422 (`"date_from must be on or before date_to"` or `"date_from is not a real date"`).

`media`: `posts` returns photo and text posts, `videos` returns videos and reels, `reels` returns reels only. Each item also carries:
- `media_urls`: every photo of the post, up to 30. For "+N" albums these come from Facebook's photo viewer.
- `media_more`: photos of the album that are not stored, usually 0.
- `video_url`: the video's own page (`/reel/ID`, `/videos/ID`, `/watch/?v=ID`) for Facebook's embedded player, or `null`.
- `media_type`: `photo`, `video`, `reel` or `null` for text-only posts.

For a video or reel, `media_urls[0]` is the thumbnail.
```json
{"feed": [{
   "id": 12, "post_key": "fb:pfbid02…", "permalink": "https://www.facebook.com/…/posts/…",
   "user": {"name": "Page name", "url": "https://www.facebook.com/page", "profile_image_url": "https://scontent…"},
   "text": "…", "media_url": "https://scontent…", "link_domains": ["claim-prize.xyz"],
   "reactions_count": "1.2K", "comments_count": "12", "shares_count": "3",
   "spam_score": 82, "category": "likely_scam", "spam_reason": "Suspicious page name, Prize / giveaway",
   "indicators": [{"category": "giveaway", "label": "…", "weight": 22, "evidence": "…"}],
   "scoring_method": "rules", "created_at": "…Z", "analyzed_at": "…Z"
 }], "total": 139, "total_pages": 14, "page": 1}
```
`GET /facebook/feed/{id}/playable` → `{playable: true|false|null}`: whether Facebook's embedded player can play this post's video. `false` means Facebook would show "Video unavailable" (embedding turned off, licensed music, sponsored or non-public reel), so the dashboard shows a "Watch on Facebook" card instead. `null` means Facebook couldn't be asked. Results are cached for an hour. 404 if the post has no video or isn't the user's.

`GET /facebook/config` → `{config: {total_posts, total_videos, unique_authors, spam_clean, spam_suspicious, spam_spam, unverified, last_synced_at, retention_days}}`, the same bucket names as `/twitter/config`.
