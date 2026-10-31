"""Fill a model's vector-code table (see common.EMBEDDERS) for every body that has no codes in it yet.
Ingest writes codes for the active model only, so run this for the other model after an ingest to keep it current
(the explorer's map reads MiniLM codes; a rollback to MiniLM needs them too).

  KB_EMBEDDER=minilm  .venv/bin/python scripts/embed_codes.py      # backfill, ~60 texts/s
  KB_EMBEDDER=granite .venv/bin/python scripts/embed_codes.py      # ~21 texts/s on this CPU

If the model has no vec_quant row yet, this builds its table from scratch: it embeds every body, freezes the 4-bit
scale at the 0.5/99.5 percentiles and writes all codes (granite: 180k texts in ~2.3 h). The text embedded for a body is
'<name> <section>: <body>' of its representative ref (chunk_refs.id = bodies.id), as at ingest."""
import sys, time, numpy as np
from common import *

conn = connect(); cur = conn.cursor()
cur.execute(f"""CREATE TABLE IF NOT EXISTS {VEC_TABLE} (id bigint PRIMARY KEY REFERENCES bodies(id) ON DELETE CASCADE,
                b bit(384) NOT NULL, q bytea NOT NULL)""")
cur.execute(f"ALTER TABLE {VEC_TABLE} SET (parallel_workers = 4)")
cur.execute(f"""SELECT b.id, e.name || ' ' || r.section || ': ' || b.body FROM bodies b
                JOIN chunk_refs r ON r.id = b.id JOIN entities e ON e.id = r.entity_id
                WHERE NOT EXISTS (SELECT 1 FROM {VEC_TABLE} v WHERE v.id = b.id) ORDER BY b.id""")
todo = cur.fetchall(); conn.commit()
cur.execute("SELECT 1 FROM vec_quant WHERE model_version = %s", (MODEL_VERSION,)); fresh = cur.fetchone() is None
print(f"{EMBEDDER}: {len(todo)} bodies without codes in {VEC_TABLE}" + (" (no frozen scale yet: full build)" if fresh else ""), flush=True)
if not todo: sys.exit()

def write(rows, V):
    quant(cur)   # load the frozen scale now: encode() would otherwise query it in the middle of the COPY and hang
    with cur.copy(f"COPY {VEC_TABLE} (id, b, q) FROM STDIN") as cp:
        for (bid, _), v in zip(rows, V):
            b, q = encode(cur, v); cp.write_row((bid, b, q))
    conn.commit()

t, BATCH = time.time(), 4096
if fresh:   # the scale needs the whole corpus, so embed everything first (float16 in memory: 180k x 384 = 140 MB)
    cur.execute(f"SELECT count(*) FROM {VEC_TABLE}"); assert cur.fetchone()[0] == 0, f"{VEC_TABLE} has codes but no scale"
    V = np.empty((len(todo), 384), dtype=np.float16)
    for s in range(0, len(todo), BATCH):
        V[s:s + BATCH] = embed([x for _, x in todo[s:s + BATCH]])
        print(f"{min(s + BATCH, len(todo))}/{len(todo)}  {min(s + BATCH, len(todo)) / (time.time() - t):.1f}/s", flush=True)
    F = V.astype(np.float32)
    cur.execute("INSERT INTO vec_quant VALUES (%s, %s, %s)",
                (MODEL_VERSION, np.percentile(F, 0.5, axis=0).tolist(), np.percentile(F, 99.5, axis=0).tolist()))
    write(todo, F)
else:       # incremental: the frozen scale already exists, write per batch (an interrupted run resumes where it stopped)
    for s in range(0, len(todo), BATCH):
        write(todo[s:s + BATCH], embed([x for _, x in todo[s:s + BATCH]]))
        print(f"{min(s + BATCH, len(todo))}/{len(todo)}  {min(s + BATCH, len(todo)) / (time.time() - t):.1f}/s", flush=True)
cur.execute(f"ANALYZE {VEC_TABLE}"); conn.commit()
cur.execute(f"SELECT count(*), (SELECT count(*) FROM bodies) FROM {VEC_TABLE}"); print("codes / bodies:", cur.fetchone())
