#!/usr/bin/env bash
# Build kb_web/seed/docs.sql.gz: a small corpus for the hosted copy -- command help, tldr pages and man sections 1 and 8
# (user and admin commands) without boilerplate sections -- with entities, texts and vector codes, as a pg_dump data file that
# `flask bootstrap` streams into an empty hosted database. Needs the local database running.
set -euo pipefail
cd "$(dirname "$0")/../.."
WHERE="source IN ('help', 'tldr') OR (source = 'man'
    AND entity_id IN (SELECT id FROM entities WHERE type = 'manpage' AND ext_id IN ('1', '8'))
    AND section !~ '(^|: )(authors?|copyright|reporting bugs|colophon|see also|history|revision history|license|availability|bugs)\$')" \
    kb_web/scripts/export_cloud_subset.sh
set -a; . "${KB_ENV:-$HOME/security-kb/.env}"; set +a
mkdir -p kb_web/seed
docker exec "${KB_CONTAINER:-security-kb}" pg_dump -U "$POSTGRES_USER" -d kbcloud --data-only --no-owner --no-privileges -t entities -t bodies -t chunk_refs -t body_vec -t entity_links |
    gzip -9 > kb_web/seed/docs.sql.gz   # COPY format: flask bootstrap streams it (kb_web.load_seed)
ls -lh kb_web/seed/docs.sql.gz
