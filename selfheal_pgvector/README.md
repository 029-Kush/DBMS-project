# Self-Healing Vector Databases — Phases 1–2

Database Systems Lab (BCSE302P) — Kush Gupta, Arnav Tiwari

## What the project currently delivers

A real pgvector instance loaded with real vectors, a verified recall@k
baseline, and a continuous observability layer that logs searches, tracks
nearest-neighbour distance distributions, checks model consistency and index
state, and stores an explainable health status over time. This is the measured
starting point that Phase 3 will degrade and Phases 4–5 will repair.

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
| `scripts/search.py` | Shared observable search: embeds, searches, measures, and logs each query |
| `scripts/query.py` | Command-line user search that writes to `query_log` |
| `scripts/eval_recall.py` | Computes recall, latency, distance drift, version skew, dead tuples, index state, status, and issues |
| `scripts/monitor.py` | Runs the health check once or continuously on an interval |
| `scripts/inject_fault.py` | Transactional fault injection with exact vector backup and restoration |
| `scripts/run_phase3_experiment.py` | Complete baseline → fault → restore → verify experiment |
| `sql/phase2_migration.sql` | Idempotent, additive Phase 2 migration that preserves Phase 1 data |
| `sql/phase3_migration.sql` | Fault-run audit trail and per-document embedding backups |
| `PHASE2.md` | Phase 2 design, thresholds, operation, and definition of done |
| `PHASE3.md` | Fault design, safety model, measured results, and remaining experiments |

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

## Phase 2 commands

```powershell
# From demo\
.\db.ps1 up
.\demo.ps1 phase2
.\demo.ps1 timeline

# Observable user search, from selfheal_pgvector\scripts\
$env:MAMBA_ROOT_PREFIX = 'C:\tools\mmroot'
& C:\tools\micromamba.exe run -n pgv python query.py `
  "a smartphone update that improves battery performance" --top-k 5

# Continuous monitor: check every five minutes
& C:\tools\micromamba.exe run -n pgv python monitor.py --interval 300
```

For this 480-row corpus PostgreSQL may prefer a sequential scan even though the
HNSW index exists, because scanning the tiny table is cheaper. Phase 2 records
this as `WARNING: INDEX_NOT_USED`, not as database damage. A larger Phase 3
competition dataset will make the HNSW performance distinction measurable.

## Phase 3: first real fault experiment

```powershell
# From demo\: runs baseline → injection → measurement → exact restore → verify
.\demo.ps1 phase3-drift
```

The implemented incompatible-embedding experiment changes 25% of the actual
vectors. In the verified run, recall@10 fell from 1.000 to 0.775 and mean
nearest-neighbour distance shifted by +21.8%; exact restoration returned both
metrics to baseline. See `PHASE3.md` for the complete result table.

Next Phase 3 experiments are controlled vector noise, HNSW index removal, and
intentional row churn. Phase 4 will consume the resulting fault-to-signal map
to select repairs automatically.
