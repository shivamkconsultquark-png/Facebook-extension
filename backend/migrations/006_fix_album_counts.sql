-- 006: "+N" on the 5th feed tile counts that tile too (album = 4 + N photos), so rows saved
-- before this fix over-counted by one. Albums fetched from the viewer that went all the way
-- round (album_complete, fewer than the 30-photo cap) have nothing left on Facebook.
UPDATE fb_post_analyses SET media_more = 0
    WHERE album_complete = 1 AND media_more > 0 AND JSON_LENGTH(media_urls) < 30;
UPDATE fb_post_analyses SET media_more = media_more - 1
    WHERE album_complete = 0 AND media_more > 0 AND JSON_LENGTH(media_urls) >= 5;
