-- 008: photo posts carrying an invisible <video> placeholder were tagged 'video'. Their image is a
-- photo (fbcdn path t39.*), while real videos/reels have a thumbnail (t15.* / t51.*).
UPDATE fb_post_analyses SET media_type = 'photo'
    WHERE media_type = 'video' AND video_url IS NULL AND media_url LIKE '%/v/t39.%';
