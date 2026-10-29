-- 003: every photo of a post (not just the first), and post type so reels/videos can be filtered.

ALTER TABLE fb_post_analyses
    ADD COLUMN media_urls  TEXT NULL AFTER media_url,          -- JSON list of image URLs, in post order
    ADD COLUMN media_more  SMALLINT UNSIGNED NULL AFTER media_urls,  -- "+N" photos Facebook did not render
    ADD COLUMN media_type  VARCHAR(10) NULL AFTER media_more;   -- photo | video | reel | NULL (text only)

CREATE INDEX idx_fb_post_media_type ON fb_post_analyses (user_id, media_type);
