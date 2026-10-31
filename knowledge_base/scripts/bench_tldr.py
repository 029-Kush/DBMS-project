"""External retrieval benchmark built from tldr-pages (written by tldr contributors, not tuned against this KB).

Query = a tldr example description ("Show all TCP sockets listening on the local 8080 port"),
relevant = any chunk of an entity named like the page's command ("ss"). tldr chunks are excluded from
the corpus while benchmarking, so the answer cannot be the query's own source text.

  bench_tldr.py build        sample queries, split dev/test by command hash (frozen to $KB_HDD/bench/queries.json)
  bench_tldr.py cache [split] run every retriever once per query, store the ranked ids (runs_<split>.json)
  bench_tldr.py score [split] evaluate fusion configs offline from the cache
"""
import hashlib, json, random, re, sys, time
from pathlib import Path
from common import *
import search as S

OUT = HDD / "bench"; OUT.mkdir(parents=True, exist_ok=True)
TLDR = HDD / "sources" / "tldr-pages" / "pages"
PER_SPLIT, PER_CMD, SEED = 500, 2, 13
EXCLUDE = ("tldr",)

def split_of(cmd):
    return "dev" if int(hashlib.md5(cmd.encode()).hexdigest(), 16) % 2 == 0 else "test"

def build():
    cur = connect().cursor()
    cur.execute("SELECT DISTINCT lower(name) FROM entities WHERE type IN ('manpage','tool','concept')")
    known = {r[0] for r in cur.fetchall()}
    pages = sorted(p for d in ("common", "linux") for p in (TLDR / d).glob("*.md"))
    by_split, total_cmds, cmds_known = {"dev": [], "test": []}, 0, 0
    for p in pages:
        cmd = p.stem.lower(); total_cmds += 1
        if cmd not in known: continue
        cmds_known += 1
        descs = [l[2:].strip().rstrip(":") for l in p.read_text(errors="replace").splitlines() if l.startswith("- ")]
        for d in descs:
            by_split[split_of(cmd)].append({"cmd": cmd, "q": d})
    rng = random.Random(SEED); out = {}
    for sp, qs in by_split.items():
        rng.shuffle(qs); seen, pick = {}, []
        for x in qs:
            if seen.get(x["cmd"], 0) < PER_CMD:
                seen[x["cmd"]] = seen.get(x["cmd"], 0) + 1; pick.append(x)
            if len(pick) == PER_SPLIT: break
        out[sp] = pick
    meta = {"tldr_commands": total_cmds, "with_kb_entity": cmds_known, "pool_dev": len(by_split["dev"]), "pool_test": len(by_split["test"])}
    (OUT / "queries.json").write_text(json.dumps({"meta": meta, **out}, indent=1))
    print(meta, {k: len(v) for k, v in out.items()})

def cache(split):
    Q = json.loads((OUT / "queries.json").read_text())[split]
    path = OUT / f"runs_{split}.json"
    done = json.loads(path.read_text()) if path.exists() else {}
    conn = connect(); cur = conn.cursor()
    S.TRGM_ALWAYS = True   # cache the fuzzy run for every query; gating is decided offline from it
    for i, x in enumerate(Q):
        if x["q"] in done: continue
        t = time.time()
        eids = S.entity_ids(cur, x["q"])
        runs = S.retrieve(cur, x["q"], eids, 40, EXCLUDE)
        rt = time.time() - t
        cur.execute("BEGIN")
        runs["vec_wide"] = S.vector_ids(cur, embed([x["q"]])[0], 100)   # ids are bodies now; tldr texts are dropped at fan-out
        cur.execute("COMMIT")
        done[x["q"]] = {"cmd": x["cmd"], "eids": eids, "flags": extract_flags(x["q"]), "runs": runs, "sec": round(rt, 3)}
        if i % 25 == 0:
            path.write_text(json.dumps(done)); print(f"{split} {i}/{len(Q)} {rt:.2f}s", flush=True)
    path.write_text(json.dumps(done)); print("cached", len(done))

def apropos(q):
    """Baseline: man -k over the same man pages. Pages matching more query words rank first."""
    import subprocess
    words = [w.lower() for w in re.findall(r"[A-Za-z][A-Za-z0-9_-]{2,}", q) if w.lower() not in S.STOP]
    if not words: return []
    out = subprocess.run(["apropos", "-l", *words], capture_output=True, text=True).stdout.splitlines()
    hits = {}
    for line in out:
        m = re.match(r"(\S+) \((\w+)\)\s+- (.*)", line)
        if not m: continue
        text = (m[1] + " " + m[3]).lower()
        hits[m[1].lower()] = max(hits.get(m[1].lower(), 0), sum(w in text for w in words))
    return sorted(hits, key=lambda n: -hits[n])

BOILER = re.compile(r"(^|: )(authors?|copyright|reporting bugs|colophon|see also|history|revision history|license|availability|bugs)$")

def entity_rank(chunk_ids, meta):
    """Collapse a chunk ranking into a ranking of entity names (first occurrence wins)."""
    out = []
    for c in chunk_ids:
        n = meta[c][0]
        if n not in out: out.append(n)
    return out

def metrics(ranked, cmds):
    h1 = h5 = h10 = mrr = 0
    for r, cmd in zip(ranked, cmds):
        k = r.index(cmd) + 1 if cmd in r else 0
        h1 += k == 1; h5 += 0 < k <= 5; h10 += 0 < k <= 10; mrr += 1 / k if 0 < k <= 10 else 0
    n = len(cmds)
    return {"hit@1": h1 / n, "hit@5": h5 / n, "hit@10": h10 / n, "mrr@10": mrr / n}

