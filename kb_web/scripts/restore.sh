#!/usr/bin/env bash
# Restore a pg_dump custom-format backup into the database at DATABASE_URL (local or Supabase).
#   DATABASE_URL='postgresql://...' kb_web/scripts/restore.sh /path/to/kbcloud-YYYY-MM-DD.dump
# Uses the pg_restore inside the pgvector image, so no Postgres client is needed on the host.
# --clean --if-exists drops and recreates each object: the target's existing copy of these tables is replaced.
set -euo pipefail
dump=${1:?usage: restore.sh <file.dump>}
: "${DATABASE_URL:?set DATABASE_URL}"
docker run --rm -i -e DATABASE_URL pgvector/pgvector:pg16 \
    sh -c 'pg_restore --no-owner --no-privileges --clean --if-exists --exit-on-error -d "$DATABASE_URL"' < "$dump"
docker run --rm -e DATABASE_URL pgvector/pgvector:pg16 \
    sh -c 'psql "$DATABASE_URL" -At -c "SELECT source, count(*) FROM chunk_refs GROUP BY 1 ORDER BY 1" -c "SELECT count(*) || '"' users'"' FROM users"'
