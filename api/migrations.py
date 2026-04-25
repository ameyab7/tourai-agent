"""api/migrations.py — Run once on startup to ensure DB schema is up to date."""

import logging

import psycopg2

logger = logging.getLogger("tourai.api")

_CREATE_PROFILES = """
CREATE TABLE IF NOT EXISTS profiles (
  id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id             UUID NOT NULL UNIQUE REFERENCES auth.users(id) ON DELETE CASCADE,
  device_id           TEXT,
  interests           TEXT[]  NOT NULL DEFAULT '{}',
  travel_style        TEXT,
  pace                TEXT,
  drive_tolerance_hrs REAL,
  is_premium          BOOLEAN NOT NULL DEFAULT FALSE,
  created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Add is_premium to existing tables that predate this migration
ALTER TABLE IF EXISTS profiles ADD COLUMN IF NOT EXISTS is_premium BOOLEAN NOT NULL DEFAULT FALSE;
"""

_CREATE_POLICIES = [
    "ALTER TABLE profiles ENABLE ROW LEVEL SECURITY;",
    """
    DO $$ BEGIN
      IF NOT EXISTS (
        SELECT 1 FROM pg_policies WHERE tablename='profiles' AND policyname='users_own_profile_select'
      ) THEN
        CREATE POLICY users_own_profile_select ON profiles
          FOR SELECT USING (auth.uid() = user_id);
      END IF;
    END $$;
    """,
    """
    DO $$ BEGIN
      IF NOT EXISTS (
        SELECT 1 FROM pg_policies WHERE tablename='profiles' AND policyname='users_own_profile_insert'
      ) THEN
        CREATE POLICY users_own_profile_insert ON profiles
          FOR INSERT WITH CHECK (auth.uid() = user_id);
      END IF;
    END $$;
    """,
    """
    DO $$ BEGIN
      IF NOT EXISTS (
        SELECT 1 FROM pg_policies WHERE tablename='profiles' AND policyname='users_own_profile_update'
      ) THEN
        CREATE POLICY users_own_profile_update ON profiles
          FOR UPDATE USING (auth.uid() = user_id);
      END IF;
    END $$;
    """,
]


def run_migrations(database_url: str) -> None:
    if not database_url:
        logger.warning("DATABASE_URL not set — skipping migrations")
        return
    try:
        conn = psycopg2.connect(database_url)
        conn.autocommit = True
        cur = conn.cursor()
        cur.execute(_CREATE_PROFILES)
        for stmt in _CREATE_POLICIES:
            cur.execute(stmt)
        cur.close()
        conn.close()
        logger.info("migrations_ok")
    except Exception as exc:
        logger.error("migrations_failed", extra={"error": str(exc)})
