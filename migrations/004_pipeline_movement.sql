-- 004_pipeline_movement.sql: Weekly pipeline movement tracking
-- Stores snapshots of deal stage changes for weekly wrap-up reports

-- Pipeline movement events: individual deal stage transitions detected in a period
CREATE TABLE IF NOT EXISTS pipeline_movements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    opp_id TEXT NOT NULL,
    opp_name TEXT NOT NULL,
    assigned_to TEXT,
    assigned_name TEXT,
    from_stage_id TEXT,
    from_stage_name TEXT,
    to_stage_id TEXT NOT NULL,
    to_stage_name TEXT NOT NULL,
    monetary_value REAL DEFAULT 0,
    movement_type TEXT NOT NULL,  -- 'advanced', 'new_deal', 'lost', 'won'
    detected_at TEXT DEFAULT (datetime('now')),
    week_start TEXT NOT NULL,     -- ISO date of the Monday starting the week
    created_at TEXT DEFAULT (datetime('now'))
);

-- Weekly pipeline snapshots: aggregate stats per week for trend comparison
CREATE TABLE IF NOT EXISTS pipeline_weekly_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    week_start TEXT NOT NULL UNIQUE,  -- ISO date of Monday
    total_open_deals INTEGER NOT NULL DEFAULT 0,
    total_pipeline_value REAL NOT NULL DEFAULT 0,
    deals_advanced INTEGER NOT NULL DEFAULT 0,
    deals_new INTEGER NOT NULL DEFAULT 0,
    deals_lost INTEGER NOT NULL DEFAULT 0,
    deals_won INTEGER NOT NULL DEFAULT 0,
    deals_by_stage TEXT NOT NULL DEFAULT '{}',  -- JSON: {stage_name: count}
    value_by_stage TEXT NOT NULL DEFAULT '{}',  -- JSON: {stage_name: total_value}
    summary_json TEXT NOT NULL DEFAULT '{}',    -- Full categorized movement data
    created_at TEXT DEFAULT (datetime('now'))
);

-- Opportunity stage cache: tracks last-known stage per opp for change detection
CREATE TABLE IF NOT EXISTS opp_stage_cache (
    opp_id TEXT PRIMARY KEY,
    opp_name TEXT NOT NULL,
    stage_id TEXT NOT NULL,
    stage_name TEXT NOT NULL,
    assigned_to TEXT,
    monetary_value REAL DEFAULT 0,
    last_updated TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_pipeline_movements_week ON pipeline_movements(week_start);
CREATE INDEX IF NOT EXISTS idx_pipeline_movements_opp ON pipeline_movements(opp_id);
CREATE INDEX IF NOT EXISTS idx_pipeline_movements_type ON pipeline_movements(movement_type);
CREATE INDEX IF NOT EXISTS idx_opp_stage_cache_stage ON opp_stage_cache(stage_id);
