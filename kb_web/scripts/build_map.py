"""Precompute the explorer's 3-D map for the hosted copy (UMAP and the HNSW graph are too heavy to build on a free
512 MB instance). Reads the kbcloud database made by export_cloud_subset.sh and writes kb_web/seed/map.npz
(text ids, 3-D positions, source, title) and kb_web/seed/map_hnsw.pkl (the search graph over those texts).

Run locally with the explorer's environment (umap-learn installed):
  KB_ENV=~/security-kb/.env ~/dbms-project-ui/venv/bin/python kb_web/scripts/build_map.py [max points, default 15000]
"""
import os
import sys
import time
from pathlib import Path

import numpy as np
import psycopg
import umap

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "knowledge_base" / "scripts"))
sys.path.insert(0, str(ROOT / "selfheal_pgvector" / "explorer"))
os.environ.setdefault("KB_EMBEDDER", "minilm")
from common import ENV, MODEL_VERSION, decode   # noqa: E402
from hnsw_trace import TraceHNSW, save          # noqa: E402

MAX_POINTS = int(sys.argv[1]) if len(sys.argv) > 1 else 15000
OUT = ROOT / "kb_web" / "seed"
assert MODEL_VERSION.startswith("minilm"), "the hosted copy embeds queries with MiniLM, so the map must be MiniLM space"

conn = psycopg.connect(host="127.0.0.1", port=int(ENV["KB_PORT"]), dbname="kbcloud",
                       user=ENV["POSTGRES_USER"], password=ENV["POSTGRES_PASSWORD"])
cur = conn.cursor()
# every small-source text, plus an even 1-in-N slice of the man pages (same rule as the local explorer's sample)
cur.execute("""SELECT count(*) FILTER (WHERE r.source = 'man'), count(*) FROM bodies b
               JOIN LATERAL (SELECT source FROM chunk_refs WHERE body_id = b.id ORDER BY id LIMIT 1) r ON true""")
bulk, total = cur.fetchone()
stride = max(1, -(-bulk // max(MAX_POINTS - (total - bulk), 1000)))
cur.execute("""SELECT b.id, r.source, e.name || ' · ' || r.section, v.q
               FROM bodies b JOIN body_vec v ON v.id = b.id
               JOIN LATERAL (SELECT entity_id, section, source FROM chunk_refs WHERE body_id = b.id ORDER BY id LIMIT 1) r ON true
               JOIN entities e ON e.id = r.entity_id
               WHERE r.source <> 'man' OR mod(b.id, %s) = 0 ORDER BY b.id""", (stride,))
rows = cur.fetchall()
vecs = decode(cur, [r[3] for r in rows])
vecs /= np.linalg.norm(vecs, axis=1, keepdims=True)
print(f"{len(rows)} points (stride {stride} over {bulk} man texts)")

t = time.time()
index = TraceHNSW(vecs, m=16, ef_construction=64)
print(f"HNSW built in {time.time() - t:.0f} s")
t = time.time()
raw = umap.UMAP(n_components=3, n_neighbors=15, min_dist=0.1, metric="cosine", random_state=7).fit_transform(index.v)
center = raw.mean(0)
coords = (raw - center) * (320.0 / np.abs(raw - center).max())   # same scene scale as the local explorer
print(f"UMAP in {time.time() - t:.0f} s")

# how well the 3-D map keeps each point's 10 nearest neighbours (shown in the UI, as in the local explorer)
from sklearn.neighbors import NearestNeighbors   # noqa: E402  (umap depends on scikit-learn)
a = NearestNeighbors(n_neighbors=11, metric="cosine").fit(index.v).kneighbors(index.v, return_distance=False)[:, 1:]
b = NearestNeighbors(n_neighbors=11).fit(coords).kneighbors(coords, return_distance=False)[:, 1:]
preservation = float(np.mean([len(set(x) & set(y)) / 10 for x, y in zip(a, b)]))
rng = np.random.default_rng(3)
pick = rng.choice(len(rows), min(200, len(rows)), replace=False)
exact = np.argsort(-(index.v[pick] @ index.v.T), axis=1)[:, :10]
recall = float(np.mean([len({e for e, _ in index.search(index.v[p], 10, 40)[0]} & set(exact[i])) / 10 for i, p in enumerate(pick)]))
print(f"neighbour preservation {preservation:.3f}, HNSW recall@10 vs exact {recall:.3f}")

OUT.mkdir(parents=True, exist_ok=True)
np.savez_compressed(OUT / "map.npz", ids=np.array([r[0] for r in rows], dtype=np.int64), xyz=coords.astype(np.float32),
                    source=np.array([r[1] for r in rows]), title=np.array([r[2][:90] for r in rows]),
                    stats=np.array([preservation, recall], dtype=np.float32))
index.v = None   # vectors are rebuilt from body_vec at start-up; keep the pickle to the graph
save(index, OUT / "map_hnsw.pkl")
for f in ("map.npz", "map_hnsw.pkl"):
    print(f, f"{(OUT / f).stat().st_size / 1e6:.1f} MB")
