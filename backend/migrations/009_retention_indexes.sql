-- Retention purge deletes by date across all users. Without these indexes each DELETE scanned and
-- locked whole tables, and could deadlock with logins and scraper inserts (MySQL error 1213).
-- Additive only: no data is changed, and older code works with these indexes in place.
CREATE INDEX ix_audit_events_created ON audit_events (created_at);
CREATE INDEX ix_fb_post_analyzed ON fb_post_analyses (analyzed_at);
