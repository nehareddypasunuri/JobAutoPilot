-- HirePilot — Initial PostgreSQL Schema (Supabase)
-- Migration: 001_initial_schema
-- Run with: psql $DATABASE_URL < 001_initial_schema.sql
--   or via Supabase dashboard → SQL editor
--
-- This schema is the authoritative multi-user data model.
-- SQLite (development) mirrors this structure without RLS policies.

-- ── Extensions ────────────────────────────────────────────────────────────
CREATE EXTENSION IF NOT EXISTS "pgcrypto";   -- gen_random_uuid()
CREATE EXTENSION IF NOT EXISTS "pg_trgm";    -- GIN index for company/role search

-- ════════════════════════════════════════════════════════════════════════
-- USERS
-- ════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS users (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email         TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,                 -- PBKDF2-HMAC-SHA256
    name          TEXT NOT NULL DEFAULT '',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_login    TIMESTAMPTZ,
    is_active     BOOLEAN NOT NULL DEFAULT TRUE, -- soft-disable without data loss
    plan          TEXT NOT NULL DEFAULT 'free'   -- free | pro (future)
        CHECK (plan IN ('free', 'pro'))
);

CREATE INDEX IF NOT EXISTS idx_users_email ON users (email);

-- ════════════════════════════════════════════════════════════════════════
-- USER PREFERENCES (per-user key-value store)
-- ════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS user_prefs (
    id         BIGSERIAL PRIMARY KEY,
    user_id    UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    key        TEXT NOT NULL,
    value      TEXT,                             -- JSON-serialized
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (user_id, key)
);

CREATE INDEX IF NOT EXISTS idx_prefs_user ON user_prefs (user_id);

