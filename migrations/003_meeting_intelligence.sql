-- 003_meeting_intelligence.sql: Meeting transcript ingestion, commitment tracking, pattern detection

-- Meeting transcripts: stores metadata about ingested Otter recordings
CREATE TABLE IF NOT EXISTS meetings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    otter_speech_id TEXT UNIQUE,          -- Otter's speech/meeting ID
    title TEXT NOT NULL,
    meeting_type TEXT,                     -- 'pipeline_triage', 'pipeline_review', 'other'
    organizer TEXT,                        -- who organized the meeting
    start_time TEXT NOT NULL,              -- ISO 8601
    end_time TEXT,
    duration_minutes INTEGER,
    attendees TEXT,                        -- JSON array of attendee names/emails
    summary TEXT,                          -- AI-generated summary
    transcript_text TEXT,                  -- full transcript (for re-processing)
    merchants_mentioned TEXT,             -- JSON array of merchant names found
    processed_at TEXT DEFAULT (datetime('now')),
    created_at TEXT DEFAULT (datetime('now'))
);

-- Commitments: extracted action items from meetings
CREATE TABLE IF NOT EXISTS commitments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    meeting_id INTEGER NOT NULL REFERENCES meetings(id),
    assignee_name TEXT NOT NULL,           -- "Henry", "Hannah", "Drew"
    assignee_ghl_id TEXT,                  -- GHL user ID if matched
    action TEXT NOT NULL,                  -- "Submit MPA for Moon Tide"
    merchant_name TEXT,                    -- extracted merchant name
    opportunity_id TEXT,                   -- matched GHL opp ID (NULL if unmatched)
    deadline TEXT,                         -- extracted deadline (ISO 8601 or "this week")
    status TEXT DEFAULT 'open',            -- 'open', 'fulfilled', 'missed', 'dismissed'
    fulfilled_at TEXT,                     -- when status changed to fulfilled
    fulfilled_evidence TEXT,              -- what confirmed it (stage move, task completed, etc.)
    source_quote TEXT,                     -- the exact transcript excerpt
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

-- Pipeline patterns: accumulated observations about deal behavior
CREATE TABLE IF NOT EXISTS pipeline_patterns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pattern_type TEXT NOT NULL,            -- 'stall_point', 'lead_source_processor', 'commitment_miss', 'recurring_topic'
    entity_type TEXT,                      -- 'opportunity', 'user', 'stage', 'lead_source'
    entity_id TEXT,                        -- the specific entity (opp_id, user_id, stage_id, etc.)
    pattern_key TEXT NOT NULL UNIQUE,      -- unique key for deduplication
    description TEXT NOT NULL,             -- human-readable description
    evidence TEXT NOT NULL,                -- JSON array of supporting data points
    confidence REAL DEFAULT 0.0,           -- 0.0-1.0 based on evidence strength
    occurrences INTEGER DEFAULT 1,         -- how many times observed
    first_seen TEXT DEFAULT (datetime('now')),
    last_seen TEXT DEFAULT (datetime('now')),
    actionable INTEGER DEFAULT 0,          -- 1 if Atlas should surface this
    created_at TEXT DEFAULT (datetime('now'))
);

-- Digest feedback: tracks whether digest items were acted upon
CREATE TABLE IF NOT EXISTS digest_feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    audit_snapshot_id INTEGER,
    finding_key TEXT,                       -- same key format as tracker.py
    feedback_type TEXT NOT NULL,            -- 'acted_on', 'dismissed', 'escalated'
    user_id TEXT,
    notes TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_meetings_type ON meetings(meeting_type);
CREATE INDEX IF NOT EXISTS idx_meetings_date ON meetings(start_time);
CREATE INDEX IF NOT EXISTS idx_commitments_status ON commitments(status);
CREATE INDEX IF NOT EXISTS idx_commitments_assignee ON commitments(assignee_ghl_id);
CREATE INDEX IF NOT EXISTS idx_commitments_opp ON commitments(opportunity_id);
CREATE INDEX IF NOT EXISTS idx_patterns_type ON pipeline_patterns(pattern_type);
CREATE INDEX IF NOT EXISTS idx_patterns_key ON pipeline_patterns(pattern_key);
CREATE INDEX IF NOT EXISTS idx_digest_feedback_snapshot ON digest_feedback(audit_snapshot_id);
