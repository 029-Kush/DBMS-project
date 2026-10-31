#!/usr/bin/env bash
# Build a trimmed copy of the knowledge base for a free cloud Postgres, then back it up with pg_dump.
#   KEEP="tldr help man" kb_web/scripts/export_cloud_subset.sh
# Runs inside the local container (docker exec): creates database kbcloud, copies only the passages from the sources in
# KEEP (plus their entities, texts, MiniLM vector codes and links), adds the app tables, and writes
# $KB_HDD/backups/kbcloud-<date>.dump (custom format). Restore it with kb_web/scripts/restore.sh.
set -euo pipefail
cd "$(dirname "$0")/../.."
set -a; . "${KB_ENV:-$HOME/security-kb/.env}"; set +a
C=${KB_CONTAINER:-security-kb}
KEEP=${KEEP:-"tldr help man"}
SRC=$POSTGRES_DB
psql() { docker exec -i "$C" psql -v ON_ERROR_STOP=1 -q -U "$POSTGRES_USER" "$@"; }
keep_sql="ARRAY['$(echo $KEEP | sed "s/ /','/g")']"

psql -d "$SRC" -c "DROP DATABASE IF EXISTS kbcloud" -c "CREATE DATABASE kbcloud"
for f in schema.sql 002_links.sql 003_vec_quant.sql 004_lookup_indexes.sql 005_term_df.sql; do psql -d kbcloud < knowledge_base/sql/$f; done
# the body trigram lane is off at this corpus size (search.py TRGM_BODY_OFF), so its index is dead weight in the copy
psql -d kbcloud -c "DROP INDEX IF EXISTS bodies_trgm_idx"

copy() {   # copy("table [(columns)]", select) : stream rows between the two databases without a temp file
    docker exec "$C" psql -v ON_ERROR_STOP=1 -q -U "$POSTGRES_USER" -d "$SRC" -c "COPY ($2) TO STDOUT (FORMAT binary)" |
        psql -d kbcloud -c "COPY $1 FROM STDIN (FORMAT binary)"
}
refs="SELECT * FROM chunk_refs WHERE source = ANY($keep_sql)"
copy entities   "SELECT * FROM entities WHERE id IN (SELECT entity_id FROM ($refs) r)"
copy "bodies (id, hash, body, flags, trust)" "SELECT id, hash, body, flags, trust FROM bodies WHERE id IN (SELECT body_id FROM ($refs) r)"
copy chunk_refs "$refs"
copy body_vec   "SELECT * FROM body_vec WHERE id IN (SELECT body_id FROM ($refs) r)"
copy entity_links "SELECT l.* FROM entity_links l WHERE l.src IN (SELECT entity_id FROM ($refs) r) AND l.dst IN (SELECT entity_id FROM ($refs) r)"
# bodies and chunk_refs share chunks_id_seq
psql -d kbcloud -c "SELECT setval('chunks_id_seq', greatest((SELECT max(id) FROM chunk_refs), (SELECT max(id) FROM bodies)))" \
                -c "SELECT setval('entities_id_seq', (SELECT max(id) FROM entities))" >/dev/null
# word document frequencies over this copy's texts (what scripts/refresh_stats.sh builds for the full database)
psql -d kbcloud -c "INSERT INTO term_df SELECT word, ndoc FROM ts_stat('SELECT tsv FROM bodies')"
psql -d kbcloud < kb_web/sql/001_web.sql
psql -d kbcloud -c "VACUUM ANALYZE"
psql -d kbcloud -At -c "SELECT 'size: ' || pg_size_pretty(pg_database_size('kbcloud'))" \
    -c "SELECT source || ': ' || count(*) FROM chunk_refs GROUP BY source ORDER BY 1"

mkdir -p "$KB_HDD/backups"
out="$KB_HDD/backups/kbcloud-$(date +%F).dump"
docker exec "$C" pg_dump -U "$POSTGRES_USER" -d kbcloud -Fc --no-owner --no-privileges > "$out"
ls -lh "$out"
