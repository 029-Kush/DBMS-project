# Phase 1 Demo — Run Sheet

Self-Healing Vector Databases · BCSE302P · Kush Gupta, Arnav Tiwari

Keep this open in a second window during the presentation. Every command
here is copy-paste ready. Test the whole sheet once tonight.

---

## 0. Setup — already installed on THIS machine

PostgreSQL 16 + pgvector 0.8.6 + a Python env were installed under `C:\tools\`
via micromamba (no admin, no Docker). Nothing to install again. Two helper
scripts in this folder drive everything — run them from PowerShell here:

```powershell
cd "C:\Users\Kush Gupta\Documents\codes\Projects\DBMS\demo"

.\db.ps1 up          # start Postgres on 127.0.0.1:5432, create db if missing
.\db.ps1 status      # confirm it's running + show row counts
```

`db.ps1 up` is safe to run repeatedly — if the server is already up it just says so.
The server does NOT survive a reboot; re-run `.\db.ps1 up` after restarting the PC.

Open a psql shell to type the section-2 queries into:

```powershell
& C:\tools\mmroot\envs\pgv\Library\bin\psql.exe -h 127.0.0.1 -p 5432 -U svuser -d selfheal
```

Run the whole Phase 1 pipeline (fits model, loads vectors, builds canary, baseline):

```powershell
.\demo.ps1 baseline
```

Do the fault-injection demo (inject → measure → restore, all automatic):

```powershell
.\demo.ps1 faults
.\demo.ps1 timeline    # just re-print the health_snapshots table
```

When done for the day: `.\db.ps1 down`

---

### If you ever need to rebuild this on another machine (Docker path)

```bash
cd selfheal_pgvector
docker run -d --name pgvec -e POSTGRES_PASSWORD=svpass -e POSTGRES_USER=svuser \
  -e POSTGRES_DB=selfheal -p 5432:5432 pgvector/pgvector:pg16
docker exec -i pgvec psql -U svuser -d selfheal < sql/schema.sql
pip install psycopg2-binary scikit-learn numpy pandas
cd scripts && python load_data.py && python build_canary.py && python eval_recall.py baseline
```

---

## 1. The visual (no database needed — safe fallback)

```bash
cd "C:\Users\Kush Gupta\Documents\codes\Projects\DBMS\demo"
python visualize_clusters.py
```

Opens/writes `embedding_clusters.png`. Talking points:

- These are the **real 64-dim vectors** that go into pgvector, projected to 2D.
- t-SNE shows **6 clean, separated clusters** — one per topic.
- **1-NN purity = 100%**: every document's nearest neighbour (cosine) is
  the same topic. That is why the canary set has unambiguous ground truth.
- Silhouette is only ~0.19 because all categories reuse the same sentence
  scaffolding ("… improves … by …"); the nearest-neighbour result is the
  honest measure of separation here.

---

## 2. Live demo sequence

### 2a. The schema is real
```
\d documents
```
> Point at: `embedding vector(64)`, `documents_embedding_hnsw` index,
> `embedding_model_version`, `created_at`, `last_reindexed_at`.

```
\dt
```
> `documents`, `canary_set`, `health_snapshots`, `query_log` — the last
> three exist purely for monitoring; that is the self-healing scaffolding.

### 2b. The data is loaded
```sql
SELECT category, count(*) FROM documents GROUP BY category ORDER BY category;
```
> Expect 6 rows, 80 each, 480 total.

```sql
SELECT id, body, left(embedding::text, 55) || ' ...' AS embedding_preview
FROM documents WHERE id = 1;
```
> A real stored vector, not a plan on a slide.

### 2c. ANN search actually uses the HNSW index
```sql
EXPLAIN ANALYZE
SELECT id, body
FROM documents
ORDER BY embedding <=> (SELECT embedding FROM documents WHERE id = 1)
LIMIT 10;
```
> Look for `Index Scan using documents_embedding_hnsw`. `<=>` is pgvector's
> cosine-distance operator. Sub-millisecond execution time.

### 2d. Nearest neighbours make sense
```sql
SELECT d.category, d.body
FROM documents d, (SELECT embedding FROM documents WHERE id = 1) q
ORDER BY d.embedding <=> q.embedding
LIMIT 5;
```
> All 5 should be the same category as document 1 (technology).

### 2e. The baseline health check, live
```powershell
.\demo.ps1 baseline      # runs load_data + build_canary + eval_recall
```
> Expected (last block of output):
> ```
> avg recall@10:   1.000
> avg latency:     ~0.5 ms
> p95 latency:     ~0.5 ms
> version skew:    0.0%
> dead tuple ratio: 0.0%
> ```

### 2f. It is tracked over time
```sql
SELECT note, recall_at_k, round(avg_latency_ms::numeric, 2) AS avg_ms,
       version_skew_pct, dead_tuple_pct, recorded_at
