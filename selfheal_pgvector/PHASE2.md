# Phase 2 — Continuous, Explainable Observability

## Goal

Phase 1 answered: "Does vector search work when the database is healthy?"
Phase 2 answers: "Can we continuously observe search behaviour and explain
which part of the system looks unhealthy?"

Phase 2 does not repair anything. Its output is reliable evidence that Phase 3
fault injection and Phase 4 repair can consume.

## What is measured

| Signal | Meaning | Phase 2 rule |
|---|---|---|
| Recall@10 | Stability of known canary results | Critical below 0.95 |
| Mean nearest-neighbour distance | Typical closeness of the best match | Tracked against a fixed baseline |
| Distance shift | Percentage change from baseline closeness | Degraded above +20% |
| Model-version skew | Rows produced by a non-current embedder | Degraded above 5% |
| Dead-tuple ratio | PostgreSQL row churn/bloat proxy | Degraded above 20% |
| HNSW existence | Whether the expected ANN index is present | Critical when missing |
| HNSW plan usage | Whether PostgreSQL selects it for a canary query | Warning when not selected |

`INDEX_NOT_USED` is only a warning because PostgreSQL can legitimately choose a
sequential scan for the 480-row demo corpus. It should select HNSW naturally
when the competition dataset is made much larger.

## Data flow

1. `monitor.py` starts a health-check run.
2. `eval_recall.py` loads the fixed 12-query canary set.
3. Every query passes through `search.py`.
4. `search.py` embeds the text, searches pgvector, and writes the query,
   result IDs, result distances, latency, and source to `query_log`.
5. The evaluator calculates recall, latency, and nearest-neighbour distance
   distributions.
6. PostgreSQL catalog and query-plan checks report index existence and usage.
7. Independent threshold rules produce a status and an explicit issue list.
8. One row is committed to `health_snapshots`.

All query logs and the health snapshot use the same transaction: a failed run
does not leave a misleading half-written observation behind.

The baseline TF-IDF model cannot embed a query made entirely from unseen words.
`search.py` rejects that zero-vector case explicitly instead of returning
undefined cosine distances. A sentence-transformer in a later competition build
will generalise much better to unfamiliar language.

## Running Phase 2

From `demo` in PowerShell:

```powershell
.\db.ps1 up
.\demo.ps1 phase2
.\demo.ps1 timeline
```

For continuous checks, from `selfheal_pgvector\scripts`:

```powershell
$env:MAMBA_ROOT_PREFIX = 'C:\tools\mmroot'
& C:\tools\micromamba.exe run -n pgv python monitor.py --interval 300
```

The interval is seconds. Omit `--interval` for exactly one check.

## Definition of done

- Each canary search creates one complete `query_log` row.
- Every health snapshot contains non-null distance and index measurements.
- Healthy, warning, degraded, and critical conditions are explainable through
  the `issues` array.
- The migration can be run repeatedly without deleting existing data.
- The monitor can run once or continuously.
- Pure diagnosis rules have automated tests independent of PostgreSQL.

## What Phase 2 deliberately does not do

- It does not inject faults.
- It does not automatically repair faults.
- It does not treat a tiny-table sequential scan as a broken index.
- It does not replace the canary ground truth when results become worse.

Those boundaries keep Phase 3 experiments and Phase 4 repairs measurable and
prevent the monitoring system from hiding failures by rewriting its baseline.
