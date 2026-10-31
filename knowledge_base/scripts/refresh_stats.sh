#!/usr/bin/env bash
# Rebuild the term document-frequency table used by search.py's OR-keyword run. Run after every ingest (takes ~5 s).
# ndoc counts distinct texts (bodies), which is what the retrievers rank.
docker exec security-kb psql -U kb -d securitykb -c "
BEGIN; DROP TABLE IF EXISTS term_df;
CREATE TABLE term_df AS SELECT word, ndoc FROM ts_stat('SELECT tsv FROM bodies');
CREATE UNIQUE INDEX term_df_word ON term_df(word); COMMIT; ANALYZE term_df; ANALYZE bodies; ANALYZE chunk_refs; ANALYZE body_vec;"
