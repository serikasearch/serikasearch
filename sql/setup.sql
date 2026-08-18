-- One-time Postgres setup for SerikaSearch.
--
-- Run once against the database, as the postgres superuser:
--     psql "$DATABASE_URL" -f sql/setup.sql
-- (point DATABASE_URL at Postgres DIRECTLY here, not at PgBouncer).
--
-- Some of these need a Postgres RESTART to take effect (noted below). On
-- Coolify, restart the Postgres service after running this.

-- 1. Performance GUCs as DATABASE defaults.
--    The app no longer sets these per session (that breaks under a
--    transaction-pooling PgBouncer), so they must live on the database.
--    enable_seqscan=off pushes the planner onto the GIN/btree indexes for
--    search; jit=off avoids per-query JIT warm-up on short queries.
--    Applies to every NEW connection — no restart needed.
ALTER DATABASE postgres SET enable_seqscan = off;
ALTER DATABASE postgres SET jit = off;

-- 2. Raise the connection ceiling so the crawler fleet + web app + PgBouncer
--    have plenty of headroom. Each server connection costs ~5-10 MB RAM, so
--    "unlimited" is not a real setting — this is a high, safe cap. With
--    PgBouncer (transaction pooling) in front, a few hundred SERVER connections
--    already back thousands of CLIENT connections, so you rarely need more.
--    REQUIRES A RESTART to take effect.
ALTER SYSTEM SET max_connections = 500;

-- After running this file, restart Postgres, then verify:
--     SHOW max_connections;   -- expect 500
--     SHOW enable_seqscan;    -- expect off (on a fresh connection)
