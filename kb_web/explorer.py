"""The VectorSpace explorer (selfheal_pgvector/explorer/static/index.html), served behind the app's login.

Same page and the same JSON contract as the local FastAPI explorer, adapted to a free 512 MB instance:
- the 3-D map and its HNSW graph are precomputed by scripts/build_map.py (UMAP needs too much memory here); the
  vectors are decoded from the database's own 4-bit codes at start-up, so the drawn search walk is a real search;
- a query (or a hit outside the map) is placed at the similarity-weighted mean of its nearest map points instead of
  UMAP's transform, and the meta endpoint says so;
- results are ranked by the knowledge-base hybrid search, as in the local explorer;
- "Add information" stores passages through the knowledge base (editor role); the hosted copy has no self-healing
  pipeline, so the monitor reports canary and index checks and the healer has nothing to repair.
"""
import logging
import sys
import threading
import time
from pathlib import Path

import numpy as np
from flask import Blueprint, Response, jsonify, request
from markupsafe import escape
from flask_login import current_user, login_required

from . import kbsearch
from .models import Entity, SearchLog, db

HERE = Path(__file__).resolve().parent
UI = HERE.parent / "selfheal_pgvector" / "explorer"
SEED = HERE / "seed"
K_PLACE, EF = 10, 40
SUGGESTIONS = ["find files changed in the last day", "show listening ports", "rsync --delete",
               "change file owner recursively", "mount an iso image", "show free memory"]
# fixed lookups the monitor runs: (query, entity that must be in the top 10)
CANARIES = [("show listening ports", "ss"), ("change file owner recursively", "chown"), ("rsync --delete", "rsync"),
            ("find files by name", "find"), ("list open files", "lsof"), ("show disk usage of a directory", "du")]

bp = Blueprint("explorer", __name__)
log = logging.getLogger(__name__)
_lock = threading.Lock()
S = {}   # the loaded map: index, ids, pos, xyz, category, title, stats


def _readable():
    if str(UI) not in sys.path:
        sys.path.insert(0, str(UI))
    import readable   # noqa: E402
    return readable


def state():
    """Load the map once per process (about 1 s): graph from the pickle, vectors from body_vec."""
    if S:
        return S
    with _lock:
        if S:
            return S
        t0 = time.time()
        sys.path.insert(0, str(UI))
        from hnsw_trace import load   # noqa: E402
        m = np.load(SEED / "map.npz")
        index = load(SEED / "map_hnsw.pkl")
        ids = [int(i) for i in m["ids"]]
        kbsearch.engine()
        common = sys.modules["common"]
        with common.connect() as conn:
            cur = conn.cursor()
            cur.execute("SELECT id, q FROM body_vec WHERE id = ANY(%s)", (ids,))
            q = dict(cur.fetchall())
            missing = [i for i in ids if i not in q]
            if missing:
                raise RuntimeError(f"{len(missing)} map texts are not in the database; rebuild the map for this seed")
            v = common.decode(cur, [q[i] for i in ids])
        index.v = v / np.linalg.norm(v, axis=1, keepdims=True)
        S.update(index=index, ids=ids, pos={i: n for n, i in enumerate(ids)}, xyz=m["xyz"].astype(np.float32),
                 category=[str(c) for c in m["source"]], title=[str(t) for t in m["title"]],
                 preservation=float(m["stats"][0]), recall=float(m["stats"][1]), built_seconds=round(time.time() - t0, 1))
        log.info("explorer map loaded: %d points in %.1f s", len(ids), time.time() - t0)
        return S


def place(vecs):
    """3-D positions for new vectors: similarity-weighted mean of the K_PLACE nearest map points."""
    s = state()
    v = np.asarray(vecs, dtype=np.float32)
    v = v / np.maximum(np.linalg.norm(v, axis=1, keepdims=True), 1e-12)
    sims = v @ s["index"].v[: len(s["xyz"])].T
    near = np.argpartition(-sims, K_PLACE, axis=1)[:, :K_PLACE]
    w = np.maximum(np.take_along_axis(sims, near, 1), 0) ** 4 + 1e-6
    return (s["xyz"][near] * w[..., None]).sum(1) / w.sum(1, keepdims=True)


