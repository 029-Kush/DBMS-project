"""VectorSpace explorer: a 3D view of the vector database with an auditable search trace.

Run:  uvicorn app:app --port 8765     (from selfheal_pgvector/explorer, env as for the scripts)

Results are served from an in-process HNSW (hnsw_trace.py) so the drawn path is the
search that produced them; every answer is also checked against pgvector's own HNSW
and against brute force, and the overlap is returned. The 3D positions are a UMAP
projection of the real 384-d vectors; their quality is measured and returned too.
"""
import csv
import os
import sys
import threading
import time
from contextlib import closing, contextmanager
from pathlib import Path

import numpy as np
import psycopg2
import umap
from fastapi import Body, FastAPI, Query
from fastapi.responses import FileResponse, JSONResponse
from psycopg2.extras import execute_values
from sklearn.neighbors import NearestNeighbors

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "scripts"))

import embed_model  # noqa: E402
from config import CURRENT_MODEL_VERSION, DB_DSN, EMBED_DIM, EMBEDDER  # noqa: E402
import heal  # noqa: E402
from eval_recall import main as run_health_check  # noqa: E402
from hnsw_trace import TraceHNSW  # noqa: E402
from load_data import vec_to_pg  # noqa: E402

M, EF_CONSTRUCTION, EF_SEARCH = 16, 64, 40
SUGGESTIONS = [
    "a chip maker unveils a faster processor",
    "oil prices rise as supply concerns grow",
    "a team wins the championship final in overtime",
    "an election result disputed by opposition leaders",
]
STALE_CHECK_SECONDS = 5

app = FastAPI(title="VectorSpace explorer")
STATE = {"ready": False, "building": False, "error": None}
LOCK = threading.Lock()
MODEL = None


@contextmanager
def db():
    """A cursor on a fresh connection that is always closed (psycopg2's own `with` does not close)."""
    with closing(psycopg2.connect(DB_DSN)) as conn:
        with conn, conn.cursor() as cur:
            yield cur


def fingerprint(cur):
    cur.execute("SELECT count(*), md5(string_agg(md5(embedding::text), '' ORDER BY id)) FROM documents")
    n, h = cur.fetchone()
    return f"{n}:{h}"


def neighbour_preservation(vectors, coords, k=10):
    a = NearestNeighbors(n_neighbors=k + 1, metric="cosine").fit(vectors).kneighbors(vectors, return_distance=False)[:, 1:]
    b = NearestNeighbors(n_neighbors=k + 1).fit(coords).kneighbors(coords, return_distance=False)[:, 1:]
    return float(np.mean([len(set(x) & set(y)) / k for x, y in zip(a, b)]))


def short_title(body, words=8):
    parts = body.split()
    return " ".join(parts[:words]) + ("…" if len(parts) > words else "")


def build():
    """(Re)build the index and projection from the database. Returns the new state dict."""
    t0 = time.time()
    with db() as cur:
        fp = fingerprint(cur)
        cur.execute("SELECT id, category, body, embedding::text FROM documents ORDER BY id")
        rows = cur.fetchall()
    ids = [r[0] for r in rows]
    vectors = np.array([[float(x) for x in r[3].strip("[]").split(",")] for r in rows], dtype=np.float32)
    index = TraceHNSW(vectors, m=M, ef_construction=EF_CONSTRUCTION)
    reducer = umap.UMAP(n_components=3, n_neighbors=15, min_dist=0.1, metric="cosine", random_state=7)
    raw = reducer.fit_transform(index.v)
    center = raw.mean(0)
    scale = 320.0 / np.abs(raw - center).max()  # fit the cloud into roughly +-320 scene units
    coords = (raw - center) * scale
    # recall of the in-process index against brute force, on 200 of its own documents' texts
    rng = np.random.default_rng(3)
    pick = rng.choice(len(ids), min(200, len(ids)), replace=False)
    exact = np.argsort(-(index.v[pick] @ index.v.T), axis=1)[:, :10]
    rec = float(np.mean([len({e for e, _ in index.search(index.v[p], 10, EF_SEARCH)[0]} & set(exact[i])) / 10
                         for i, p in enumerate(pick)]))
    return {
        "fingerprint": fp, "ids": ids, "pos": {i: n for n, i in enumerate(ids)},
        "category": [r[1] for r in rows], "title": [short_title(r[2]) for r in rows],
        "body": [r[2] for r in rows], "index": index, "reducer": reducer, "coords": coords, "center": center, "scale": scale,
        "projection_score": neighbour_preservation(index.v, coords),
        "index_recall": rec, "built_seconds": round(time.time() - t0, 1), "checked_at": time.time(),
    }


