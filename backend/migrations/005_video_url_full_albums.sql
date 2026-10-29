-- 005: the video's own page (for Facebook's embedded player) and full multi-photo albums.
ALTER TABLE fb_post_analyses
    ADD COLUMN video_url      VARCHAR(512) NULL AFTER media_type,           -- /reel/ID, /videos/ID or /watch/?v=ID
    ADD COLUMN album_complete TINYINT(1) NOT NULL DEFAULT 0 AFTER video_url; -- every photo fetched from the viewer

-- Reels stored before this migration: their permalink is the video page.
UPDATE fb_post_analyses SET video_url = permalink
    WHERE video_url IS NULL AND media_type IN ('reel', 'video')
      AND (permalink LIKE '%/reel/%' OR permalink LIKE '%/videos/%' OR permalink LIKE '%watch%v=%');
