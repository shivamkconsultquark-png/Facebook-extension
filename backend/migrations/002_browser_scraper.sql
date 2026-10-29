-- 002: server-side Facebook browser scraper (replaces the Chrome extension).

-- Imported / captured Facebook session per dashboard user. Cookies are Fernet-encrypted.
CREATE TABLE IF NOT EXISTS fb_sessions (
    user_id      INT UNSIGNED NOT NULL,
    cookies_enc  MEDIUMTEXT NOT NULL,
    account_id   VARCHAR(64) NULL,
    account_name VARCHAR(255) NULL,
    created_at   TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at   TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id),
    CONSTRAINT fk_fb_sessions_user FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Fields the timeline shows, as on the Twitter page (media, avatar, engagement counts).
ALTER TABLE fb_post_analyses
    ADD COLUMN media_url       VARCHAR(1024) NULL AFTER text_excerpt,
    ADD COLUMN author_image    VARCHAR(1024) NULL AFTER author_url,
    ADD COLUMN reactions_count VARCHAR(16) NULL AFTER link_domains,
    ADD COLUMN comments_count  VARCHAR(16) NULL AFTER reactions_count,
    ADD COLUMN shares_count    VARCHAR(16) NULL AFTER comments_count;

-- The extension heartbeat table is no longer used.
DROP TABLE IF EXISTS extension_sessions;
