import os, re, shlex
from pathlib import Path
import psycopg

ROOT = Path(__file__).resolve().parent.parent
# Embedder, picked by KB_EMBEDDER. Each one has its own code table and frozen 4-bit scale (vec_quant row),
# so a second model can be tested against the live data without touching the first. Both are 384-d and capped at 256 tokens.
# Ingest writes codes for the active model only; scripts/embed_codes.py fills any other model's table afterwards.
EMBEDDERS = {   # name: (fastembed model, model_version, code table)
    "minilm": ("sentence-transformers/all-MiniLM-L6-v2", "minilm-l6-v2-halfvec", "body_vec"),
    "granite": ("ibm-granite/granite-embedding-small-english-r2", "granite-r2-256", "body_vec_granite"),
}
EMBEDDER = os.environ.get("KB_EMBEDDER", "granite")   # granite since 2026-10-27 (README "Embedder change")
MODEL_NAME, MODEL_VERSION, VEC_TABLE = EMBEDDERS[EMBEDDER]

def load_env():
    """.env if present (local use), overridden by the process environment (containers / cloud hosts have no .env)."""
    env = {}
    if (ROOT / ".env").exists():
        for line in (ROOT / ".env").read_text().splitlines():
            if line.strip() and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = shlex.split(v)[0] if v.strip() else ""
    env.update(os.environ)
    return env

ENV = load_env()
HDD = Path(ENV.get("KB_HDD", ROOT))
MODEL_DIR = Path(ENV["KB_MODEL_DIR"]) if ENV.get("KB_MODEL_DIR") else (ROOT / "models" if EMBEDDER == "minilm" else HDD / "models")

def connect():
    if ENV.get("DATABASE_URL"):   # hosted Postgres (one URL); the parts below are the local docker-compose setup
        return psycopg.connect(ENV["DATABASE_URL"])
    return psycopg.connect(
        host="127.0.0.1", port=int(ENV["KB_PORT"]), dbname=ENV["POSTGRES_DB"],
        user=ENV["POSTGRES_USER"], password=ENV["POSTGRES_PASSWORD"])

_model = None
def embed(texts):
    global _model
    if _model is None:
        from fastembed import TextEmbedding
        _model = TextEmbedding(MODEL_NAME, cache_dir=str(MODEL_DIR))
        _model.model.tokenizer.enable_truncation(max_length=256)
    return [v / (sum(x * x for x in v) ** 0.5) for v in _model.embed(texts)]

def vec_lit(v):
    return "[" + ",".join(f"{x:.6f}" for x in v) + "]"

# Literal tokens that must match exactly: flags (-sU, --script=), ATT&CK / CVE / CWE ids.
FLAG_RE = re.compile(r"(?<![\w-])(--?[A-Za-z0-9][\w-]*)")
ID_RE = re.compile(r"\b(T\d{4}(?:\.\d{3})?|CVE-\d{4}-\d{4,}|CWE-\d+|CAPEC-\d+)\b", re.I)

def extract_flags(text):
    out = set(FLAG_RE.findall(text))
    out |= {m.upper() for m in ID_RE.findall(text)}
    return sorted(f for f in out if len(f) >= 2)


# ---- shared ingest helpers -------------------------------------------------
import hashlib
CITE_RE = re.compile(r"\(Citation: [^)]*\)")
MDLINK_RE = re.compile(r"\[([^\]]+)\]\((?:https?://|/)[^)]*\)")

def clean(text):
    """Strip ATT&CK citation markers and markdown links; keep readable text."""
    t = CITE_RE.sub("", text or "")
    t = MDLINK_RE.sub(r"\1", t)
    t = re.sub(r"</?code>", "`", t)
    return re.sub(r"[ \t]+\n", "\n", t).strip()

def upsert_entity(cur, type_, name, ext_id="", domain=(), aliases=(), summary=None, version=None, facts=None):
    import json
    cur.execute("""INSERT INTO entities(name,type,ext_id,domain,aliases,summary,version,facts)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (type,name,ext_id) DO UPDATE SET domain=EXCLUDED.domain, aliases=EXCLUDED.aliases,
                       summary=EXCLUDED.summary, version=EXCLUDED.version, facts=EXCLUDED.facts
                   RETURNING id""",
                (name, type_, ext_id, list(domain), list(aliases), summary, version, json.dumps(facts or {})))
    return cur.fetchone()[0]

def link(cur, src, dst, rel, note=None, source=""):
    if src is None or dst is None or src == dst: return
    cur.execute("""INSERT INTO entity_links(src,dst,rel,note,source) VALUES (%s,%s,%s,%s,%s)
                   ON CONFLICT (src,dst,rel) DO UPDATE SET note=COALESCE(EXCLUDED.note, entity_links.note)""",
                (src, dst, rel, note, source))

