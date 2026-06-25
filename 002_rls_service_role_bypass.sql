-- Migration: 002_rls_service_role_bypass
-- Allow the service role to bypass RLS for admin operations
-- (backfills, migrations, cascade deletes triggered by auth webhook)
--
-- This is a Supabase-specific concern. With custom auth (SQLite/Postgres without Supabase),
-- admin operations are performed directly in Python with no RLS.

-- Service role bypasses RLS automatically in Supabase.
-- This migration is a no-op placeholder that documents the policy decision.

-- For the users table (no RLS — only the service role touches this):
-- INSERT is done by the API server using the service role key.
-- SELECT is done by the API server for authentication.
-- Users do NOT have direct DB access.

COMMENT ON TABLE users IS
  'Authentication table. No RLS — API server uses service role for auth ops.
   Users cannot query this table directly via PostgREST.';

COMMENT ON TABLE user_prefs IS
  'Per-user key-value preferences. RLS: users see only their own rows.';

COMMENT ON TABLE jobs IS
  'Job applications. RLS: users see only their own rows.
   match_score: 0-100 from ATS keyword scorer.';

COMMENT ON TABLE documents IS
  'AI-generated resumes and cover letters. RLS: users see only their own rows.';