def texts(body_ids):
    """'entity · section\\nbody' for each text id, as the local explorer stores them."""
    common = sys.modules["common"]
    with common.connect() as conn:
        cur = conn.cursor()
        cur.execute("""SELECT b.id, e.name || ' · ' || r.section || E'\\n' || b.body FROM bodies b
                       JOIN LATERAL (SELECT entity_id, section FROM chunk_refs WHERE body_id = b.id ORDER BY id LIMIT 1) r ON true
                       JOIN entities e ON e.id = r.entity_id WHERE b.id = ANY(%s)""", (list(body_ids),))
        return dict(cur.fetchall())


def build_topic(q, hits):
    """The reference page for the entity the query names, else the one most top hits belong to (as locally)."""
    eng = kbsearch.engine()
    try:
        with eng.connect() as conn:
            cur = conn.cursor()
            eids = eng.entity_ids(cur, q)
            ql = q.strip().lower()
            cur.execute("""SELECT id FROM entities WHERE id = ANY(%s) AND (lower(name) = %s
                           OR %s = ANY(SELECT lower(a) FROM unnest(aliases) a) OR lower(ext_id) = %s)""", (eids or [0], ql, ql, ql))
            named = [r[0] for r in cur.fetchall()]
            eid = named[0] if named else None
            if eid is None and hits:
                cur.execute("SELECT entity_id FROM chunk_refs WHERE id = ANY(%s)", ([h[0] for h in hits[:8]],))
                ents = [r[0] for r in cur.fetchall()]
                top = max(set(ents), key=ents.count)
                if ents.count(top) >= max(3, len(ents) // 2 + 1):
                    eid = top
            if eid is None:
                return None
            cur.execute("SELECT name, type, aliases FROM entities WHERE id = %s", (eid,))
            name, typ, aliases = cur.fetchone()
            cur.execute("SELECT id, section, source, source_version, body FROM chunks WHERE entity_id = %s ORDER BY id LIMIT 600", (eid,))
            return _readable().topic({"name": name, "type": typ, "aliases": aliases}, cur.fetchall())
    except Exception:
        log.exception("topic failed")
        return None


@bp.route("/")
@login_required
def home():
    page = (UI / "static" / "index.html").read_text()
    links = [("Dashboard", "/dashboard"), ("Entities", "/entities"), ("Bookmarks", "/bookmarks")]
    if current_user.can("admin"):
        links += [("Users", "/admin/users"), ("Audit", "/admin/audit")]
    # a round menu button under the page's own "+" button, in the page's style; the links open the app's other pages
    nav = ("<style>#appnav{position:absolute;z-index:31;left:18px;top:136px}"
           "#appnav summary{list-style:none;width:48px;height:48px;border-radius:24px;display:flex;align-items:center;"
           "justify-content:center;cursor:pointer;color:var(--t2);background:rgba(20,20,24,.72);border:1px solid rgba(255,255,255,.09);"
           "backdrop-filter:blur(12px)}#appnav summary::-webkit-details-marker{display:none}#appnav[open] summary,#appnav summary:hover{color:var(--t1)}"
           "#appnav .m{position:absolute;left:58px;top:0;min-width:190px;padding:8px;border-radius:16px;background:rgba(20,20,24,.92);"
           "border:1px solid rgba(255,255,255,.09);backdrop-filter:blur(12px);font-size:13px}"
           "#appnav .m a,#appnav .m button{display:block;width:100%;text-align:left;padding:7px 10px;border-radius:9px;color:var(--t2);"
           "text-decoration:none;background:none;border:0;font:inherit;cursor:pointer}#appnav .m a:hover,#appnav .m button:hover{background:rgba(255,255,255,.06);color:var(--t1)}"
           "#appnav .u{padding:6px 10px 8px;color:var(--t3);font-size:12px;border-bottom:1px solid rgba(255,255,255,.07);margin-bottom:4px}</style>"
           "<details id=\"appnav\"><summary title=\"Menu\" aria-label=\"Menu\"><svg width=\"20\" height=\"20\" viewBox=\"0 0 24 24\" fill=\"none\" "
           "stroke=\"currentColor\" stroke-width=\"2\" stroke-linecap=\"round\"><path d=\"M4 7h16M4 12h16M4 17h16\"/></svg></summary>"
           f"<div class=\"m\"><div class=\"u\">{escape(current_user.username)} · {current_user.role}</div>"
           + "".join(f"<a href=\"{u}\">{t}</a>" for t, u in links)
           + "<form method=\"post\" action=\"/logout\" style=\"margin:0\"><input type=\"hidden\" name=\"csrf_token\" value=\""
           + _csrf() + "\"><button>Log out</button></form></div></details>")
    return Response(page.replace("<body>", "<body>\n" + nav, 1), mimetype="text/html")


def _csrf():
    from flask_wtf.csrf import generate_csrf
    return generate_csrf()


@bp.route("/api/meta")
@login_required
def meta():
    s = state()
    common = sys.modules["common"]
    return {"documents": len(s["ids"]), "dim": 384, "embedder": "minilm", "model": common.MODEL_VERSION,
            "index": {"type": "HNSW", "metric": "cosine", "m": s["index"].m, "ef_construction": s["index"].efc,
                      "ef_search": EF, "top_layer": int(s["index"].top)},
            "projection": {"method": "UMAP (3-D, cosine), precomputed; queries placed by their nearest map points",
                           "neighbour_preservation": round(s["preservation"], 3)},
            "index_recall_vs_exact": round(s["recall"], 3), "built_seconds": s["built_seconds"],
            "suggestions": SUGGESTIONS, "stale": False}


@bp.route("/api/points")
@login_required
def points():
    s = state()
    return {"ids": s["ids"], "xyz": np.round(s["xyz"], 2).tolist(), "category": s["category"], "title": s["title"]}


@bp.route("/api/search")
@login_required
def search():
    q = request.args.get("q", "").strip()
    k = min(max(request.args.get("k", 10, type=int), 1), 20)
    if not q or len(q) > 500:
        return jsonify(error="query must be 1-500 characters"), 400
    s = state()
    common = sys.modules["common"]
    t_all = time.perf_counter()
    qv = np.asarray(common.embed([q])[0], dtype=np.float32)
    with _lock:
        index, ids, category, title = s["index"], s["ids"], s["category"], s["title"]
        t0 = time.perf_counter()
        top, trace = index.search(qv, k=k, ef=EF)
        search_ms = (time.perf_counter() - t0) * 1e3
        sims = index.v @ qv
    part = np.argpartition(-sims, min(k, len(sims) - 1))[:k]
    exact = {int(i) for i in part}
    try:
        hits = kbsearch.run(q, limit=k)
    except Exception:
        log.exception("hybrid search failed")
        hits = []
    readable = _readable()
    body_of = {}
    if hits:
        with common.connect() as conn:
            cur = conn.cursor()
            cur.execute("SELECT id, body_id FROM chunk_refs WHERE id = ANY(%s)", ([h[0] for h in hits],))
            body_of = dict(cur.fetchall())
    # texts for the graph's own results and near misses (the map stores no text)
    want = {ids[n] for n, _ in top + trace["near_miss"]}
    stored = texts(want) if want else {}

    def nodes(lst):
        return [{"n": int(n), "id": ids[n], "dist": round(d, 4), "score": round(1 - d, 4), "title": title[n],
                 "category": category[n], "text": stored.get(ids[n], title[n])[:4000]} for n, d in lst]

    results, hybrid = nodes(top), False
    if hits:
        off = [body_of[h[0]] for h in hits if h[0] in body_of and body_of[h[0]] not in s["pos"]]
        extra = {}
        if off:
            with common.connect() as conn:
                cur = conn.cursor()
                cur.execute("SELECT id, q FROM body_vec WHERE id = ANY(%s)", (off,))
                got = cur.fetchall()
                if got:
                    vecs = common.decode(cur, [g[1] for g in got])
                    for (i, _), v, p in zip(got, vecs, place(vecs)):
                        extra[i] = (v / max(float(np.linalg.norm(v)), 1e-12), p)
        top_rrf = hits[0][7]
        results = []
        for cid, ent, sec, src, ver, body, also, score, via in hits:
            text, b = f"{ent} · {sec}\n{body}", body_of.get(cid)
            if b in s["pos"]:
                n = s["pos"][b]
                r = {"n": n, "dist": round(float(1 - index.v[n] @ qv), 4), "title": title[n], "category": category[n]}
            elif b in extra:
                v, p = extra[b]
                r = {"n": -1, "dist": round(float(1 - v @ qv), 4), "title": f"{ent} · {sec}"[:90], "category": src,
                     "xyz": [round(float(x), 2) for x in p]}
            else:
                continue
            r.update(id=cid, score=round(score / top_rrf, 4), text=text, via=via)
            results.append(r)
        hybrid = bool(results)
    near = nodes(trace["near_miss"])
    for r in results + near:
        bs = readable.blocks(r["text"].split("\n", 1)[1] if "\n" in r["text"] else r["text"], r["category"])
        r["blocks"], r["snippet"] = bs, readable.snippet(bs)
    results = [r for r in results if r["blocks"]] or results
    try:
        db.session.add(SearchLog(user_id=current_user.id, query=q[:300], n_results=len(results),
                                 latency_ms=int((time.perf_counter() - t_all) * 1000)))
        db.session.commit()
    except Exception:
        db.session.rollback()
        log.exception("search log write failed")
    return {"query": q, "k": k, "search_ms": round(search_ms, 2), "pgvector_ms": None, "hybrid": hybrid,
            "topic": build_topic(q, hits), "results": results, "near_miss": near,
            "paths": {str(n): p for n, p in trace["paths"].items()}, "steps": trace["steps"],
            "visited_layer0": trace["visited_layer0"], "documents": len(ids),
            "query_xyz": [round(float(x), 2) for x in place(qv[None])[0]],
            "check": {"own_vs_exact": len({n for n, _ in top} & exact), "own_vs_pgvector": None, "pgvector_vs_exact": None, "k": k},
            "stale": False}


def health_payload():
    """Canary lookups through the full hybrid search, plus the in-process graph's recall against exact search."""
    s = state()
    hits = 0
    for q, want in CANARIES:
        try:
            hits += any(h[1] == want for h in kbsearch.run(q, limit=10))
        except Exception:
            log.exception("canary %r failed", q)
    rng = np.random.default_rng()
    with _lock:
        index = s["index"]
        pick = rng.choice(index.n, min(50, index.n), replace=False)
        exact = np.argsort(-(index.v[pick] @ index.v.T), axis=1)[:, :10]
        ann = float(np.mean([len({e for e, _ in index.search(index.v[p], 10, EF)[0]} & set(exact[i])) / 10
                             for i, p in enumerate(pick)]))
    recall = hits / len(CANARIES)
    issues = (["CANARY_MISS"] if recall < 1 else []) + (["INDEX_RECALL_LOW"] if ann < 0.95 else [])
    return {"status": "HEALTHY" if not issues else "DEGRADED", "issues": issues, "recall": round(recall, 3),
            "ann": round(ann, 3), "sentinel": 0.0}


def _json_post_ok():
    return request.is_json   # cross-site forms cannot send application/json without a CORS preflight


@bp.route("/api/info")
@login_required
def info():
    s = state()
    common = sys.modules["common"]
    return {"faults_enabled": False, "model": common.MODEL_VERSION, "other_model": "", "pool_left": {},
            "categories": sorted(set(s["category"])), "documents": len(s["ids"])}


@bp.route("/api/health", methods=["POST"])
@login_required
def health():
    if not _json_post_ok():
        return jsonify(error="expected JSON"), 400
    t = time.time()
    return {"health": health_payload(), "seconds": round(time.time() - t, 2), "documents": len(state()["ids"])}


@bp.route("/api/ingest", methods=["POST"])
@login_required
def ingest():
    """Store pasted texts as passages of the entity named in 'category' (created if new), then add them to the map."""
    if not _json_post_ok():
        return jsonify(error="expected JSON"), 400
    if not current_user.can("editor"):
        return jsonify(error="adding information needs the editor role; ask an admin"), 403
    body = request.get_json(silent=True) or {}
    if body.get("faulty"):
        return jsonify(error="fault injection is not available in the hosted copy"), 403
    texts_in = [t.strip() for t in (body.get("texts") or []) if isinstance(t, str) and len(t.strip()) >= 30][:20]
    name = str(body.get("category") or "").strip()[:100]
    if not texts_in or not name:
        return jsonify(error="paste at least one text of 30+ characters and name what it is about in the category box"), 400
    t0 = time.time()
    e = db.session.query(Entity).filter(db.func.lower(Entity.name) == name.lower()).order_by(Entity.id).first()
    if e is None:
        e = Entity(name=name, type="other", domain=[], aliases=[], ext_id="", facts={})
        db.session.add(e)
        db.session.commit()
    common = sys.modules["common"]
    added = []
    for t in texts_in:
        new, bid = kbsearch.add_passage(e.id, e.name, "notes", t)
        if new:
            added.append((bid, t))
    s = state()
    out, diffs = [], []
    if added:
        with common.connect() as conn:
            cur = conn.cursor()
            cur.execute("SELECT id, q FROM body_vec WHERE id = ANY(%s)", ([b for b, _ in added],))
            q = dict(cur.fetchall())
            vecs = common.decode(cur, [q[b] for b, _ in added])
        xyz = place(vecs)
        with _lock:
            first = len(s["ids"])
            diffs = s["index"].add_batch(vecs)
            for (b, t) in added:
                s["pos"][b] = len(s["ids"])
                s["ids"].append(b); s["category"].append("user"); s["title"].append(f"{e.name} · notes"[:90])
            s["xyz"] = np.vstack([s["xyz"], xyz.astype(np.float32)])
        for k, d in enumerate(diffs):
            out.append({"n": first + k, "id": added[k][0], "xyz": [round(float(x), 2) for x in xyz[k]], "cat": "user",
                        "title": s["title"][first + k], "links": d["links"][:16], "touched": d["touched"][:16],
                        "pruned": d["pruned"][:12]})
    return {"new": out, "faulty": False, "count": len(out), "total": len(s["ids"]), "model": common.MODEL_VERSION,
            "neighbour_lists_updated": len({c for d in diffs for c in d["touched"]}),
            "links_pruned": sum(len(d["pruned"]) for d in diffs), "seconds": round(time.time() - t0, 2)}


@bp.route("/api/heal", methods=["POST"])
@login_required
def heal():
    """The hosted copy keeps no float vectors to re-embed, so there is nothing to repair: report the check only."""
    if not _json_post_ok():
        return jsonify(error="expected JSON"), 400
    t = time.time()
    h = health_payload()
    return {"cycles": [{"outcome": "healthy" if h["status"] == "HEALTHY" else "no_action", "actions": [],
                        "before": h, "after": h}], "repaired": [], "seconds": round(time.time() - t, 2)}
