"""Self-healing simulator: a sandbox copy of the database where you can ingest new documents,
break them, and watch the real healer reorganise and repair.

Run:  SIM_BASE_DB=minilm5k SELFHEAL_EMBEDDER=minilm uvicorn app:app --port 8766   (from selfheal_pgvector/sim)

What is real: the sandbox database (a copy of SIM_BASE_DB), the embeddings, the inserts, the
health checks and the healer (heal.py, unchanged). What is illustrative: the neighbour graph drawn
on screen is an in-process HNSW with pgvector's parameters (pgvector's own graph is not
observable), and the 3-D positions are a UMAP projection of the real vectors.
"""
import csv
import os
import sys
import threading
import time
from contextlib import closing
from pathlib import Path

import numpy as np
import psycopg2
import umap
from fastapi import Body, FastAPI
from fastapi.responses import FileResponse, JSONResponse
from psycopg2.extras import execute_values

HERE = Path(__file__).resolve().parent
BASE_DB = os.environ.get("SIM_BASE_DB", "minilm5k")
SIM_DB = os.environ.get("SIM_DB", "selfheal_sim")
BASE_DSN = os.environ.get("SELFHEAL_DSN", f"dbname={BASE_DB} user=svuser password=svpass host=127.0.0.1 port=5433")


def dsn_for(db):
    parts = [p for p in BASE_DSN.split() if not p.startswith("dbname=")]
    return " ".join([f"dbname={db}"] + parts)


os.environ["SELFHEAL_DSN"] = dsn_for(SIM_DB)  # the healer and scripts must act on the sandbox only
sys.path.insert(0, str(HERE.parent / "explorer"))
sys.path.insert(0, str(HERE.parent / "scripts"))

import embed_model  # noqa: E402
import heal  # noqa: E402
from config import CURRENT_MODEL_VERSION, DB_DSN, EMBEDDER  # noqa: E402
from eval_recall import main as run_health_check  # noqa: E402
from hnsw_trace import TraceHNSW  # noqa: E402
from load_data import vec_to_pg  # noqa: E402

INSERTS = Path(os.environ.get("SELFHEAL_INSERTS", HERE.parent.parent / "data" / "inserts.csv"))
OTHER_EMBEDDER = "bge" if EMBEDDER != "bge" else "minilm"
MAX_VISIBLE_DIFFS = 80  # per ingest; the rest are inserted without per-node link animation

app = FastAPI(title="Self-healing simulator")
LOCK = threading.RLock()
S = {"ready": False, "busy": None, "log": []}
MODELS = {}


def model(name):
    if name not in MODELS:
        MODELS[name] = embed_model.load_embedder(name)
    return MODELS[name]


def admin(autocommit=True):
    conn = psycopg2.connect(dsn_for("postgres"))
    conn.autocommit = autocommit
    return closing(conn)


def ensure_sandbox(recreate=False):
    with admin() as conn:
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM pg_database WHERE datname=%s", (SIM_DB,))
        exists = cur.fetchone() is not None
        if exists and not recreate:
            return
        for attempt in range(8):  # the template needs to be idle; transient connections come and go
            try:
                cur.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=%s AND pid<>pg_backend_pid()", (SIM_DB,))
                cur.execute(f'DROP DATABASE IF EXISTS "{SIM_DB}"')
                cur.execute(f'CREATE DATABASE "{SIM_DB}" TEMPLATE "{BASE_DB}"')
                return
            except psycopg2.Error:
                time.sleep(1.5)
        raise RuntimeError(f"could not create sandbox from {BASE_DB}")


def db():
    return closing(psycopg2.connect(DB_DSN))


def short_title(body, words=8):
    parts = body.split()
    return " ".join(parts[:words]) + ("…" if len(parts) > words else "")


def load_pool():
    rows = list(csv.DictReader(open(INSERTS)))
    pool = {}
    for r in rows:
        pool.setdefault(r["category"], []).append(r)
    return pool


def build_state():
    """Load the sandbox, build the replica graph and the 3-D layout."""
    with db() as conn, conn.cursor() as cur:
        cur.execute("SELECT id, category, body, embedding::text FROM documents ORDER BY id")
        rows = cur.fetchall()
    ids = [r[0] for r in rows]
    vecs = np.array([[float(x) for x in r[3].strip("[]").split(",")] for r in rows], dtype=np.float32)
    index = TraceHNSW(vecs)
    reducer = umap.UMAP(n_components=3, n_neighbors=15, min_dist=0.1, metric="cosine", random_state=7)
    raw = reducer.fit_transform(index.v)
    center = raw.mean(0)
    scale = 320.0 / np.abs(raw - center).max()
    S.update(ids=ids, category=[r[1] for r in rows], title=[short_title(r[2]) for r in rows],
             xyz=((raw - center) * scale).astype(np.float32), index=index, reducer=reducer, center=center, scale=scale,
             flag=[0] * len(ids), pool=load_pool(), used={}, log=[])


def place(vectors):
    unit = vectors / np.maximum(np.linalg.norm(vectors, axis=1, keepdims=True), 1e-12)
    return ((S["reducer"].transform(unit) - S["center"]) * S["scale"]).astype(np.float32)


def health_payload(h):
    if not h:
        return None
    return {"status": h["status"], "issues": list(h["issues"]), "recall": h["recall_at_k"],
            "ann": h.get("ann_recall"), "sentinel": h.get("sentinel_mismatch_pct")}


def fingerprints():
    with db() as conn, conn.cursor() as cur:
        cur.execute("SELECT id, md5(embedding::text) FROM documents")
        return dict(cur.fetchall())


