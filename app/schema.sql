-- ShortPulse schema. All timestamps are Unix epoch seconds (UTC).
-- The link record is the hub: rules, tags, click events and campaign all hang off it.

CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY,
    email         TEXT    NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT    NOT NULL,
    role          TEXT    NOT NULL DEFAULT 'user' CHECK (role IN ('user', 'admin')),
    -- active | suspended (can't sign in) | deleted (soft-deleted: links kept, disabled, for traceability)
    status        TEXT    NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'suspended', 'deleted')),
    last_login_at INTEGER,
    created_at    INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS campaigns (
    id          INTEGER PRIMARY KEY,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name        TEXT    NOT NULL,
    description TEXT    NOT NULL DEFAULT '',
    created_at  INTEGER NOT NULL,
    UNIQUE (user_id, name)
);

CREATE TABLE IF NOT EXISTS links (
    id              INTEGER PRIMARY KEY,
    user_id         INTEGER NOT NULL REFERENCES users(id),
    campaign_id     INTEGER REFERENCES campaigns(id) ON DELETE SET NULL,
    code            TEXT    NOT NULL UNIQUE,          -- short code or custom alias (case-sensitive)
    is_custom_alias INTEGER NOT NULL DEFAULT 0,
    destination     TEXT    NOT NULL,
    title           TEXT    NOT NULL DEFAULT '',
    -- active: redirects | paused: owner switched it off | disabled: admin takedown
    status          TEXT    NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'paused', 'disabled')),
    -- Redirects served to non-crawler visitors. This is the click-limit counter.
    -- Analytics (human/bot/unique) come from click_events instead.
    click_count     INTEGER NOT NULL DEFAULT 0,
    risk_score      REAL,                             -- URL model probability at create/edit time
    risk_reasons    TEXT    NOT NULL DEFAULT '',
    flagged         INTEGER NOT NULL DEFAULT 0,       -- waiting for admin review
    disabled_reason TEXT,
    disabled_by     INTEGER REFERENCES users(id),
    disabled_at     INTEGER,
    created_at      INTEGER NOT NULL,
    updated_at      INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_links_user     ON links(user_id);
CREATE INDEX IF NOT EXISTS idx_links_campaign ON links(campaign_id);
CREATE INDEX IF NOT EXISTS idx_links_flagged  ON links(flagged) WHERE flagged = 1;

-- One row per link. Kept separate so the redirect rules are explicit data.
CREATE TABLE IF NOT EXISTS rules (
    link_id       INTEGER PRIMARY KEY REFERENCES links(id) ON DELETE CASCADE,
    expires_at    INTEGER,                                            -- NULL = never
    max_clicks    INTEGER CHECK (max_clicks IS NULL OR max_clicks > 0), -- NULL = unlimited
    password_hash TEXT,                                               -- NULL = public
    open_in_app      INTEGER NOT NULL DEFAULT 1,   -- phones: try the YouTube/Spotify/Instagram app first
    track_engagement INTEGER NOT NULL DEFAULT 0    -- append ?lp_cid= for the owner's beacon script
);

CREATE TABLE IF NOT EXISTS tags (
    id      INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name    TEXT    NOT NULL,
    UNIQUE (user_id, name)
);

CREATE TABLE IF NOT EXISTS link_tags (
    link_id INTEGER NOT NULL REFERENCES links(id) ON DELETE CASCADE,
    tag_id  INTEGER NOT NULL REFERENCES tags(id)  ON DELETE CASCADE,
    PRIMARY KEY (link_id, tag_id)
);

CREATE TABLE IF NOT EXISTS click_events (
    id              INTEGER PRIMARY KEY,
    link_id         INTEGER NOT NULL REFERENCES links(id) ON DELETE CASCADE,
    ts              INTEGER NOT NULL,
    visitor_hash    TEXT    NOT NULL,       -- salted hash of IP + user agent; raw IPs are never stored
    source          TEXT    NOT NULL DEFAULT 'link' CHECK (source IN ('link', 'qr')),
    referrer_domain TEXT    NOT NULL DEFAULT '',
    user_agent      TEXT    NOT NULL DEFAULT '',
    device          TEXT    NOT NULL DEFAULT 'Other',
    browser         TEXT    NOT NULL DEFAULT 'Other',
    os              TEXT    NOT NULL DEFAULT 'Other',
    country         TEXT    NOT NULL DEFAULT 'Unknown',
    city            TEXT    NOT NULL DEFAULT '',
    is_bot          INTEGER NOT NULL DEFAULT 0,
    bot_score       REAL,
    bot_reason      TEXT    NOT NULL DEFAULT '',
    click_uid       TEXT,                           -- random id passed as ?lp_cid= (engagement only)
    ip_address      TEXT,                           -- full IP; erased after IP_RETENTION_DAYS
    ip_masked       TEXT    NOT NULL DEFAULT '',    -- 103.21.45.x / IPv6 /48: kept for analytics
    app_switch      INTEGER NOT NULL DEFAULT 0,     -- phone was offered the native app
    engaged_ms      INTEGER,                        -- time visible on the page (beacon)
    scroll_pct      INTEGER,                        -- deepest scroll on the page (beacon)
    engaged_at      INTEGER,                        -- last beacon: "reading now" if recent
    landing         INTEGER NOT NULL DEFAULT 0,     -- visitor saw the ShortPulse landing page
    outbound        TEXT                            -- landing-page button the visitor chose
);
CREATE INDEX IF NOT EXISTS idx_clicks_link_ts ON click_events(link_id, ts);
CREATE INDEX IF NOT EXISTS idx_clicks_ts      ON click_events(ts);

CREATE TABLE IF NOT EXISTS qr_designs (
    link_id    INTEGER PRIMARY KEY REFERENCES links(id) ON DELETE CASCADE,
    options    TEXT    NOT NULL DEFAULT '{}',   -- JSON: colours, styles, caption
    logo_png   BLOB,                            -- re-encoded, max 512x512
    updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS landing_pages (
    link_id      INTEGER PRIMARY KEY REFERENCES links(id) ON DELETE CASCADE,
    enabled      INTEGER NOT NULL DEFAULT 1,
    heading      TEXT    NOT NULL DEFAULT '',
    message      TEXT    NOT NULL DEFAULT '',
    button_label TEXT    NOT NULL DEFAULT 'Continue',
    extra_links  TEXT    NOT NULL DEFAULT '[]',   -- JSON [{label, url}], max 5
    theme        TEXT    NOT NULL DEFAULT 'ink',
    updated_at   INTEGER NOT NULL
);

-- Every admin action, for accountability. Never contains passwords.
CREATE TABLE IF NOT EXISTS admin_actions (
    id          INTEGER PRIMARY KEY,
    admin_id    INTEGER REFERENCES users(id),
    action      TEXT    NOT NULL,
    target_type TEXT    NOT NULL,   -- user | link | campaign | blocklist
    target_id   INTEGER,
    detail      TEXT    NOT NULL DEFAULT '',
    created_at  INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_admin_actions_target ON admin_actions(target_type, target_id);

CREATE TABLE IF NOT EXISTS blocklist (
    id         INTEGER PRIMARY KEY,
    domain     TEXT    NOT NULL UNIQUE,
    reason     TEXT    NOT NULL DEFAULT '',
    added_by   INTEGER REFERENCES users(id),
    created_at INTEGER NOT NULL
);