def refresh_async():
    def work():
        try:
            new = build()
            with LOCK:
                STATE.update(new, building=False, error=None)
        except Exception as exc:  # keep serving the old index, flagged stale
            with LOCK:
                STATE.update(building=False, error=str(exc))
    with LOCK:
        if STATE["building"]:
            return
        STATE["building"] = True
    threading.Thread(target=work, daemon=True).start()


@app.on_event("startup")
def startup():
    global MODEL
    MODEL = embed_model.load_embedder()
    STATE.update(build())
    STATE["ready"] = True


def stale_flag():
    """True when the database vectors changed since the index was built (checked at most every few s)."""
    if time.time() - STATE["checked_at"] > STALE_CHECK_SECONDS:
        STATE["checked_at"] = time.time()
        with db() as cur:
            now = fingerprint(cur)
        if now != STATE["fingerprint"]:
            STATE["stale"] = True
            refresh_async()
        else:
            STATE["stale"] = False
    return bool(STATE.get("stale")) or STATE["building"]


@app.get("/")
def home():
    return FileResponse(HERE / "static" / "index.html")


@app.get("/api/meta")
def meta():
    return {
        "documents": len(STATE["ids"]), "dim": EMBED_DIM, "embedder": EMBEDDER, "model": CURRENT_MODEL_VERSION,
        "index": {"type": "HNSW", "metric": "cosine", "m": M, "ef_construction": EF_CONSTRUCTION, "ef_search": EF_SEARCH,
                  "top_layer": int(STATE["index"].top)},
        "projection": {"method": "UMAP (3-D, cosine)", "neighbour_preservation": round(STATE["projection_score"], 3)},
        "index_recall_vs_exact": round(STATE["index_recall"], 3), "built_seconds": STATE["built_seconds"],
        "suggestions": SUGGESTIONS, "stale": stale_flag(),
    }


@app.get("/api/points")
def points():
    c = STATE["coords"]
    return {"ids": STATE["ids"], "xyz": [[round(float(a), 2), round(float(b), 2), round(float(d), 2)] for a, b, d in c],
            "category": STATE["category"], "title": STATE["title"]}


@app.get("/api/search")
def search(q: str = Query(..., min_length=1, max_length=500), k: int = Query(10, ge=1, le=20)):
    if not q.strip():
        return JSONResponse({"error": "empty query"}, status_code=400)
    vec = np.array(MODEL.embed([q])[0], dtype=np.float32)
    if not np.any(vec):
        return JSONResponse({"error": "query has no usable content for this model"}, status_code=400)
    stale = stale_flag()
    with LOCK:
        index, pos, ids = STATE["index"], STATE["pos"], STATE["ids"]
        reducer, body, title, category = STATE["reducer"], STATE["body"], STATE["title"], STATE["category"]
    t0 = time.perf_counter()
    with LOCK:
        top, trace = index.search(vec, k=k, ef=EF_SEARCH)
    search_ms = (time.perf_counter() - t0) * 1e3
    qn = vec / max(float(np.linalg.norm(vec)), 1e-12)
    exact = [int(i) for i in np.argsort(-(index.v @ qn))[:k]]
    pg_ids, pg_ms = [], None
    try:
        with db() as cur:
            cur.execute("SET LOCAL enable_seqscan = off")
            t1 = time.perf_counter()
            cur.execute("SELECT id FROM documents ORDER BY embedding <=> %s::vector LIMIT %s", (vec_to_pg(vec.tolist()), k))
            pg_ids = [r[0] for r in cur.fetchall()]
            pg_ms = (time.perf_counter() - t1) * 1e3
    except Exception as exc:  # database down: still show our own result, flag the check as unavailable
        pg_ids = None
        pg_error = str(exc)
    mine_db = [ids[n] for n, _ in top]
    # place the query in the same frame as the stored coordinates
    qpos = [round(float(x), 2) for x in (reducer.transform(qn.reshape(1, -1))[0] - STATE["center"]) * STATE["scale"]]
    def nodes(lst):
        return [{"n": int(n), "id": ids[n], "dist": round(d, 4), "score": round(1 - d, 4), "title": title[n],
                 "category": category[n], "text": body[n][:400]} for n, d in lst]
    out = {
        "query": q, "k": k, "search_ms": round(search_ms, 2), "pgvector_ms": None if pg_ms is None else round(pg_ms, 2),
        "results": nodes(top), "near_miss": nodes(trace["near_miss"]),
        "paths": {str(n): p for n, p in trace["paths"].items()},
        "steps": trace["steps"], "visited_layer0": trace["visited_layer0"], "documents": len(ids),
        "query_xyz": qpos,
        "check": {
            "own_vs_exact": len(set(n for n, _ in top) & set(exact)),
            "own_vs_pgvector": None if pg_ids is None else len(set(mine_db) & set(pg_ids)),
            "pgvector_vs_exact": None if pg_ids is None else len(set(pg_ids) & {ids[n] for n in exact}),
            "k": k,
        },
        "stale": stale,
    }
    if pg_ids is None:
        out["check"]["pgvector_error"] = pg_error
    return out


