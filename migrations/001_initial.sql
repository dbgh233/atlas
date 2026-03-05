-- 001_initial.sql: Atlas foundation schema
-- Enable WAL mode for concurrent read/write
PRAGMA journal_mode=WAL;

-- Dead Letter Queue: failed webhook payloads for investigation and replay
CREATE TABLE IF NOT EXISTS dead_letter_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,
    payload TEXT NOT NULL,
    error_message TEXT NOT NULL,
    error_context TEXT,
    retry_count INTEGER DEFAULT 0,
    status TEXT DEFAULT 'pending',
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

-- Audit Snapshots: daily audit results for trend tracking
CREATE TABLE IF NOT EXISTS audit_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_date TEXT NOT NULL,
    run_type TEXT DEFAULT 'scheduled',
    total_opportunities INTEGER NOT NULL,
    total_issues INTEGER NOT NULL,
    issues_by_type TEXT NOT NULL,
    full_results TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now'))
);

-- Interaction Log: every human interaction with Atlas
CREATE TABLE IF NOT EXISTS interaction_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    interaction_type TEXT NOT NULL,
    user_id TEXT NOT NULL,
    channel_id TEXT,
    opportunity_id TEXT,
    field_name TEXT,
    old_value TEXT,
    new_value TEXT,
    context TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

-- Idempotency Keys: prevent duplicate webhook processing
CREATE TABLE IF NOT EXISTS idempotency_keys (
    key TEXT PRIMARY KEY,
    event_type TEXT NOT NULL,
    processed_at TEXT DEFAULT (datetime('now')),
    result TEXT
);

-- Indexes for common query patterns
CREATE INDEX IF NOT EXISTS idx_dlq_status ON dead_letter_queue(status);
CREATE INDEX IF NOT EXISTS idx_audit_date ON audit_snapshots(run_date);
CREATE INDEX IF NOT EXISTS idx_interaction_opp ON interaction_log(opportunity_id);
CREATE INDEX IF NOT EXISTS idx_idempotency_time ON idempotency_keys(processed_at);
