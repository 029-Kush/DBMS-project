# Self-Healing Vector Databases — Phase 1: Baseline

Database Systems Lab (BCSE302P) — Kush Gupta, Arnav Tiwari

## What this phase delivers

A real pgvector instance, loaded with real vectors, with a verified
recall@k baseline — the clean starting point that Phase 3's fault
injector will degrade and Phases 4–5 will have to detect and repair.

## Setup (reproducing from scratch)

```bash
# 1. Postgres + pgvector
apt-get install -y postgresql postgresql-contrib postgresql-server-dev-all build-essential git
git clone --branch v0.8.0 https://github.com/pgvector/pgvector.git
cd pgvector && make -j4 && make install

# 2. Start Postgres, create db/role, enable extension
service postgresql start
su postgres -c "psql -c \"CREATE DATABASE selfheal;\""
su postgres -c "psql -c \"CREATE USER svuser WITH PASSWORD 'svpass' SUPERUSER;\""
su postgres -c "psql -d selfheal -c \"CREATE EXTENSION vector;\""

# 3. Schema
su postgres -c "psql -d selfheal -f sql/schema.sql"
su postgres -c "psql -d selfheal -c \"GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO svuser;\""

# 4. Python deps (deliberately no torch — see note below)
pip install psycopg2-binary scikit-learn numpy pandas

# 5. Build corpus, fit embedding model, load, build canary set, run baseline
cd scripts
python3 generate_corpus.py
python3 load_data.py
python3 build_canary.py
python3 eval_recall.py baseline
```

## What's in here

| File | Purpose |
|---|---|
| `sql/schema.sql` | `documents` (vectors + version/timestamp metadata), `query_log`, `canary_set`, `health_snapshots` |
| `scripts/generate_corpus.py` | Builds a 480-doc synthetic corpus across 6 topical categories (tech, sports, finance, health, food, travel) |
| `scripts/embed_model.py` | The embedding model — TF-IDF + Truncated SVD, 64 dims |
| `scripts/load_data.py` | Fits the embedding model on the corpus and loads vectors into pgvector with an HNSW index |
| `scripts/build_canary.py` | Builds the fixed canary set: 12 hand-written probe queries, one expected top-10 per query, taken from the healthy baseline |
| `scripts/eval_recall.py` | Runs the canary set, computes recall@10, latency, version skew, dead-tuple ratio, and logs a row to `health_snapshots` |

## A note on the embedding model

This sandbox has no network route to Hugging Face or any model hub, so
a real sentence-transformer can't be downloaded here. TF-IDF + SVD is
used instead — it's a legitimate, fast baseline that still produces
real topical clusters (verified below), and it needs no external
download.

**To use a real transformer model** (recommended if you have internet
access when you run this): replace `TfidfSvdEmbedder` in
`embed_model.py` with a `sentence-transformers` call. Nothing else in
the project — schema, loader, canary set, monitoring, or the later
fault-injection/repair phases — depends on how the vectors were
produced, as long as `embed()` returns fixed-length vectors.

## Baseline results

```
avg recall@10:   1.000
avg latency:     0.59 ms
p95 latency:     0.53 ms
version skew:    0.0%
dead tuple ratio: 0.0%
```

Sanity check: 119/120 canary expected-result documents belong to the
same category as their query (the one exception is a food query
returning one health-related document — a reasonable overlap, not a
labeling bug).

## Next (Phase 2)

Turn `eval_recall.py` into a scheduled job (pg_cron or a loop) and add
distance-distribution tracking so drift shows up as a distributional
shift, not just a recall drop.
