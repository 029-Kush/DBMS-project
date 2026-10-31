"""One-off migration (2026-10-26): chunks(body, embedding, tsv) -> bodies + chunk_refs + body_vec. See sql/003_bodies.sql.
Step 1 (bodies, chunk_refs) is plain SQL in that file; this script does step 2: freeze the 4-bit scale from the stored
halfvecs and write the per-body codes. Run before the old `chunks` table is dropped (it reads chunks.embedding)."""
import io, time
import numpy as np
from common import *

t0 = time.time()
conn = connect(); cur = conn.cursor()
cur.execute("""CREATE TABLE IF NOT EXISTS vec_quant (model_version text PRIMARY KEY, lo real[] NOT NULL, hi real[] NOT NULL)""")
cur.execute("""CREATE TABLE IF NOT EXISTS body_vec (id bigint PRIMARY KEY REFERENCES bodies(id) ON DELETE CASCADE,
                                                  b bit(384) NOT NULL, q bytea NOT NULL)""")
cur.execute("ALTER TABLE body_vec SET (parallel_workers = 4)")
cur.execute("SELECT b.id, c.embedding::text FROM bodies b JOIN chunks c ON c.id = b.id WHERE NOT EXISTS (SELECT 1 FROM body_vec v WHERE v.id = b.id)")
rows = cur.fetchall()
ids = [r[0] for r in rows]
X = np.array([np.array(r[1].strip("[]").split(","), dtype=np.float32) for r in rows])
print(f"{len(ids)} bodies to encode ({time.time() - t0:.0f}s)")
cur.execute("SELECT 1 FROM vec_quant WHERE model_version = %s", (MODEL_VERSION,))
if cur.fetchone() is None:
    lo, hi = np.percentile(X, 0.5, axis=0), np.percentile(X, 99.5, axis=0)
    hi = np.maximum(hi, lo + 1e-4)     # three MiniLM dimensions are constant across the corpus
    cur.execute("INSERT INTO vec_quant VALUES (%s, %s, %s)", (MODEL_VERSION, lo.tolist(), hi.tolist()))
buf = io.StringIO()
for i, v in zip(ids, X):
    b, q = encode(cur, v)
    buf.write(f"{i}\t{b}\t\\\\x{q.hex()}\n")
with cur.copy("COPY body_vec (id, b, q) FROM STDIN") as cp:
    cp.write(buf.getvalue())
conn.commit()
cur.execute("SELECT count(*), pg_size_pretty(pg_total_relation_size('body_vec')) FROM body_vec")
print("body_vec:", cur.fetchone(), f"{time.time() - t0:.0f}s")
