# VectorSpace explorer

A 3-D view of the vector database with an auditable search trace. Type a query in the
Spotlight-style bar; the page replays the search step by step, then shows the final path
and the alternate paths. Design from the Stitch export (`DESIGN.md` tokens).

```bash
pip install fastapi uvicorn umap-learn            # plus the script deps
cd selfheal_pgvector/explorer
SELFHEAL_DSN="..." SELFHEAL_EMBEDDER=minilm uvicorn app:app --port 8765
# open http://localhost:8765/   (also: /?q=your+query&final=1)
```
Startup builds the index and the 3-D layout from the database (about 30 s for 5,000 documents).

## What is real and what is approximate
- **The trace is the search that produced the results.** pgvector does not expose its HNSW walk,
  so results come from an in-process HNSW (`hnsw_trace.py`, m=16, ef_construction=64, ef_search=40)
  built from the same vectors. Every answer is checked against a full scan and against pgvector's
  own index; the overlap is shown ("Verified 10/10" or "Differs").
- **Scores are exact** (cosine similarity in the original dimensions).
- **Positions are approximate.** 3-D coordinates are a UMAP projection that keeps only about 37% of
  each point's true 10 nearest neighbours (PCA kept 4%), so a correct path can look indirect.
  The measured score is shown in the inspector.
- A "hop" is: score a document's linked neighbours against the query and move to the closest.
- The index is rebuilt automatically when the stored vectors change (checked every 5 s), e.g. after
  the healer rewrites rows; results are flagged until then.

Measured on 5,000 AG News documents with all-MiniLM-L6-v2: in-process index recall@10 0.990 against exact
(600 held-out queries), 0.988 overlap with pgvector's HNSW (200 queries); search about 1 ms.

## Add information and healing (left column)
The **+** icon under the search icon opens an "Add information" column on the same map:
- **Add your own text** (paste documents separated by blank lines, pick a category) or **sample articles** (needs
  `SELFHEAL_INSERTS`, a CSV with `category,body`). Documents are embedded with the current model, inserted into
  Postgres, linked into the search graph and placed on the map; they are searchable immediately.
- The monitor then checks health (gauges for search quality, rows that don't match their text, index accuracy).
- **Healing tab:** run the real healer (`heal.py`) or heal automatically after each add; stages and activity log.
- **Demo failure button** ("+ 300 with the wrong embedder") only exists when the server starts with
  `EXPLORER_ENABLE_FAULTS=1`. Never enable it on data you care about; use a scratch copy of the database.
Endpoints: `/api/info`, `/api/health`, `/api/ingest`, `/api/heal`. Ingest and heal run one at a time; searches keep working.
Tested: custom text added, then found as result #1 (verified 10/10 against exact and pgvector); faulty batch detected;
healer repaired 300 rows. Not tested: very large pastes, concurrent users, Windows.
