# Self-healing simulator

A sandbox where you ingest new documents, break some of them, and watch the real healer react.
Separate from the search explorer (`../explorer`, port 8765); this runs on port 8766.

```bash
pip install fastapi uvicorn umap-learn            # plus the script deps
cd selfheal_pgvector/sim
SIM_BASE_DB=minilm5k SELFHEAL_INSERTS=/path/to/inserts.csv SELFHEAL_EMBEDDER=minilm \
SELFHEAL_DSN="dbname=minilm5k user=svuser password=svpass host=127.0.0.1 port=5433" \
uvicorn app:app --port 8766
# open http://localhost:8766/
```
On startup it copies `SIM_BASE_DB` into a scratch database (`selfheal_sim`); your original data is never touched.
`SELFHEAL_INSERTS` is a CSV with `category,body` columns of documents to ingest (the samples used here are
AG News articles that are not in the base corpus).

## What you can do
- **+ documents:** embeds them, inserts them in Postgres, and shows the neighbour graph reorganising
  (new links, neighbour lists updated, old links dropped). The monitor then checks health and, correctly, does nothing.
- **+ 300 with the wrong embedder:** the batch is embedded by a different model but labelled as the current one
  (a silent failure). The monitor flags vector mismatch from sampling rows against their text.
- **Run healer** (or turn on Auto-heal): the real healer diagnoses, rewrites only the bad rows from their text, verifies.

## What is real and what is illustrative
- Real: the sandbox database, embeddings, inserts, health checks and the healer (`scripts/heal.py`, unchanged).
- Illustrative: the neighbour graph on screen is an in-process HNSW with pgvector's parameters (pgvector's own
  graph cannot be observed), rebuilt after a heal; positions are a UMAP projection of the real vectors.
- A 3-D map cannot show cross-model corruption: repaired rows move only a few units. The simulator says so.
- Detection by sampling is probabilistic (reliable from about 2% of rows); a healer pass can miss it, and the UI says so.