def load(split):
    C = json.loads((OUT / f"runs_{split}.json").read_text())
    ids = {c for v in C.values() for r in v["runs"].values() for c in r}
    cur = connect().cursor()
    cur.execute("""SELECT c.id, lower(e.name), c.section, c.source, e.name || ' · ' || c.section || E'\n' || left(c.body, 1500)
                   FROM chunks c JOIN entities e ON e.id = c.entity_id WHERE c.id = ANY(%s)""", (list(ids),))
    meta = {r[0]: r[1:] for r in cur.fetchall()}
    return C, meta, cur

def fuse(runs, weights, k=S.K):
    sc = {}
    for name, ids in runs.items():
        w = weights.get(name, 0)
        for i, c in enumerate(ids):
            if w: sc[c] = sc.get(c, 0) + w / (k + i + 1)
    return sorted(sc, key=sc.get, reverse=True), sc

def gated(v, mode):
    """Which runs production would have: trgm only under the gate."""
    runs = dict(v["runs"])
    if mode == "prod":
        if not (len(runs["fts"]) < S.TRGM_MIN_FTS or v["eids"]): runs.pop("trgm", None)
    elif mode == "no_trgm":
        runs.pop("trgm", None)
    return runs

_ce = None
def rerank(query, cands, meta, model):
    global _ce
    if _ce is None or _ce[0] != model:
        from fastembed.rerank.cross_encoder import TextCrossEncoder
        _ce = (model, TextCrossEncoder(model, cache_dir=str(HDD / "models")))
    scores = list(_ce[1].rerank(query, [meta[c][3] for c in cands]))
    return [c for _, c in sorted(zip(scores, cands), key=lambda t: -t[0])], dict(zip(cands, scores))

def score(split, rr_models=("Xenova/ms-marco-MiniLM-L-6-v2",), depth=30):
    C, meta, cur = load(split)
    qs = list(C); cmds = [C[q]["cmd"] for q in qs]
    res = {}
    base_w = {"flag": 3.0, "fts": 1, "fts_any": 0.5, "trgm": 1, "vec": 1}
    def run(name, fn):
        t = time.time(); ranked = [entity_rank(fn(q), meta) for q in qs]
        res[name] = {**metrics(ranked, cmds), "ms/q": round((time.time() - t) / len(qs) * 1e3, 1)}
        print(f"{name:34s} " + "  ".join(f"{k} {v:.3f}" if k != "ms/q" else f"{k} {v}" for k, v in res[name].items()), flush=True)
    apr = {q: apropos(q) for q in qs}
    res["apropos"] = metrics([apr[q] for q in qs], cmds)
    print(f"{'apropos (man -k)':34s} " + "  ".join(f"{k} {v:.3f}" for k, v in res["apropos"].items()), flush=True)
    for r in ("fts", "fts_any", "trgm", "vec", "vec_wide"):
        run(f"only {r}", lambda q, r=r: C[q]["runs"].get(r, []))
    run("hybrid (prod gate)", lambda q: fuse(gated(C[q], "prod"), base_w)[0])
    run("hybrid, trgm always", lambda q: fuse(gated(C[q], "all"), base_w)[0])
    run("hybrid, no trgm", lambda q: fuse(gated(C[q], "no_trgm"), base_w)[0])
    nob = lambda lst: [c for c in lst if not BOILER.search(meta[c][1])]
    run("hybrid no trgm, -boilerplate", lambda q: nob(fuse(gated(C[q], "no_trgm"), base_w)[0]))
    wide = dict(base_w, vec=0, vec_wide=1)
    run("hybrid no trgm, -boiler, vec_wide", lambda q: nob(fuse(gated(C[q], "no_trgm"), wide)[0]))
    for m in rr_models:
        run(f"+rerank {m.split('/')[-1]} top{depth}", lambda q: rerank(q, nob(fuse(gated(C[q], "no_trgm"), base_w)[0])[:depth], meta, m)[0])
    (OUT / f"score_{split}.json").write_text(json.dumps(res, indent=1))

def final(split):
    """Baselines from the cache (as the system was before tuning) vs the live search.py, on one split."""
    C, meta, cur = load(split); qs = list(C); cmds = [C[q]["cmd"] for q in qs]
    old_w = {"flag": 3.0, "fts": 1, "fts_any": 0.5, "trgm": 1, "vec": 1}
    rows = {"apropos (man -k)": [apropos(q) for q in qs],
            "full-text only": [entity_rank(C[q]["runs"]["fts"], meta) for q in qs],
            "vector only (ef 40)": [entity_rank(C[q]["runs"]["vec"], meta) for q in qs],
            "hybrid before (cache, no boosts)": [entity_rank(fuse(gated(C[q], "prod"), old_w)[0], meta) for q in qs]}
    for rr in (False, True):
        t = time.time(); out = []
        for q in qs:
            out.append([r[0][1].lower() for r in S.search(q, limit=30, exclude=EXCLUDE, rerank=rr)])
        rows[f"search.py now{' + rerank' if rr else ''}"] = [list(dict.fromkeys(o)) for o in out]
        print(f"  live{' rerank' if rr else ''}: {(time.time() - t) / len(qs) * 1e3:.0f} ms/q", flush=True)
    res = {name: metrics(r, cmds) for name, r in rows.items()}
    for name, m in res.items():
        print(f"{name:34s} " + "  ".join(f"{k} {v:.3f}" for k, v in m.items()))
    (OUT / f"final_{split}.json").write_text(json.dumps({"n": len(qs), **res}, indent=1))

if __name__ == "__main__":
    if sys.argv[1] == "final": final(sys.argv[2]); sys.exit()
    cmd = sys.argv[1]
    if cmd == "build": build()
    elif cmd == "cache": cache(sys.argv[2] if len(sys.argv) > 2 else "dev")
    elif cmd == "score": score(sys.argv[2] if len(sys.argv) > 2 else "dev")
