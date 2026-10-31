"""Rebuild the explorer's scratch DB `kbview` (table documents) from securitykb. Run after any ingest, then
restart the explorer. The KB itself is only read. One point per distinct text (bodies.id), labelled with its first chunk;
the vector is the decoded 4-bit code (the KB stores no float vectors).

Only the explorer's map sample is copied (MAX_POINTS, default 30k): every security row plus an even 1-in-N slice of the
bulk man/kernel docs. Copying all 290k rows duplicated 1.2 GB; search hits outside the sample are read from securitykb."""
import os, sys; sys.path.insert(0, "/home/meh/security-kb/scripts")
import psycopg
from common import ENV, EMBEDDERS, connect, decode
MAP_MODEL, MAP_VERSION, MAP_TABLE = EMBEDDERS["minilm"]   # the explorer embeds its queries with MiniLM, so its map is MiniLM space
dst = psycopg.connect(host="127.0.0.1", port=int(ENV["KB_PORT"]), dbname="kbview", user=ENV["POSTGRES_USER"], password=ENV["POSTGRES_PASSWORD"])
d = dst.cursor()
d.execute("""DROP TABLE IF EXISTS documents; CREATE TABLE documents (id bigint PRIMARY KEY, category text NOT NULL,
  body text NOT NULL, embedding vector(384) NOT NULL, embedding_model_version text NOT NULL DEFAULT 'all-MiniLM-L6-v2')""")
MAX_POINTS, BULK = int(os.environ.get("EXPLORER_MAX_POINTS", "30000")), ["man", "kernel-doc"]
src = connect(); s = src.cursor()
s.execute("""SELECT count(*) FILTER (WHERE r.source = ANY(%s)), count(*) FROM bodies b
             JOIN LATERAL (SELECT source FROM chunk_refs WHERE body_id = b.id ORDER BY id LIMIT 1) r ON true""", (BULK,))
bulk, total = s.fetchone(); stride = max(1, -(-bulk // max(MAX_POINTS - (total - bulk), 1000)))
s.execute(f"""SELECT b.id, r.source, e.name || ' · ' || r.section || E'\n' || b.body, v.q
             FROM bodies b JOIN {MAP_TABLE} v ON v.id = b.id
             JOIN LATERAL (SELECT entity_id, section, source FROM chunk_refs WHERE body_id = b.id ORDER BY id LIMIT 1) r ON true
             JOIN entities e ON e.id = r.entity_id
             WHERE NOT r.source = ANY(%s) OR mod(b.id, %s) = 0 ORDER BY b.id""", (BULK, stride))
rows = s.fetchall()
vecs = decode(s, [r[3] for r in rows], MAP_VERSION)
vecs /= ((vecs ** 2).sum(1, keepdims=True) ** 0.5)
with d.copy("COPY documents (id, category, body, embedding) FROM STDIN") as cp:
    for r, v in zip(rows, vecs): cp.write_row((r[0], r[1], r[2], "[" + ",".join(f"{x:.5f}" for x in v) + "]"))
d.execute("CREATE INDEX documents_hnsw ON documents USING hnsw (embedding vector_cosine_ops)")
dst.commit(); d.execute("SELECT count(*), count(DISTINCT category) FROM documents"); print(d.fetchone())
d.execute("SELECT category, count(*) FROM documents GROUP BY 1 ORDER BY 2 DESC"); print(d.fetchall())