# ---- storage: each distinct text is stored once (bodies), chunk_refs map (entity, section, source) -> body ------
# Vectors are kept as two codes per body (body_vec), not as floats: b = sign bits (48 B, brute-force Hamming scan for
# candidates) and q = 4 bits per dimension (192 B, re-sorts the candidates against the float query). The 4-bit scale is
# frozen in vec_quant so ingest and query always agree. Measured on the tldr dev split: same hybrid MRR as halfvec re-sort.
import numpy as np
QUANT_BITS = 4
_quant = {}
def quant(cur, version=None):
    """(lo, step) per dimension for a model version (default: the active one), frozen when its codes were built
    (0.5/99.5 percentiles of the corpus)."""
    version = version or MODEL_VERSION
    if version not in _quant:
        cur.execute("SELECT lo, hi FROM vec_quant WHERE model_version = %s", (version,))
        lo, hi = (np.array(a, dtype=np.float32) for a in cur.fetchone())
        _quant[version] = (lo, (hi - lo) / (2 ** QUANT_BITS - 1))
    return _quant[version]

def sign_bits(v):
    return "".join("1" if x > 0 else "0" for x in v)          # same rule as pgvector's binary_quantize

def encode(cur, v, version=None):
    lo, step = quant(cur, version)
    c = np.clip(np.rint((np.asarray(v, dtype=np.float32) - lo) / step), 0, 2 ** QUANT_BITS - 1).astype(np.uint8)
    return sign_bits(v), (c[0::2] << 4 | c[1::2]).tobytes()

def decode(cur, qbytes, version=None):
    """n x 192-byte codes -> n x 384 float32 (approximate vectors), with the scale of the model that made them."""
    lo, step = quant(cur, version)
    a = np.frombuffer(b"".join(qbytes), dtype=np.uint8).reshape(len(qbytes), -1)
    c = np.empty((a.shape[0], a.shape[1] * 2), dtype=np.float32); c[:, 0::2] = a >> 4; c[:, 1::2] = a & 15
    return c * step + lo

def store_chunk(cur, eid, sec, source, version, trust, is_code, body, flags, content_hash, vec):
    """Insert one chunk; the text, its flags and its vector codes are stored once per distinct body. Returns 1 if new."""
    h = hashlib.md5(body.encode()).digest()
    cur.execute("""INSERT INTO bodies(id, hash, body, flags, trust) VALUES (nextval('chunks_id_seq'), %s, %s, %s, %s)
                   ON CONFLICT (hash) DO UPDATE SET flags = ARRAY(SELECT DISTINCT f FROM unnest(bodies.flags || EXCLUDED.flags) f ORDER BY 1),
                                                    trust = least(bodies.trust, EXCLUDED.trust)
                   WHERE NOT EXCLUDED.flags <@ bodies.flags OR EXCLUDED.trust < bodies.trust   -- no rewrite when nothing changes
                   RETURNING id, (xmax = 0)""", (h, body, flags, trust))
    row = cur.fetchone()
    if row is None:
        cur.execute("SELECT id, false FROM bodies WHERE hash = %s", (h,)); row = cur.fetchone()
    bid, new_body = row
    if new_body:
        b, q = encode(cur, vec)
        cur.execute(f"INSERT INTO {VEC_TABLE}(id, b, q) VALUES (%s, %s::bit(384), %s)", (bid, b, q))
    cur.execute("""INSERT INTO chunk_refs(id, entity_id, section, source, source_version, trust, is_code, content_hash, model_version, body_id)
                   VALUES (nextval('chunks_id_seq'), %s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (content_hash) DO NOTHING""",
                (eid, sec, source, version, trust, is_code, bytes.fromhex(content_hash), MODEL_VERSION, bid))
    return cur.rowcount

def add_chunks(cur, eid, name_prefix, items, source, version, trust=1, is_code=False, extra_flags=()):
    """items: [(section, text)]. Embeds in one batch; dedupes on content_hash. Returns rows inserted."""
    items = [(s, t.strip()) for s, t in items if t and len(t.strip()) > 20]
    if not items: return 0
    vecs = embed([f"{name_prefix} {s}: {t}" for s, t in items])
    n = 0
    for (sec, body), v in zip(items, vecs):
        h = hashlib.sha256(f"{eid}|{sec}|{body}".encode()).hexdigest()
        flags = sorted(set(extract_flags(body)) | {f for f in extra_flags if f})
        n += store_chunk(cur, eid, sec, source, version, trust, is_code, body, flags, h, v)
    return n
