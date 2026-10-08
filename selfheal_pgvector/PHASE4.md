# Phase 4 — Diagnosis, Targeted Repair, Verification

## Goal

Close the loop: **check → find problem → choose fix → fix → check again**, with
no human reading the health table. Phase 4 consumes the Phase 2 issue codes and
repairs only what it can explain.

## Components

| File | Role |
|---|---|
| `scripts/diagnose.py` | Pure rules: issue codes → ordered repair plan, or escalation. No DB access. |
| `scripts/repair.py` | Executors: `reembed_stale`, `rebuild_index`, `vacuum_documents`, `rollback_reembed`. |
| `scripts/heal.py` | `heal_once()`: check → diagnose → repair → re-check → keep or roll back. |
| `scripts/monitor.py --heal` | Opt-in loop. Without `--heal` the monitor still only observes. |
| `sql/phase4_migration.sql` | `maintenance_events` (audit), `embedding_backups` (healer's own rollback data), adds `HEALED` to `fault_runs.status`. |
| `scripts/run_phase4_experiment.py` | Six fault scenarios against a live database. |
| `scripts/test_phase4.py` | Rules and control flow, no database needed. |

## Diagnosis rules

| Issues seen | Plan |
|---|---|
| `VERSION_SKEW` (+ `LOW_RECALL`, `DISTANCE_DRIFT`) | re-embed stale rows from their text, then vacuum |
| `INDEX_MISSING` | create the HNSW index (REINDEX if present) |
| `TABLE_BLOAT` | `VACUUM ANALYZE` |
| `INDEX_NOT_USED` only | nothing (small-table seq scan is legitimate) |
| `LOW_RECALL` / `DISTANCE_DRIFT` **without** `VERSION_SKEW` | **escalate**, do not touch vectors |

Order is always re-embed → index → vacuum, because re-embedding creates dead tuples.

## Safety model

- The healer repairs from `documents.body` + the current model only. It never
  reads `fault_embedding_backups` or calls the fault lab's restore.
- Before re-embedding it copies the old vectors to `embedding_backups`.
- A repair is accepted only if a fresh health check no longer shows the issue
  codes the plan was responsible for, and the status is not CRITICAL (unless
  part of the problem was escalated). Otherwise the re-embed is rolled back
  and the event is `ROLLED_BACK`.
- An identical plan that failed once is escalated next cycle, not retried.
- Outcomes: `SUCCEEDED`, `PARTIAL` (our part verified, rest escalated),
  `ROLLED_BACK`, `FAILED`, `ESCALATED`, `NO_ACTION`.
- One RUNNING maintenance event at a time (partial unique index).
- A fault-lab run still ACTIVE after a fully verified repair is marked `HEALED`.
  This is a heuristic link (any ACTIVE run); it is not set for `PARTIAL` repairs.

## Run

```bash
psql -d selfheal -f sql/phase4_migration.sql
cd scripts
python monitor.py --heal --interval 300     # continuous self-healing
python run_phase4_experiment.py             # six-scenario experiment
python -m unittest test_phase4
```

## Measured results (Linux, `pgvector/pgvector:pg16` container, 480 rows, run by me)

| Scenario | Healer outcome | Status | recall@10 | Actions |
|---|---|---|---|---|
| healthy (control) | NO_ACTION | WARNING→WARNING | 1.000 | none |
| model drift 25% | SUCCEEDED | CRITICAL→WARNING | 0.775→1.000 | re-embed, vacuum |
| stale labels (id%3) | SUCCEEDED | DEGRADED→WARNING | 1.000 | re-embed, vacuum |
| HNSW index dropped | SUCCEEDED | CRITICAL→WARNING | 1.000 | rebuild index |
| table bloat | SUCCEEDED | DEGRADED→WARNING | 1.000 | vacuum |
| drift + index dropped | SUCCEEDED | CRITICAL→WARNING | 0.775→1.000 | re-embed, index, vacuum |
| silent corruption (labels say current) | PARTIAL | CRITICAL→CRITICAL | 0.775→0.775 | vacuum only; recall loss escalated |

After drift repairs, healed vectors matched the fault lab's originals exactly
(max L2 distance 0.0 — the TF-IDF model is deterministic, so re-embedding the
same text reproduces the same vectors). The residual WARNING is the expected
`INDEX_NOT_USED` on a 480-row table.

## Limits

- Healing by re-embedding works here because the embedder is deterministic and
  the source text is stored. A real transformer swap changes nothing structural,
  but re-embedding cost would matter at scale.
- Silent corruption (bad vectors, correct labels) is detected but not repaired:
  with no stale-model signal the cause is unknowable from the health metrics.
- Only the fault types above are handled; unknown failures are escalated.
- The escalation memory (`HealState`) is per process; restarting the monitor forgets it.
- `demo.ps1 phase4-heal` is untested on Windows; everything above ran on Linux.
