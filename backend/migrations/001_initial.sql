-- 001: initial schema for the Facebook scam-detection backend.
-- Data minimisation: only what the dashboard and feedback loop need. Post text is an
-- optional, truncated excerpt (STORE_POST_TEXT / POST_TEXT_MAX_CHARS) and every analysis
-- row is purged after RETENTION_DAYS.

CREATE TABLE IF NOT EXISTS users (
    id            INT UNSIGNED NOT NULL AUTO_INCREMENT,
    email         VARCHAR(255) NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    created_at    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_users_email (email)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Opaque bearer tokens; only the SHA-256 of the token is stored.
CREATE TABLE IF NOT EXISTS api_tokens (
    id           INT UNSIGNED NOT NULL AUTO_INCREMENT,
    user_id      INT UNSIGNED NOT NULL,
    token_hash   CHAR(64) NOT NULL,
    label        VARCHAR(64) NOT NULL DEFAULT 'extension',
    created_at   TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_used_at TIMESTAMP NULL,
    expires_at   TIMESTAMP NOT NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uq_api_tokens_hash (token_hash),
    KEY ix_api_tokens_user (user_id),
    CONSTRAINT fk_api_tokens_user FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Key/value settings per user (mirrors {prefix}twitter_settings: llm_instructions, preferences).
CREATE TABLE IF NOT EXISTS user_settings (
    user_id       INT UNSIGNED NOT NULL,
    setting_key   VARCHAR(100) NOT NULL,
    setting_value TEXT,
    updated_at    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, setting_key),
    CONSTRAINT fk_user_settings_user FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Detector versions that produced scores, so feedback can be tied to a ruleset/model.
CREATE TABLE IF NOT EXISTS model_versions (
    version     VARCHAR(64) NOT NULL,
    description VARCHAR(255) NULL,
    created_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (version)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Content-addressed cache of scores (hash of normalised text + links + instructions).
-- Holds no post text, so it can be shared safely and expires with retention.
CREATE TABLE IF NOT EXISTS analysis_cache (
    content_hash   CHAR(64) NOT NULL,
    engine_version VARCHAR(64) NOT NULL,
    score          TINYINT UNSIGNED NOT NULL,
    result_json    JSON NOT NULL,
    created_at     TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (content_hash, engine_version),
    KEY ix_analysis_cache_created (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- One row per (user, Facebook post) the user's extension analysed. Feeds the dashboard.
CREATE TABLE IF NOT EXISTS fb_post_analyses (
    id             BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    user_id        INT UNSIGNED NOT NULL,
    platform       VARCHAR(20) NOT NULL DEFAULT 'facebook',
    post_key       VARCHAR(191) NOT NULL,
    permalink      VARCHAR(512) NULL,
    author_name    VARCHAR(255) NULL,
    author_url     VARCHAR(512) NULL,
    text_excerpt   TEXT NULL,
    content_hash   CHAR(64) NOT NULL,
    link_domains   JSON NULL,
    score          TINYINT UNSIGNED NOT NULL,
    category       ENUM('likely_legitimate','suspicious','likely_scam','unable_to_verify') NOT NULL,
    reason         VARCHAR(255) NULL,
    indicators     JSON NULL,
    scoring_method VARCHAR(32) NOT NULL,
    engine_version VARCHAR(64) NOT NULL,
    first_seen_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    analyzed_at    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_fb_post_user_key (user_id, post_key),
    KEY ix_fb_post_user_time (user_id, analyzed_at),
    KEY ix_fb_post_user_cat (user_id, category),
    KEY ix_fb_post_user_score (user_id, score),
    CONSTRAINT fk_fb_post_user FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- User corrections: the main signal for improving rules / prompts / a future model.
CREATE TABLE IF NOT EXISTS fb_feedback (
    id             BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    user_id        INT UNSIGNED NOT NULL,
    post_key       VARCHAR(191) NOT NULL,
    verdict        ENUM('false_positive','missed_scam','correct') NOT NULL,
    note           VARCHAR(500) NULL,
    -- Snapshot of the excerpt the user chose to report (user-initiated; FEEDBACK_RETENTION_DAYS).
    text_excerpt   TEXT NULL,
    score_at_time  TINYINT UNSIGNED NULL,
    category_at_time VARCHAR(32) NULL,
    engine_version VARCHAR(64) NULL,
    created_at     TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_fb_feedback_user_post (user_id, post_key),
    KEY ix_fb_feedback_verdict (verdict, created_at),
    CONSTRAINT fk_fb_feedback_user FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Extension heartbeat (replaces the Twitter browser-scraper status).
CREATE TABLE IF NOT EXISTS extension_sessions (
    user_id            INT UNSIGNED NOT NULL,
    extension_version  VARCHAR(32) NULL,
    enabled            TINYINT(1) NOT NULL DEFAULT 1,
    last_seen_at       TIMESTAMP NULL,
    last_batch_count   INT UNSIGNED NOT NULL DEFAULT 0,
    total_analyzed     BIGINT UNSIGNED NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id),
    CONSTRAINT fk_ext_sessions_user FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Minimal audit trail for security-relevant events (login, token revoke, settings change).
CREATE TABLE IF NOT EXISTS audit_events (
    id         BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    user_id    INT UNSIGNED NULL,
    event      VARCHAR(64) NOT NULL,
    detail     VARCHAR(255) NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    KEY ix_audit_events_user (user_id, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