@app.on_event("startup")
def startup():
    ensure_sandbox()
    build_state()
    S["ready"] = True


@app.get("/")
def home():
    return FileResponse(HERE / "static" / "index.html")


@app.get("/api/state")
def state():
    with LOCK:
        pool_left = {c: len(r) - S["used"].get(c, 0) for c, r in S["pool"].items()}
        return {"ids": S["ids"], "category": S["category"], "title": S["title"], "flag": S["flag"],
                "xyz": [[round(float(a), 2), round(float(b), 2), round(float(c), 2)] for a, b, c in S["xyz"]],
                "pool_left": pool_left, "model": CURRENT_MODEL_VERSION, "other_model": OTHER_EMBEDDER,
                "sandbox": f"{SIM_DB} (copy of {BASE_DB})"}


@app.post("/api/health")
def health():
    with LOCK:
        t = time.time()
        h = run_health_check(note="sim_tick")
        return {"health": health_payload(h), "seconds": round(time.time() - t, 2), "documents": len(S["ids"])}


@app.post("/api/ingest")
def ingest(body: dict = Body(...)):
    count = max(1, min(int(body.get("count", 100)), 600))
    topic = body.get("topic") or None
    faulty = bool(body.get("faulty", False))
    with LOCK:
        t0 = time.time()
        cats = [topic] if topic else list(S["pool"])
        picked = []
        while len(picked) < count and any(S["used"].get(c, 0) < len(S["pool"][c]) for c in cats):
            for c in cats:
                u = S["used"].get(c, 0)
                if u < len(S["pool"][c]) and len(picked) < count:
                    picked.append(S["pool"][c][u]); S["used"][c] = u + 1
        if not picked:
            return JSONResponse({"error": "no unused sample documents left for that topic; reset the sandbox"}, status_code=400)
        texts = [r["body"] for r in picked]
        vecs = np.array(model(OTHER_EMBEDDER if faulty else EMBEDDER).embed(texts), dtype=np.float32)
        with db() as conn, conn.cursor() as cur:
            res = execute_values(cur, """INSERT INTO documents (category, body, embedding, embedding_model_version)
                                         VALUES %s RETURNING id""",
                                 [(r["category"], r["body"], vec_to_pg(v.tolist()), CURRENT_MODEL_VERSION) for r, v in zip(picked, vecs)],
                                 template="(%s, %s, %s::vector, %s)", fetch=True)
            new_ids = [x[0] for x in res]
            conn.commit()
        diffs = S["index"].add_batch(vecs)
        xyz = place(vecs)
        first = len(S["ids"])
        S["ids"] += new_ids; S["category"] += [r["category"] for r in picked]
        S["title"] += [short_title(t) for t in texts]
        S["xyz"] = np.vstack([S["xyz"], xyz]); S["flag"] += [1] * len(new_ids)  # 1 = new
        out = []
        for k, d in enumerate(diffs):
            n = first + k
            item = {"n": n, "id": new_ids[k], "xyz": [round(float(x), 2) for x in xyz[k]], "cat": picked[k]["category"], "title": S["title"][n]}
            if k < MAX_VISIBLE_DIFFS:
                item.update(links=d["links"][:16], touched=d["touched"][:16], pruned=d["pruned"][:12])
            out.append(item)
        touched = len({c for d in diffs for c in d["touched"]})
        pruned = sum(len(d["pruned"]) for d in diffs)
        return {"new": out, "faulty": faulty, "count": len(out), "total": len(S["ids"]), "model": OTHER_EMBEDDER if faulty else CURRENT_MODEL_VERSION,
                "neighbour_lists_updated": touched, "links_pruned": pruned, "seconds": round(time.time() - t0, 2),
                "top_layer": int(S["index"].top)}


@app.post("/api/heal")
def run_heal():
    with LOCK:
        t0 = time.time()
        before_fp = fingerprints()
        hist = heal.heal_until_stable("sim")
        after_fp = fingerprints()
        changed = [i for i, h in after_fp.items() if before_fp.get(i) != h and i in before_fp]
        moved = []
        if changed:
            with db() as conn, conn.cursor() as cur:
                cur.execute("SELECT id, embedding::text FROM documents WHERE id = ANY(%s) ORDER BY id", (changed,))
                got = cur.fetchall()
            pos = {i: n for n, i in enumerate(S["ids"])}
            vecs = np.array([[float(x) for x in r[1].strip("[]").split(",")] for r in got], dtype=np.float32)
            newxyz = place(vecs)
            for (i, _), p in zip(got, newxyz):
                n = pos[i]
                moved.append({"n": n, "id": i, "from": [round(float(x), 2) for x in S["xyz"][n]], "to": [round(float(x), 2) for x in p]})
                S["xyz"][n] = p
            # the replica graph is rebuilt from the repaired vectors
            with db() as conn, conn.cursor() as cur:
                cur.execute("SELECT embedding::text FROM documents ORDER BY id")
                allv = np.array([[float(x) for x in r[0].strip("[]").split(",")] for r in cur.fetchall()], dtype=np.float32)
            S["index"] = TraceHNSW(allv)
        S["flag"] = [0] * len(S["ids"])
        cycles = [{"outcome": o["outcome"], "actions": [str(a) for a in (o.get("actions") or ())],
                   "before": health_payload(o.get("before")), "after": health_payload(o.get("after"))} for o in hist]
        return {"cycles": cycles, "repaired": moved, "seconds": round(time.time() - t0, 2)}


@app.post("/api/reset")
def reset():
    with LOCK:
        S["ready"] = False
        ensure_sandbox(recreate=True)
        build_state()
        S["ready"] = True
        return {"ok": True}