-- ════════════════════════════════════════════════════════════════════════
-- JOBS (job applications per user)
-- ════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS jobs (
    id                BIGSERIAL PRIMARY KEY,
    user_id           UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    company           TEXT NOT NULL,
    role              TEXT NOT NULL,
    location          TEXT NOT NULL DEFAULT '',
    job_url           TEXT NOT NULL DEFAULT '',
    source            TEXT NOT NULL DEFAULT '',
    job_description   TEXT NOT NULL DEFAULT '',
    match_score       SMALLINT CHECK (match_score IS NULL OR (match_score >= 0 AND match_score <= 100)),
    resume_version    TEXT NOT NULL DEFAULT '',
    status            TEXT NOT NULL DEFAULT 'Found'
        CHECK (status IN (
            'Found', 'Applied', 'No Response',
            'Recruiter Screen', 'Phone Screen', 'Assessment',
            'Virtual Interview', 'Technical Interview', 'Final Round',
            'Offer', 'Rejected'
        )),
    date_found        DATE,
    date_applied      DATE,
    recruiter_email   TEXT NOT NULL DEFAULT '',
    referral_contact  TEXT NOT NULL DEFAULT '',
    notes             TEXT NOT NULL DEFAULT '',
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_jobs_user    ON jobs (user_id);
CREATE INDEX IF NOT EXISTS idx_jobs_status  ON jobs (user_id, status);
CREATE INDEX IF NOT EXISTS idx_jobs_score   ON jobs (user_id, match_score DESC NULLS LAST);
CREATE INDEX IF NOT EXISTS idx_jobs_created ON jobs (user_id, created_at DESC);
-- Full-text search on company and role
CREATE INDEX IF NOT EXISTS idx_jobs_company_trgm ON jobs USING GIN (company gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_jobs_role_trgm    ON jobs USING GIN (role gin_trgm_ops);

-- Auto-update updated_at on row change
CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER jobs_updated_at
    BEFORE UPDATE ON jobs
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

-- ════════════════════════════════════════════════════════════════════════
-- EMAILS (recruiter emails linked to jobs)
-- ════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS emails (
    id               BIGSERIAL PRIMARY KEY,
    user_id          UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    job_id           BIGINT REFERENCES jobs(id) ON DELETE CASCADE,
    gmail_id         TEXT,                        -- Gmail Message-ID header (dedup key)
    sender           TEXT NOT NULL DEFAULT '',
    subject          TEXT NOT NULL DEFAULT '',
    body             TEXT NOT NULL DEFAULT '',
    detected_status  TEXT,
    confidence_score NUMERIC(4,3) CHECK (confidence_score IS NULL OR confidence_score BETWEEN 0 AND 1),
    received_date    TIMESTAMPTZ,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (user_id, gmail_id)                   -- prevents duplicate sync
);

CREATE INDEX IF NOT EXISTS idx_emails_user ON emails (user_id);
CREATE INDEX IF NOT EXISTS idx_emails_job  ON emails (user_id, job_id);
CREATE INDEX IF NOT EXISTS idx_emails_date ON emails (user_id, received_date DESC);

-- ════════════════════════════════════════════════════════════════════════
-- DOCUMENTS (AI-generated resumes and cover letters)
-- ════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS documents (
    id         BIGSERIAL PRIMARY KEY,
    user_id    UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    job_id     BIGINT REFERENCES jobs(id) ON DELETE SET NULL,
    doc_type   TEXT NOT NULL CHECK (doc_type IN ('Tailored Resume', 'Cover Letter')),
    company    TEXT NOT NULL DEFAULT '',
    role       TEXT NOT NULL DEFAULT '',
    content    TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_docs_user ON documents (user_id);
CREATE INDEX IF NOT EXISTS idx_docs_job  ON documents (user_id, job_id);

-- ════════════════════════════════════════════════════════════════════════
-- NOTIFICATIONS
-- ════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS notifications (
    id         BIGSERIAL PRIMARY KEY,
    user_id    UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    type       TEXT,
    title      TEXT NOT NULL,
    body       TEXT NOT NULL DEFAULT '',
    job_id     BIGINT REFERENCES jobs(id) ON DELETE SET NULL,
    is_read    BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_notif_user  ON notifications (user_id);
CREATE INDEX IF NOT EXISTS idx_notif_unread ON notifications (user_id, is_read) WHERE NOT is_read;

-- ════════════════════════════════════════════════════════════════════════
-- DISCOVERED JOBS (Phase 2 — auto-found by job search engine)
-- ════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS discovered_jobs (
    id          BIGSERIAL PRIMARY KEY,
    user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    ext_id      TEXT NOT NULL,                   -- SHA1 fingerprint for dedup
    company     TEXT NOT NULL,
    role        TEXT NOT NULL,
    job_url     TEXT NOT NULL DEFAULT '',
    source_type TEXT,
    match_score SMALLINT,
    status      TEXT NOT NULL DEFAULT 'new'
        CHECK (status IN ('new', 'viewed', 'promoted', 'dismissed')),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (user_id, ext_id)
);

CREATE INDEX IF NOT EXISTS idx_disc_user  ON discovered_jobs (user_id);
CREATE INDEX IF NOT EXISTS idx_disc_score ON discovered_jobs (user_id, match_score DESC NULLS LAST);

-- ════════════════════════════════════════════════════════════════════════
-- ROW-LEVEL SECURITY (RLS)
-- ════════════════════════════════════════════════════════════════════════
-- Supabase/PostgREST uses RLS as the data access layer.
-- When using Supabase Auth, replace auth.uid()::text with the appropriate claim.
-- When using custom JWT (HirePilot's own auth), set current_setting per request.
--
-- For HirePilot's custom auth, set the session user_id before each request:
--   SET LOCAL app.current_user_id = '<user_id>';
-- Then RLS checks current_setting('app.current_user_id').

ALTER TABLE user_prefs      ENABLE ROW LEVEL SECURITY;
ALTER TABLE jobs            ENABLE ROW LEVEL SECURITY;
ALTER TABLE emails          ENABLE ROW LEVEL SECURITY;
ALTER TABLE documents       ENABLE ROW LEVEL SECURITY;
ALTER TABLE notifications   ENABLE ROW LEVEL SECURITY;
ALTER TABLE discovered_jobs ENABLE ROW LEVEL SECURITY;

-- Policy helper: current request's user_id
-- Works with Supabase Auth JWT (auth.uid()) or custom auth (session variable)
CREATE OR REPLACE FUNCTION current_user_id() RETURNS UUID AS $$
BEGIN
    -- Try Supabase Auth first, fall back to session variable
    BEGIN
        RETURN auth.uid();
    EXCEPTION WHEN undefined_function THEN
        RETURN current_setting('app.current_user_id', TRUE)::UUID;
    END;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- RLS policies: users can only see/modify their own rows
DO $$ 
DECLARE
    tbl TEXT;
BEGIN
    FOREACH tbl IN ARRAY ARRAY['user_prefs','jobs','emails','documents','notifications','discovered_jobs']
    LOOP
        EXECUTE format(
            'CREATE POLICY user_isolation ON %I
             USING (user_id = current_user_id())
             WITH CHECK (user_id = current_user_id())',
            tbl
        );
    END LOOP;
END $$;

-- ════════════════════════════════════════════════════════════════════════
-- INDEXES FOR COMMON QUERY PATTERNS
-- ════════════════════════════════════════════════════════════════════════

-- Dashboard: "show me all my jobs sorted by date, limited to 200"
-- Already covered by idx_jobs_created

-- "How many of my jobs are in each status?"
-- Already covered by idx_jobs_status

-- "Find my jobs at this company"
-- Already covered by idx_jobs_company_trgm (trigram for ILIKE search)
