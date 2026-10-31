"""Adapter to the hybrid search engine in knowledge_base/scripts (keyword + flag + vector lanes fused with RRF).
The engine opens its own psycopg connection from DATABASE_URL; the app only calls these two functions."""
import hashlib
import os
import sys
import threading
from pathlib import Path

KB_SCRIPTS = Path(os.environ.get("KB_SCRIPTS", Path(__file__).resolve().parent.parent / "knowledge_base" / "scripts"))
HIDDEN_SOURCES = ("attack", "cwe", "kev")   # personal-use sources; never shown by this app
_lock = threading.Lock()   # the engine caches the embedding model and the vector codes in module globals
_engine = None


def engine():
    global _engine
    if _engine is None:
        sys.path.insert(0, str(KB_SCRIPTS))
        import search   # noqa: E402  (also imports common: reads DATABASE_URL, KB_EMBEDDER)
        _engine = search
    return _engine


def run(query, limit, source=None, sources=()):
    """Top `limit` passages for a query: [(chunk_id, entity, section, source, version, text, also_in, score, lanes)].
    `source` keeps one source; everything in HIDDEN_SOURCES is always excluded."""
    exclude = set(HIDDEN_SOURCES)
    if source:
        exclude |= {s for s in sources if s != source}
    with _lock:
        hits = engine().search(query, limit=limit, exclude=tuple(sorted(exclude)))
    return [(cid, ent, sec, src, ver, body, also, score, via)
            for (cid, ent, sec, src, ver, body, also), score, via in hits]


def add_passage(entity_id, entity_name, section, text, source="user"):
    """Insert one passage in a single transaction: the text (stored once per distinct body), its vector codes and the
    chunk_refs row. Returns (True if the passage was new, the text's bodies.id). Embedding happens before the transaction opens."""
    engine()
    common = sys.modules["common"]
    text = text.strip()
    vec = common.embed([f"{entity_name} {section}: {text}"])[0]
    h = hashlib.sha256(f"{entity_id}|{section}|{text}".encode()).hexdigest()
    with _lock, common.connect() as conn:          # psycopg: commit on success, rollback on any exception
        with conn.transaction():
            cur = conn.cursor()
            new = common.store_chunk(cur, entity_id, section, source, None, 3, False, text,
                                     sorted(set(common.extract_flags(text))), h, vec) == 1
            cur.execute("SELECT id FROM bodies WHERE hash = %s", (hashlib.md5(text.encode()).digest(),))
            return new, cur.fetchone()[0]
