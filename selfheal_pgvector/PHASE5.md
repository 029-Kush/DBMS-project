# Phase 5 — Hardening, Real Embedders, Scale, and Evidence

Phase 4 built the repair loop. Phase 5 asks: does it survive crashes and
concurrency, does it work on real models at non-toy scale, does it raise false
alarms under a normal workload, and is "targeted repair" actually better than
the obvious alternatives? Every number below was measured by the scripts in
`scripts/` (Linux, `pgvector/pgvector:pg16` in Docker, 16-core shared box).
Raw outputs are in `results/`.

## What changed

| Area | Change |
|---|---|
| New signals | **ANN-vs-exact recall** (index health, distance-aware so ties/duplicates don't fool it, threshold relative to the first healthy snapshot); **sentinel re-embedding** (random stored rows re-embedded and compared → `VECTOR_MISMATCH`, catches silent corruption); invalid-index detection via `pg_index.indisvalid` |
| Canaries | Frozen canaries are evaluated only over documents that existed when they were built (`canary_set.corpus_max_id`), and ground truth is **exact** search, so legitimate inserts and index rebuilds no longer look like drift |
| Diagnosis | Staged: unexplained recall/distance loss triggers an exhaustive vector scan; only if that finds nothing is it escalated (probably outdated canaries) |
| Repair | Batched; embedding runs with **no locks held**, then a short lock + optimistic check (rows a user touched mid-repair are skipped and retried); index rebuilt `CONCURRENTLY` then swapped by rename |
| Fault tolerance | Advisory lock, orphan recovery, persisted failure memory with cooldown, transient-failure handling, DB-outage survival (see below) |
| Embedders | `SELFHEAL_EMBEDDER=tfidf|bge|minilm`; two real 384-d models (fastembed/ONNX) so cross-model contamination is real, not simulated; schema dimension applied by `db_setup.py` |
| Skew threshold | Version skew is an exact count, so the default threshold is now 0 (was 5%). A 5% threshold left labelled contamination below 5% undetected (measured) |

## 1. Fault tolerance (17/17 pass) — `run_fault_tolerance.py`

Real healer processes are killed, restarted and raced; afterwards: no stuck
RUNNING event, document set unchanged, **no torn rows** (every row is exactly
its pre- or post-fault state), no leftover helper index, valid HNSW, and a
follow-up cycle reaches rows consistent with their text.

crash after opening event · crash mid re-embed (1 of 3 batches) · crash after
repair before verification · crash mid index build · recovery restores the *exact*
pre-heal state (×2) · genuine `SIGKILL` · two healers at once (second returns
`SKIPPED`, does not recover the live one's event) · Postgres container restart
mid-repair · `pg_terminate_backend` mid-repair · database down when healer
starts · monitor loop survives an outage and resumes · writer holds a row lock
(transient failure, immediate retry allowed) · user edit while healer embeds
(edit survives, write not blocked, row finished next cycle) · rollback never
clobbers a user's later update · failure memory survives restart and expires ·
idempotent repeated cycles.

Bugs this suite found in my own code (all fixed): crash recovery recorded the
orphan as a failed plan, so the failure memory then **blocked healing for an
hour after any crash**; row locks were held during embedding (an 11 s writer
stall measured in the benchmark); skipped rows were never retried; a 1-row
residue below the alarm threshold would never be repaired.

Recovery policy: an orphaned event is **rolled back, then re-healed** rather
than rolled forward. Simpler to prove correct; costs redoing finished work.

## 2. Scenarios (heal outcome on live databases) — `run_phase4_experiment.py`

Ground truth: after each scenario every row's vector is re-embedded from its
text and compared (`inconsistent=0` means fully correct), independent of any
backup.

| Scenario | 480 docs TF-IDF | 20k docs TF-IDF | 5k docs bge (real model) |
|---|---|---|---|
| healthy control | no action | no action | no action |
| model drift 25% (labelled) | healed | healed | healed |
| stale labels | healed | healed | healed |
| **cross-model mix 25%, labelled** | n/a | n/a | healed |
| **cross-model mix 25%, SILENT** | n/a | n/a | healed |
| silent permutation / noise 25% | healed | healed | healed |
| content edited, embedding stale | vectors fixed → canaries stale → escalated | same | same |
| HNSW dropped / INVALID / degraded (m=4) | healed | healed | healed |
| table bloat | healed | healed | healed |
| drift + dropped index + bloat | healed (3 repairs) | healed | healed |
| unexplained recall loss (canary rot) | escalated | escalated | escalated |

All "healed"
rows end with 0 rows inconsistent with their text. The two escalations are by
design: if vectors match their text, the index is fine and recall is still low,
the frozen canaries are outdated and a human must recalibrate.

## 3. False alarms under a normal workload — `benchmark.py fp`

5k-doc bge database, 20 health cycles: 5 idle, then 15 with 100 inserts + 20
consistent edits per cycle (docs 5,000 → 6,500). Result: **0 actions/alarms**.
Without the corpus-horizon fix the frozen canary recall would have dropped to
0.76 and alarmed in **12 of 20 cycles**. (Limit: the horizon means drift that
affects *only* post-horizon documents is invisible to the canaries; the
sentinel covers those.)

## 4. Targeted vs blanket repair — `benchmark.py compare`

5k docs, bge, concurrent reader + writer probes. "Blanket" = what an operator
without a diagnosis does: re-embed everything, DROP/CREATE index, VACUUM FULL.
"Manual-expert" = an operator who already knows the fault, using standard
blocking DDL.

| Fault | Strategy | Time | Docs embedded | Rows updated | WAL | Reader max | Writer max |
|---|---|---:|---:|---:|---:|---:|---:|
| cross-model 10% labelled | **healer** | 17 s | 804 | 500 | 4.9 MB | 8 ms | 20 ms |
| | blanket | 112 s | 5,076 | 5,000 | 52.7 MB | 398 ms | 418 ms |
| cross-model 50% labelled | **healer** | 63 s | 2,804 | 2,500 | 20.4 MB | 12 ms | 12 ms |
| | blanket | 109 s | 5,076 | 5,000 | 43.3 MB | 376 ms | 369 ms |
| silent noise 10% | **healer** | 115 s | 5,304 | 500 | 6.1 MB | 9 ms | 31 ms |
| | blanket | 111 s | 5,076 | 5,000 | 39.1 MB | 525 ms | 519 ms |
| index degraded | **healer** | 5.3 s | 304 | 0 | 9.5 MB | 8 ms | 10 ms |
| | blanket | 110 s | 5,076 | 5,000 | 67.2 MB | 528 ms | 508 ms |
| | manual-expert | **0.4 s** | 76 | 0 | 9.3 MB | 8 ms | **327 ms** |
| table bloat | **healer** | 5.0 s | 304 | 0 | 0.8 MB | 10 ms | 12 ms |
| | blanket | 110 s | 5,076 | 5,000 | 39.3 MB | 432 ms | 413 ms |
| | manual-expert | **0.4 s** | 76 | 0 | 18.8 MB | 339 ms | 348 ms |

Honest reading:
- Versus blanket the healer rewrites 10×/2× fewer rows and writes far less WAL,
  and never blocks readers/writers (blanket stalls both for ~0.4–0.5 s).
- **Silent corruption has no time advantage**: localising it needs one
  embedding per row, so compute is O(corpus). The win is writes/WAL/availability.
- A skilled operator who already knows the fault is **faster** than the healer
  for index and bloat fixes (0.4 s vs 5 s) but blocks writers for ~330 ms. The
  healer's value is *detection and automation without blocking users*, not raw
  speed. A manual operator's diagnosis time is not measured here (no honest way to).
- "Docs embedded" for the healer includes health-check overhead (~76 per check).

## 5. Detection limits and identifiability — `benchmark.py matrix`, `analyze_matrix.py`

5 faults × severities {2,5,10,25,50}% × 3 seeds on 5k docs (75 cells).

- Silent corruption is detected from ~2% of rows (sentinel samples 64 rows: ≈70%
  per check at 2%, ≈96% at 5%; repeated cycles accumulate).
- With the old 5% skew threshold, **labelled** contamination was missed at 2%
  (3/3) and 5% (1/3); with threshold 0 it is detected 100% at both.
- **Identifiability**: all silent faults (cross-model, noise, permutation) leave
  the same signature (`VECTOR_MISMATCH` [+ `LOW_RECALL`]) — the signals cannot
  tell them apart. They collapse into repair-equivalence classes, which is
  sufficient: {stale label → re-embed stale}, {vector ≠ text → scan+rewrite},
  {index → rebuild}, {bloat → vacuum}. This is why diagnosis targets the
  *repair* class, and why unexplained symptoms trigger investigation rather than a guess.
- Large silent noise (50%, σ=0.15) additionally degrades the index
  (`INDEX_DEGRADED`).

## Limits (read these)

- Corpora: 480 synthetic, 20k and 5k real news text (AG News). Not production scale.
- bge-small and MiniLM are small models; larger models make re-embedding (the
  dominant cost) much slower, which strengthens the targeted-vs-blanket gap for
  labelled faults and does nothing for silent ones.
- Sentinel detection is probabilistic and per-check; very low corruption rates
  can persist for several cycles.
- Outdated canaries (legitimate content change) are escalated, not auto-fixed.
- Failure memory cooldown is an hour by default; a human must clear persistent escalations.
- The 480-row table legitimately shows `INDEX_NOT_USED` (planner prefers seq scan).
- `demo.ps1` actions are untested on Windows.
- No comparison against Drift-Adapter-style learned mappings (not built).

## Run

```bash
python db_setup.py ../sql/schema.sql            # dimension follows SELFHEAL_EMBEDDER
python load_data.py && python build_canary.py && python eval_recall.py baseline
python monitor.py --heal --interval 300
python run_phase4_experiment.py                 # scenarios
python run_fault_tolerance.py                   # needs docker + container name in SELFHEAL_CONTAINER
python benchmark.py fp|compare|matrix --out x.json ; python analyze_matrix.py matrix.json
```
