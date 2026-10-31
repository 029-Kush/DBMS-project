# Knowledge base (database + search engine)

PostgreSQL 16 + pgvector database of technical documentation and the hybrid search engine that ranks it.
The web application in `kb_web/` imports `scripts/search.py` from here.

## Schema (`sql/`, apply in order)

| File | Contents |
|---|---|
| `schema.sql` | `entities`, `bodies` (each distinct text once, content-addressed by md5), `chunk_refs` (entity, section, source → body), `body_vec` (1-bit sign code + 4-bit code per text, no float vectors), `vec_quant`, indexes, view `chunks` |
| `002_links.sql` | external ids, typed facts, `entity_links` (entity ↔ entity) |
| `003_vec_quant.sql` | frozen 4-bit scale for the MiniLM codes |
| `004_lookup_indexes.sql` | name / alias lookup indexes |
| `005_term_df.sql` | word document frequencies used by the keyword lane |

## Search (`scripts/search.py`)

Each query runs up to three lanes — exact flag/ID match on `bodies.flags`, PostgreSQL full-text on `bodies.tsv`, and a
vector lane (Hamming top-800 on the 1-bit codes, re-sorted with the 4-bit codes) — and fuses them with weighted
reciprocal rank fusion (k = 60). `python scripts/search.py "find files changed in the last day"` prints the top passages.

## Other scripts

`ingest_tools.py`, `ingest_bulk.py`, `ingest_concepts.py` load man / help / tldr pages; `embed_codes.py` (re)computes
vector codes; `bench_tldr.py`, `eval_*.py` are the retrieval benchmarks; `backup.sh`, `refresh_stats.sh`,
`size_report.sh` are maintenance. Configuration comes from `.env` or the environment (`DATABASE_URL`, `KB_EMBEDDER`).

`docker-compose.yml` runs the local database (data directory on an external drive).
