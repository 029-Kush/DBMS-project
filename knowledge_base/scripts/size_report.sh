#!/usr/bin/env bash
# Real on-disk sizes per table/index, and bytes per chunk.
docker exec security-kb psql -U kb -d securitykb -c "
SELECT relname AS object, pg_size_pretty(pg_total_relation_size(c.oid)) AS total,
       pg_size_pretty(pg_relation_size(c.oid)) AS heap_only
FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
WHERE n.nspname='public' AND c.relkind='r' ORDER BY pg_total_relation_size(c.oid) DESC;" -c "
SELECT indexrelname AS index, pg_size_pretty(pg_relation_size(indexrelid)) AS size
FROM pg_stat_user_indexes ORDER BY pg_relation_size(indexrelid) DESC;" -c "
SELECT (SELECT count(*) FROM chunk_refs) AS chunks, count(*) AS distinct_texts, pg_size_pretty(pg_database_size(current_database())) AS db_total,
       round(avg(octet_length(body))) AS avg_body_bytes, round(avg(pg_column_size(body))) AS avg_body_stored_bytes,
       (SELECT round(avg(pg_column_size(b) + pg_column_size(q))) FROM body_vec) AS avg_vec_bytes FROM bodies;"
sudo -n compsize /run/media/meh/kbdata/pgdata 2>/dev/null | sed -n '2,4p'   # on-disk after zstd