FROM health_snapshots ORDER BY id;
```

---

## 3. Preview Phases 3–5: break it, watch the monitor catch it

`.\demo.ps1 faults` runs this whole section automatically (inject → measure →
restore, both faults) and prints the timeline. The manual steps below are for
if you'd rather narrate each one by hand.

Observed on the 480-row corpus: **recall@10 stays 1.000 through both faults** —
version skew doesn't change vectors, and dropping the index makes search
*exact*. What actually moves is `version_skew_pct` (→33%) and the query plan
(→ `Seq Scan`). A real recall drop needs Phase 3's drift/staleness injection.
Say "the monitor caught the skew and the plan change", not "caught a recall regression".

### Fault A — half-finished model migration
```sql
UPDATE documents SET embedding_model_version = 'tfidf-svd-v0' WHERE id % 3 = 0;
```
```powershell
# from the demo\ folder:
& C:\tools\micromamba.exe run -n pgv python ..\selfheal_pgvector\scripts\eval_recall.py degraded_versionskew
```
> `version skew` jumps ~0% → ~33%. recall stays 1.0 (vectors unchanged),
> but the monitor now flags stale rows — exactly what Phase 4 would
> trigger a re-embed on.

**Restore:**
```sql
UPDATE documents SET embedding_model_version = 'tfidf-svd-v1';
```

### Fault B — someone drops the index
```sql
DROP INDEX documents_embedding_hnsw;
```
```powershell
& C:\tools\micromamba.exe run -n pgv python ..\selfheal_pgvector\scripts\eval_recall.py degraded_noindex
```
> Re-run 2c's EXPLAIN — it now shows `Seq Scan` + `Sort` instead of the index.
> Phase 5's repair = rebuild the index. (On this 480-row corpus the latency
> barely moves; the plan change is the visible signal.)

**Restore:**
```sql
CREATE INDEX documents_embedding_hnsw ON documents
  USING hnsw (embedding vector_cosine_ops)
  WITH (m = 16, ef_construction = 64);
```

### Close the loop
```sql
SELECT note, recall_at_k, round(avg_latency_ms::numeric,2) AS avg_ms, version_skew_pct
FROM health_snapshots ORDER BY id;
```
> Baseline → degraded rows → (after restore) recovered. That time series
> *is* the project: detect the dip, act, confirm recovery — automatically,
> in the later phases.

---

## 4. Teardown

```powershell
.\db.ps1 down          # stops the local Postgres server; data is kept in C:\tools\pgdata
```
Nothing to uninstall. Next session: `.\db.ps1 up`.

---

## 5. Likely questions

**Why TF-IDF + SVD, not a real embedding model?**
The build environment had no network route to model hubs. TF-IDF + Truncated
SVD is a legitimate fast baseline that still yields separable topical
clusters (see the figure). Nothing downstream — schema, loader, canary,
monitoring, repair — depends on how vectors are produced; swapping in
`sentence-transformers` is a one-function change in `embed_model.py`.

**No labelled relevance data — how is recall@k meaningful?**
Standard practice without human judgments: freeze the healthy system's own
top-k output as ground truth, then measure future regression against it.
Sanity check: 119/120 canary results are the same category as their query.

**What is specifically "DBMS" about this?**
Postgres extension (pgvector), a specialised index type (HNSW) with tunable
build params (`m`, `ef_construction`), custom distance operator (`<=>`),
query-plan inspection via `EXPLAIN ANALYZE`, catalog/stats views
(`pg_stat_user_tables` for dead-tuple ratio), array columns
(`BIGINT[]` result ids), `TIMESTAMPTZ` metadata, `TRUNCATE ... RESTART
IDENTITY`.

**What does "self-healing" concretely mean by the end?**
Phase 2: scheduled health checks (pg_cron) + distance-distribution
tracking. Phase 3: a fault injector (drift, staleness, index bloat).
Phase 4: detection — recall drop and/or distributional shift crosses a
threshold. Phase 5: automated repair — `REINDEX`, re-embed stale rows,
`VACUUM` — then re-run the canary to confirm recovery.

**Why 64 dimensions?**
Keeps the toy corpus fast. `EMBED_DIM` in `scripts/config.py` and
`VECTOR(64)` in `schema.sql` must stay in sync if changed.

**How big is the corpus and why synthetic?**
480 docs, 6 categories × 80, generated offline from recombined
subject/action/detail templates with a fixed seed — reproducible, no
dataset download, and deliberately separable so the canary ground truth
is unambiguous.