# ---------------------------------------------------------------------------------------------
# Add information / healing (the "Add information" column in the UI)
# ---------------------------------------------------------------------------------------------
OPLOCK = threading.Lock()          # one ingest/heal at a time (searches keep working)
FAULTS_ENABLED = os.environ.get("EXPLORER_ENABLE_FAULTS") == "1"   # demo mode: allows the faulty-batch button
INSERTS = os.environ.get("SELFHEAL_INSERTS")                        # optional CSV (category, body) of sample articles
OTHER_EMBEDDER = "bge" if EMBEDDER != "bge" else "minilm"
MAX_VISIBLE_DIFFS = 80
OPS = {"pool": None, "used": {}, "models": {}}


def _model(name):
    if name == EMBEDDER:
        return MODEL
    if name not in OPS["models"]:
        OPS["models"][name] = embed_model.load_embedder(name)
    return OPS["models"][name]


def _pool():
    if OPS["pool"] is None:
        pool = {}
        if INSERTS and Path(INSERTS).exists():
            for r in csv.DictReader(open(INSERTS)):
                pool.setdefault(r["category"], []).append(r)
        OPS["pool"] = pool
    return OPS["pool"]


def _place(vectors):
    unit = vectors / np.maximum(np.linalg.norm(vectors, axis=1, keepdims=True), 1e-12)
    return ((STATE["reducer"].transform(unit) - STATE["center"]) * STATE["scale"]).astype(np.float32)


def _health_payload(h):
    if not h:
        return None
    return {"status": h["status"], "issues": list(h["issues"]), "recall": h["recall_at_k"],
            "ann": h.get("ann_recall"), "sentinel": h.get("sentinel_mismatch_pct")}


def _sync_fingerprint():
    with db() as cur:
        STATE["fingerprint"] = fingerprint(cur)
    STATE["checked_at"] = time.time()
    STATE["stale"] = False


def _fingerprints():
    with db() as cur:
        cur.execute("SELECT id, md5(embedding::text) FROM documents")
        return dict(cur.fetchall())


def _vectors_from_db():
    with db() as cur:
        cur.execute("SELECT embedding::text FROM documents ORDER BY id")
        return np.array([[float(x) for x in r[0].strip("[]").split(",")] for r in cur.fetchall()], dtype=np.float32)


def _insert(texts, cats, vecs):
    """Insert documents (labelled as the current model), add them to the graph and the map."""
    with db() as cur:
        res = execute_values(cur, "INSERT INTO documents (category, body, embedding, embedding_model_version) VALUES %s RETURNING id",
                             [(c, t, vec_to_pg(v.tolist()), CURRENT_MODEL_VERSION) for c, t, v in zip(cats, texts, vecs)],
                             template="(%s, %s, %s::vector, %s)", fetch=True)
        new_ids = [r[0] for r in res]
    with LOCK:
        diffs = STATE["index"].add_batch(vecs)
        xyz = _place(vecs)
        first = len(STATE["ids"])
        for k, (i, c, t) in enumerate(zip(new_ids, cats, texts)):
            STATE["pos"][i] = first + k
            STATE["ids"].append(i); STATE["category"].append(c); STATE["body"].append(t); STATE["title"].append(short_title(t))
        STATE["coords"] = np.vstack([STATE["coords"], xyz])
    _sync_fingerprint()
    out = []
    for k, d in enumerate(diffs):
        n = first + k
        item = {"n": n, "id": new_ids[k], "xyz": [round(float(x), 2) for x in xyz[k]], "cat": cats[k], "title": STATE["title"][n]}
        if k < MAX_VISIBLE_DIFFS:
            item.update(links=d["links"][:16], touched=d["touched"][:16], pruned=d["pruned"][:12])
        out.append(item)
    return out, diffs


