-- Track dismissed audit findings so they don't resurface in future digests.
-- Dismissals are created via Slack interactive buttons on the audit digest.

CREATE TABLE IF NOT EXISTS audit_dismissed (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    opp_id TEXT NOT NULL,
    category TEXT NOT NULL,
    field_name TEXT NOT NULL DEFAULT '',
    dismissed_by TEXT NOT NULL,         -- Slack user ID
    dismissed_at TEXT NOT NULL DEFAULT (datetime('now')),
    -- A finding is uniquely identified by opp_id + category + field_name
    UNIQUE(opp_id, category, field_name)
);

CREATE INDEX IF NOT EXISTS idx_audit_dismissed_opp
    ON audit_dismissed(opp_id);
