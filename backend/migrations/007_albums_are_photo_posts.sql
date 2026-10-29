-- 007: multi-photo albums that contained a video element were tagged 'video'.
UPDATE fb_post_analyses SET media_type = 'photo'
    WHERE media_type = 'video' AND JSON_LENGTH(media_urls) >= 2;
