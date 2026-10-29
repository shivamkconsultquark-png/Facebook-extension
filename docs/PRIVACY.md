# Privacy and platform notes

**What is stored.**
- **The Facebook session.** These are the cookies you import, or the ones created by logging in through the Chrome window. They are encrypted with Fernet (`SESSION_ENCRYPTION_KEY`, or an auto-generated `backend/.session_key`) and kept until you click **Switch Facebook Account**.
- **Each scraped post**, for `RETENTION_DAYS` (default 30), then deleted automatically. This covers the author's display name, profile link and avatar URL, the first `POST_TEXT_MAX_CHARS` characters of text (turn off with `STORE_POST_TEXT=false`), up to 10 image URLs (or a video's thumbnail), the post type (photo/video/reel), link domains, reaction/comment/share counts, and the score and explanation.
- **Not stored:** passwords, your messages, friend lists, or anything outside the home feed.

**What leaves the server.** Only if configured: post text goes to Google Gemini for scoring (`GEMINI_API_KEY`), and link addresses go to Google Safe Browsing (`SAFE_BROWSING_API_KEY`). Links in posts are never opened.

**What the scraper does on Facebook.** It opens your home feed and then the Reels tab, where it watches up to `SCRAPER_REELS` reels by pressing "Next". For new multi-photo posts it opens the photo viewer and presses "Next photo" through the album. When a dashboard user presses play, the video loads from Facebook's own embedded player in their browser; the backend never downloads videos. It scrolls the feed, clicks Facebook's own "See more" text expanders, and sends a hover event to each post's timestamp, which is how Facebook reveals the post's link. It never likes, comments, posts, reports, messages, or changes settings.

**Platform terms.** Meta's terms restrict automated collection from Facebook. Using your own session this way may lead Facebook to ask for a security check, log the session out, or restrict the account. The scraper uses no anti-detection techniques. If Facebook shows a login or checkpoint page, it stops scraping and reports "login required" until you import a fresh session. Use it only on accounts you own, and keep the interval reasonable (the default is 5 minutes). Longer intervals are gentler.
