-- Accountability items: tracks per-finding assignments, button responses, GHL verification
CREATE TABLE IF NOT EXISTS accountability_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    finding_key TEXT NOT NULL,
    opp_id TEXT NOT NULL,
    opp_name TEXT NOT NULL,
    category TEXT NOT NULL,
    field_name TEXT,
    severity TEXT NOT NULL,
    description TEXT NOT NULL,
    suggested_action TEXT,
    assigned_to_ghl TEXT NOT NULL,
    assigned_to_slack TEXT,
    status TEXT NOT NULL DEFAULT 'open',
    snooze_until TEXT,
    button_clicked_by TEXT,
    button_clicked_at TEXT,
    ghl_verified_at TEXT,
    ghl_field_value TEXT,
    first_seen_at TEXT NOT NULL DEFAULT (datetime('now')),
    resolved_at TEXT,
    run_date TEXT NOT NULL DEFAULT (date('now')),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(finding_key, run_date)
);

CREATE INDEX IF NOT EXISTS idx_accountability_status ON accountability_items(status);
CREATE INDEX IF NOT EXISTS idx_accountability_assigned ON accountability_items(assigned_to_ghl);
CREATE INDEX IF NOT EXISTS idx_accountability_opp ON accountability_items(opp_id);
CREATE INDEX IF NOT EXISTS idx_accountability_created ON accountability_items(created_at);

-- CEO mirror log: records every action Atlas takes
CREATE TABLE IF NOT EXISTS ceo_action_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    action_type TEXT NOT NULL,
    recipient_ghl TEXT,
    recipient_slack TEXT,
    summary TEXT NOT NULL,
    detail TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_ceo_log_date ON ceo_action_log(created_at);
CREATE INDEX IF NOT EXISTS idx_ceo_log_type ON ceo_action_log(action_type);
