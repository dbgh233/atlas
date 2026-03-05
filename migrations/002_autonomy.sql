-- 002_autonomy.sql: Graduated autonomy — confidence scoring and auto-fix tracking

-- Fix type confidence: tracks approval rates per fix type for auto-promotion
CREATE TABLE IF NOT EXISTS fix_type_confidence (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fix_type TEXT NOT NULL UNIQUE,
    total_suggestions INTEGER DEFAULT 0,
    total_approvals INTEGER DEFAULT 0,
    total_rejections INTEGER DEFAULT 0,
    approval_rate REAL DEFAULT 0.0,
    status TEXT DEFAULT 'suggest',  -- 'suggest' or 'auto_fix'
    promoted_at TEXT,               -- when auto-promoted (NULL if still suggest)
    reverted_at TEXT,               -- when reverted from auto_fix to suggest
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

-- Auto-fix log: tracks every auto-applied fix for digest and undo
CREATE TABLE IF NOT EXISTS auto_fix_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fix_type TEXT NOT NULL,
    opportunity_id TEXT NOT NULL,
    opportunity_name TEXT,
    field_id TEXT NOT NULL,
    field_name TEXT NOT NULL,
    old_value TEXT,
    new_value TEXT NOT NULL,
    undone INTEGER DEFAULT 0,
    undone_at TEXT,
    undone_by TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_fix_type_status ON fix_type_confidence(status);
CREATE INDEX IF NOT EXISTS idx_autofix_type ON auto_fix_log(fix_type);
CREATE INDEX IF NOT EXISTS idx_autofix_opp ON auto_fix_log(opportunity_id);
CREATE INDEX IF NOT EXISTS idx_autofix_undone ON auto_fix_log(undone);