@app.get("/api/info")
def info():
    pool = _pool()
    return {"faults_enabled": FAULTS_ENABLED, "model": CURRENT_MODEL_VERSION, "other_model": OTHER_EMBEDDER,
            "pool_left": {c: len(r) - OPS["used"].get(c, 0) for c, r in pool.items()},
            "categories": sorted(set(STATE["category"])), "documents": len(STATE["ids"])}


@app.post("/api/health")
def health():
    t = time.time()
    h = run_health_check(note="explorer_tick")
    return {"health": _health_payload(h), "seconds": round(time.time() - t, 2), "documents": len(STATE["ids"])}


@app.post("/api/ingest")
def ingest(body: dict = Body(...)):
    """Add sample articles (optionally embedded by the wrong model, demo mode only) or the user's own text."""
    faulty = bool(body.get("faulty", False))
    if faulty and not FAULTS_ENABLED:
        return JSONResponse({"error": "fault injection is off; start the server with EXPLORER_ENABLE_FAULTS=1 on a scratch database"}, status_code=403)
    texts_in = body.get("texts")
    with OPLOCK:
        t0 = time.time()
        if texts_in:
            cat = (body.get("category") or "other").strip()[:40] or "other"
            texts = [t.strip() for t in texts_in if t and t.strip()][:200]
            cats = [cat] * len(texts)
        else:
            pool = _pool()
            if not pool:
                return JSONResponse({"error": "no sample articles configured (set SELFHEAL_INSERTS) — paste your own text instead"}, status_code=400)
            count = max(1, min(int(body.get("count", 100)), 600))
            topic = body.get("topic") or None
            names = [topic] if topic else list(pool)
            texts, cats = [], []
            while len(texts) < count and any(OPS["used"].get(c, 0) < len(pool.get(c, [])) for c in names):
                for c in names:
                    u = OPS["used"].get(c, 0)
                    if u < len(pool.get(c, [])) and len(texts) < count:
                        texts.append(pool[c][u]["body"]); cats.append(c); OPS["used"][c] = u + 1
        if not texts:
            return JSONResponse({"error": "nothing to add"}, status_code=400)
        vecs = np.array(_model(OTHER_EMBEDDER if faulty else EMBEDDER).embed(texts), dtype=np.float32)
        out, diffs = _insert(texts, cats, vecs)
        return {"new": out, "faulty": faulty, "count": len(out), "total": len(STATE["ids"]),
                "model": OTHER_EMBEDDER if faulty else CURRENT_MODEL_VERSION,
                "neighbour_lists_updated": len({c for d in diffs for c in d["touched"]}),
                "links_pruned": sum(len(d["pruned"]) for d in diffs), "seconds": round(time.time() - t0, 2)}


@app.post("/api/heal")
def run_heal():
    with OPLOCK:
        t0 = time.time()
        before_fp = _fingerprints()
        hist = heal.heal_until_stable("explorer")
        after_fp = _fingerprints()
        changed = [i for i, h in after_fp.items() if before_fp.get(i) != h and i in before_fp]
        moved = []
        if changed:
            with db() as cur:
                cur.execute("SELECT id, embedding::text FROM documents WHERE id = ANY(%s) ORDER BY id", (changed,))
                got = cur.fetchall()
            vecs = np.array([[float(x) for x in r[1].strip("[]").split(",")] for r in got], dtype=np.float32)
            newxyz = _place(vecs)
            new_index = TraceHNSW(_vectors_from_db())      # the replica graph is rebuilt from the repaired vectors
            with LOCK:
                for (i, _), p in zip(got, newxyz):
                    n = STATE["pos"][i]
                    moved.append({"n": n, "id": i, "from": [round(float(x), 2) for x in STATE["coords"][n]], "to": [round(float(x), 2) for x in p]})
                    STATE["coords"][n] = p
                STATE["index"] = new_index
        _sync_fingerprint()
        cycles = [{"outcome": o["outcome"], "actions": [str(a) for a in (o.get("actions") or ())],
                   "before": _health_payload(o.get("before")), "after": _health_payload(o.get("after"))} for o in hist]
        return {"cycles": cycles, "repaired": moved, "seconds": round(time.time() - t0, 2)}
