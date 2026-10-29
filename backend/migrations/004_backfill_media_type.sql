-- 004: posts stored before 003 have no media_type; infer it from the permalink / image.
UPDATE fb_post_analyses SET media_type = 'reel'
    WHERE media_type IS NULL AND permalink LIKE '%/reel/%';
UPDATE fb_post_analyses SET media_type = 'video'
    WHERE media_type IS NULL AND (permalink LIKE '%/videos/%' OR permalink LIKE '%/watch/%' OR permalink LIKE '%watch?v=%');
UPDATE fb_post_analyses SET media_type = 'photo'
    WHERE media_type IS NULL AND media_url IS NOT NULL;
