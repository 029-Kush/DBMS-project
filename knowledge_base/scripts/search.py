"""Hybrid search: exact flag/ID match + full-text + trigram + vector, fused with RRF.
Prints retrieved text VERBATIM with source and version; never paraphrases."""
import re, sys
from common import *

K = 60  # RRF constant
TRGM_ALWAYS, TRGM_MIN_FTS = False, 10   # TRGM_ALWAYS=False: skip fuzzy match when keywords already returned >= TRGM_MIN_FTS hits
# Fuzzy body matching costs O(chunks): measured median 0.1 s at 20k chunks, 1.0 s (p95 5 s) at 200k. So it is scaled back as the KB grows:
# <= TRGM_BODY_MAX: threshold TRGM_THR; <= TRGM_BODY_OFF: stricter threshold (p95 0.3 s at 200k); above: skipped.
TRGM_BODY_MAX, TRGM_BODY_OFF, TRGM_THR_BIG = 50000, 0, 0.8
# TRGM_BODY_OFF 500000 -> 0 (2026-10-28): the body trigram lane was ~all of warm p95 (452 of ~480 ms) and 2.2 s cold, for
# dev MRR -0.002 [-0.010, +0.005] and the same eval_ids / eval_lookup / eval_typos scores (the entity-name typo rescue
# covers typos). Off: warm p50/p95 145/483 -> 57/80 ms. Set it back to 500000 to restore the lane below 500k texts.
NAME_FUZZY = 0.45
FTS_ANY_ROWS = 20000  # most chunks the OR-keyword run may have to rank; refresh term_df after an ingest (scripts/refresh_stats.sh)
TRGM_THR = 0.6  # pg_trgm word_similarity threshold; higher = fewer fuzzy candidates = faster (cost grows with corpus size)
SECTION_WORDS = {r"mitigat|defen[cs]e|prevent|harden": ["mitigations"], r"detect|hunt|log source|monitor": ["detection"],
                 r"example|procedure|used by|who uses": ["procedure examples", "examples", "observed examples"],
                 r"consequence|impact": ["consequences"], r"platform": ["platforms"]}
STOP = set("how what when where which does that this with from into have your about than then them they will would could should there their being these those without while also like make need want just only over such some more most other very can you the and for are but not".split())
# a literal flag/ID hit is the strongest evidence; on the tldr benchmark (scripts/bench_tldr.py, dev split) weighting the
# vector run 2x raised hit@1 from 0.249 to 0.284 for natural-language questions without hurting the ID/flag evals.
# fts_any 0 = that lane is skipped (see retrieve); set it > 0 to bring it back
WEIGHT = {"flag": 3.0, "fts_any": 0.0, "vec": 2.0, "name": 2.0}
# vector run: VEC_CAND Hamming candidates re-sorted to VEC_POOL. No ANN index: an exact scan of 180k 48-byte codes is ~17 ms
# warm and one sequential read cold, where the 96 MB 1-bit HNSW it replaced was approximate and read randomly from the HDD.
VEC_POOL, VEC_CAND = 100, 800
FTS_SKIP_FRAC = 0.01   # see retrieve(): skip the keyword AND query when its rarest word is in > 1% of texts
# man-page sections that never answer a question unless asked for by name ("umad_open_port(3): author" for "list open ports")
BOILER = re.compile(r"(^|: )(authors?|copyright|reporting bugs|colophon|see also|history|revision history|license|availability|bugs)$")
NAME_LANE_TYPES, NAME_LANE_SIM = ("weakness", "technique", "software", "group", "mitigation", "campaign", "tactic"), 0.9
SECURITY_SOURCES, SEC_SLOT_MIN = {"attack", "cwe", "kev"}, 0.5
RERANK_MODEL, RERANK_DEPTH, RERANK_CHARS, RERANK_W = "Xenova/ms-marco-MiniLM-L-6-v2", 20, 400, 2.0

def entity_ids(cur, query):
    """Resolve entities named in the query. IDs (T1055.012, CWE-79, CVE-..) match any type;
    plain names only match locally documented tools (names like 'at' or 'net' would hijack queries)."""
    toks = {t.lower() for t in re.findall(r"[A-Za-z0-9][\w.+-]*", query)}
    # concept names (seccomp, mount, syscalls...) are common words: they only name an entity in a short query,
    # otherwise "how do I block syscalls in a container" would be locked to the 'syscalls' man page
    kinds = ['tool', 'concept'] if len(toks) <= 3 else ['tool']
    # a UNION of three index lookups (sql/004): the same condition written as one OR made Postgres scan the whole table
    cur.execute("""SELECT id FROM entities WHERE type = ANY(%s) AND lower(name) = ANY(%s)
                   UNION SELECT id FROM entities WHERE type = ANY(%s) AND aliases && %s
                   UNION SELECT id FROM entities WHERE ext_id <> '' AND type <> 'manpage' AND upper(ext_id) = ANY(%s)""",
                (kinds, list(toks), kinds, list(toks), [t.upper() for t in toks]))
    # (a man page's ext_id is its section number: "pages 1 through 3" matched all 13,855 pages of sections 1 and 3)
    found = [r[0] for r in cur.fetchall()]
    if not found and 1 <= len(toks) <= 2 and not ID_RE.search(query):
        # typo rescue on the small entities table ("namspaces" -> namespaces); cheap, unlike fuzzy matching every chunk body
        cur.execute("""SELECT id FROM entities WHERE type = ANY(%s) AND similarity(lower(name), %s) >= %s
                       ORDER BY similarity(lower(name), %s) DESC LIMIT 1""", (kinds, query.strip().lower(), NAME_FUZZY, query.strip().lower()))
        found = [r[0] for r in cur.fetchall()]
    return found

def retrieve(cur, query, eids, pool=40, exclude=()):
    """Run each retriever once; returns {retriever: [body ids, best first]}. Retrievers rank distinct texts (bodies), so a
    page duplicated under 441 alias names counts once. `exclude` (sources) is applied when bodies are mapped back to chunks."""
    ef, ep = "", []
    if eids:   # resolve the entity's texts first: as a subquery the planner fuzzy-matched the whole corpus, then filtered
        cur.execute("SELECT array_agg(DISTINCT body_id) FROM chunk_refs WHERE entity_id = ANY(%s)", (eids,))
        ef, ep = "AND b.id = ANY(%s)", [cur.fetchone()[0] or []]
    flags = extract_flags(query)
    runs = {}
    # the in-RAM vector lane needs no database, so it runs beside the SQL lanes (latency = the slower one, not the sum)
    qv = embed([query])[0]; vec_job = None
    if VEC_IN_RAM:
        _, ids, bits, q4 = load_codes(cur); qa = np.asarray(qv, dtype=np.float32); rows = np.arange(len(ids))
        if ep:
            want = np.asarray(ep[0], dtype=np.int64); pos = np.searchsorted(ids, want)
            rows = np.unique(pos[(pos < len(ids)) & (ids[np.minimum(pos, len(ids) - 1)] == want)])
        if len(rows): vec_job = _lane_pool().submit(_vec_scan, ids, bits, q4, quant(cur), qa, max(pool, VEC_POOL), rows, bool(ep))

    def def_re(f):   # a line that begins with the flag = where the flag is defined
        return r"(^|\n)[[:space:]]*" + re.sub(r"([^A-Za-z0-9])", r"\\\1", f) + r"([^A-Za-z0-9_-]|$)"
    if flags:
        cur.execute(f"""SELECT b.id FROM bodies b WHERE b.flags && %s {ef}
                        ORDER BY (b.body ~ ANY(%s)) DESC, array_length(b.flags,1), b.trust LIMIT %s""",
                    [flags] + ep + [[def_re(f) for f in flags], pool])
        runs["flag"] = [r[0] for r in cur.fetchall()]

    # drop leading dashes: websearch_to_tsquery treats "-x" as NOT x
    words = " ".join(re.findall(r"[\w.]+", query))
    # Skip the AND query when even its rarest word is in > FTS_SKIP_FRAC of all texts: it cannot be selective, and ranking
    # every match read the heap for seconds at 10M texts (82k-512k matches). Tested 2026-10-28 on dev (180k texts):
    # MRR -0.001 [-0.009, +0.006] at 1% (76/482 queries skip it); 0.5% -0.008, 0.2% -0.015. Scoped queries always run it.
    # Only when every word has a term_df row: a word newer than the last stats refresh may be the one precise match.
    qw = sorted({w.lower() for w in words.split()})
    cur.execute("SELECT min(ndoc), count(*), (SELECT reltuples FROM pg_class WHERE relname = 'bodies') FROM term_df WHERE word = ANY(%s)", (qw,))
    min_df, n_known, n_bodies = cur.fetchone()
    if eids or not min_df or n_known < len(qw) or not n_bodies or n_bodies <= 0 or min_df <= FTS_SKIP_FRAC * n_bodies:
        cur.execute(f"""SELECT b.id FROM bodies b WHERE b.tsv @@ websearch_to_tsquery('simple', %s) {ef}
                        ORDER BY ts_rank_cd(b.tsv, websearch_to_tsquery('simple', %s)) DESC LIMIT %s""",
                    [words] + ep + [words, pool])
        runs["fts"] = [r[0] for r in cur.fetchall()]
    else: runs["fts"] = []

    # natural-language questions: the AND query above finds nothing, so also rank texts matching ANY content word.
    # Off since 2026-10-26 (WEIGHT fts_any 0): on tldr dev, dropping it gave MRR +0.023 [+0.006, +0.040] and hit@1 +0.039,
    # hit@10 flat, evals unchanged, p95 249 -> 150 ms. The vector lane covers these questions better than OR-ed words.
    terms = [w.lower() for w in re.findall(r"[A-Za-z][A-Za-z0-9_]{3,}", query) if w.lower() not in STOP]
    any_run = WEIGHT["fts_any"] > 0 and len(runs["fts"]) < 10 and len(terms) >= 2
    if any_run:
        # OR-ing common words ("list", "file") matches tens of thousands of texts, and ranking them all read the HDD for
        # 20+ s. Keep the rarest terms (IDF order, from term_df) until about FTS_ANY_ROWS texts can match.
        cur.execute("SELECT word, ndoc FROM term_df WHERE word = ANY(%s)", (terms,))
        df = dict(cur.fetchall()); keep, tot = [], 0
        for t in sorted(set(terms), key=lambda t: df.get(t, 0)):
            if df.get(t, 0) == 0: continue
            if keep and tot + df[t] > FTS_ANY_ROWS: break
            keep.append(t); tot += df[t]
        terms = keep
    if any_run and terms:
        cur.execute(f"""SELECT b.id FROM bodies b WHERE b.tsv @@ to_tsquery('simple', %s) {ef}
                        ORDER BY ts_rank_cd(b.tsv, to_tsquery('simple', %s)) DESC LIMIT %s""",
                    [" | ".join(terms)] + ep + [" | ".join(terms), pool])
        runs["fts_any"] = [r[0] for r in cur.fetchall()]

    # fuzzy matching is the one retriever whose cost grows with the corpus (~100 ms at 20k texts, ~5x that at 100k),
    # so it only runs when exact keywords found little: typos, partial IDs, odd spellings
    cur.execute("SELECT reltuples::bigint FROM pg_class WHERE relname = 'bodies'")
    n_chunks = cur.fetchone()[0]
    if TRGM_ALWAYS or (0 <= n_chunks <= TRGM_BODY_OFF and (len(runs["fts"]) < TRGM_MIN_FTS or eids)):
        cur.execute("SELECT set_config('pg_trgm.word_similarity_threshold', %s, false)", (str(TRGM_THR if n_chunks <= TRGM_BODY_MAX else TRGM_THR_BIG),))   # a named entity scopes the scan to its own texts
        # scoped to an entity, score its few texts directly: with the indexable `<%` the planner walked the trigram index
        # over the whole corpus first (8k rechecks, ~1 s for "strace") and filtered to the entity afterwards
        match = "word_similarity(%s, b.body) >= current_setting('pg_trgm.word_similarity_threshold')::real" if eids else "%s <%% b.body"
        cur.execute(f"""SELECT b.id FROM bodies b WHERE {match} {ef}
                        ORDER BY word_similarity(%s, b.body) DESC LIMIT %s""", [query] + ep + [query, pool])
        runs["trgm"] = [r[0] for r in cur.fetchall()]

    # security names inside a question ("heap use after free", "zip slip path traversal"): the entity's overview directly.
    # Multi-word names/aliases only, matched on the small entities table, so this costs milliseconds, not a corpus scan.
    if not eids and len(query) >= 8:
        cur.execute("""SELECT c.body_id FROM (
                          SELECT e.id, max(word_similarity(lower(n), lower(%s)) * least(length(btrim(regexp_replace(n, '[^A-Za-z0-9 ]', '', 'g'))), 30)
                                               - length(n) / 1000.0) AS s   -- longer real words win; on ties the plainer name
                          FROM entities e, unnest(e.name || e.aliases) n
                          WHERE e.type = ANY(%s) AND n ~ ' ' AND length(n) >= 6 AND word_similarity(lower(n), lower(%s)) >= %s
                          GROUP BY e.id ORDER BY s DESC LIMIT 5) m
                        JOIN chunk_refs c ON c.entity_id = m.id AND c.section = 'overview'
                        ORDER BY m.s DESC""", [query, list(NAME_LANE_TYPES), query, NAME_LANE_SIM])
        runs["name"] = [r[0] for r in cur.fetchall()]
    runs["vec"] = vec_job.result() if vec_job else ([] if VEC_IN_RAM else vector_ids(cur, qv, max(pool, VEC_POOL), ef, ep))
    return runs

VEC_IN_RAM = True   # False: scan the code table in SQL on every query (the 2026-10-27 path, kept for comparison)
_codes = None       # (max id, ids, 1-bit codes as n x 6 uint64, 4-bit codes as n x 192 uint8), loaded once per process
VEC_THREADS, VEC_THREADS_MIN = 8, 1_000_000   # threaded scan above 1M texts: 10M in 82 ms with 8 threads vs 296 ms with one
_tp = _lp = None
def _lane_pool():   # one background lane per query; separate from _pool(), whose threads the lane itself waits on
    global _lp
    if _lp is None:
        from concurrent.futures import ThreadPoolExecutor
        _lp = ThreadPoolExecutor(1)
    return _lp
def _pool():
    global _tp
    if _tp is None:
        from concurrent.futures import ThreadPoolExecutor
        _tp = ThreadPoolExecutor(VEC_THREADS)
    return _tp

def load_codes(cur):
    """The active model's codes, read once into process memory (43 MB for 180k texts; 2.4 GB at 10M) and reloaded only
    when an ingest added texts (max id changed). Cold on the USB disk, the per-query SQL scan read the whole table (~1.3 s)."""
    global _codes
    cur.execute(f"SELECT max(id) FROM {VEC_TABLE}"); m = cur.fetchone()[0]
    if _codes is None or _codes[0] != m:
        cur.execute(f"SELECT id, b::text, q FROM {VEC_TABLE} ORDER BY id"); rows = cur.fetchall()
        bits = np.frombuffer("".join(r[1] for r in rows).encode(), dtype=np.uint8).reshape(len(rows), 384) - 48
        _codes = (m, np.array([r[0] for r in rows], dtype=np.int64), np.packbits(bits, axis=1).view(np.uint64),
                  np.frombuffer(b"".join(r[2] for r in rows), dtype=np.uint8).reshape(len(rows), 384 // 2))   # explicit width: works with 0 rows
    return _codes

def vector_ids(cur, qv, n, ef="", ep=()):
    """Nearest texts by cosine without stored floats: exact Hamming top-VEC_CAND over the 1-bit codes (no index), then
    re-sorted by the float query against the 4-bit codes (see common.encode). Every text tied at the cutoff distance is
    kept: with a plain LIMIT, Postgres' parallel scan picked an arbitrary subset of the ties (one query had 234 texts tied
    for the last 85 slots), so results changed from run to run. Re-sort ties break by id. ep = [body ids] scopes the scan."""
    q = np.asarray(qv, dtype=np.float32)
    if not VEC_IN_RAM:
        cur.execute(f"""SELECT b.id, b.q FROM {VEC_TABLE} b WHERE true {ef} ORDER BY b.b <~> %s::bit(384)
                        FETCH FIRST %s ROWS WITH TIES""", list(ep) + [sign_bits(q), VEC_CAND])
        rows = sorted(cur.fetchall())
        if not rows: return []
        s = decode(cur, [r[1] for r in rows]) @ q
        return [rows[i][0] for i in np.argsort(-s, kind="stable")[:n]]
    _, ids, bits, q4 = load_codes(cur)
    rows = np.arange(len(ids))
    if ep:   # scoped to a named entity's texts
        want = np.asarray(ep[0], dtype=np.int64); pos = np.searchsorted(ids, want)
        rows = np.unique(pos[(pos < len(ids)) & (ids[np.minimum(pos, len(ids) - 1)] == want)])
        if not len(rows): return []
    return _vec_scan(ids, bits, q4, quant(cur), q, n, rows, bool(ep))

def _vec_scan(ids, bits, q4, quant_lo_step, q, n, rows, scoped):
    """The in-RAM lane proper (no database access, so it can run beside the SQL lanes): Hamming distances, every text tied
    at the VEC_CAND-th distance kept, 4-bit re-sort. Above VEC_THREADS_MIN texts each thread scans a slice and keeps its own
    VEC_CAND smallest distances; the global cutoff comes from those (10M: the single-threaded cutoff search took 50 ms)."""
    qb = np.packbits(q > 0).view(np.uint64)
    sub = bits[rows] if scoped else bits   # unscoped: scan the array in place (fancy-indexing all rows copied 476 MB at 10M)
    k = min(VEC_CAND, len(rows))
    if len(rows) >= VEC_THREADS_MIN:
        cuts = np.linspace(0, len(rows), VEC_THREADS + 1).astype(int)
        def part(j):
            d = np.bitwise_count(sub[cuts[j]:cuts[j + 1]] ^ qb).sum(1, dtype=np.uint16)
            return d, np.partition(d, min(k, len(d)) - 1)[:k]
        parts = list(_pool().map(part, range(VEC_THREADS)))
        cut = np.partition(np.concatenate([p[1] for p in parts]), k - 1)[k - 1]
        rows = rows[np.concatenate([np.flatnonzero(p[0] <= cut) + cuts[j] for j, p in enumerate(parts)])]   # ties kept, id order
    else:
        d = np.bitwise_count(sub ^ qb).sum(1)
        if len(rows) > VEC_CAND: rows = rows[d <= np.partition(d, VEC_CAND - 1)[VEC_CAND - 1]]   # WITH TIES; rows stay in id order
    lo, step = quant_lo_step
    a = q4[rows]; c = np.empty((len(rows), a.shape[1] * 2), dtype=np.float32); c[:, 0::2] = a >> 4; c[:, 1::2] = a & 15
    s = (c * step + lo) @ q
    return [int(ids[rows[i]]) for i in np.argsort(-s, kind="stable")[:n]]

def search(query, limit=5, entity=None, pool=40, exclude=(), rerank=False):
    with connect() as conn:   # closed on return: a connection left idle in a transaction blocks schema changes
        cur = conn.cursor()
        eids = entity_ids(cur, entity if entity else query) if (entity or query) else []
        flags = extract_flags(query)
        # a named tool filters every lane to its texts. Tried 2026-10-26: unscoped runs + the scoped ones as a boost; it
        # broke flag lookups (hashcat -m, ffuf -w, gdb -p: other tools' same flags won) and still missed sshpass for an ssh query
        runs = retrieve(cur, query, eids, pool, exclude)
        return rank(cur, query, eids, flags, runs, limit, rerank, exclude)

_ce = None
def cross_scores(query, texts):
    """Cross-encoder relevance (query, passage) -> score. Model is cached on the HDD, not the SSD."""
    global _ce
    if _ce is None:
        from fastembed.rerank.cross_encoder import TextCrossEncoder
        _ce = TextCrossEncoder(RERANK_MODEL, cache_dir=str(HDD / "models"))
    return list(_ce.rerank(query, texts))

def rank(cur, query, eids, flags, runs, limit, rerank=False, exclude=()):
    """Fuse retriever runs with weighted RRF, then apply the query-shape boosts. Each result is one text, shown under one
    entity (an entity named in the query if it has the text, else the name the text uses most); row[6] lists the other names."""
    score = {}
    for name, ids in runs.items():
        for rank, cid in enumerate(ids):
            score[cid] = score.get(cid, 0) + WEIGHT.get(name, 1.0) / (K + rank + 1)
    cand = sorted(score, key=score.get, reverse=True)[:max(limit * 6, 30)]
    if not cand: return []
    cur.execute("""SELECT b.id, r.id, e.name, r.section, r.source, coalesce(r.source_version, e.version), b.body
                   FROM bodies b JOIN chunk_refs r ON r.body_id = b.id JOIN entities e ON e.id = r.entity_id
                   WHERE b.id = ANY(%s) AND r.source <> ALL(%s) ORDER BY b.id, r.entity_id = ANY(%s) DESC, r.id""",
                (cand, list(exclude), eids or []))
    refs = {}
    for bid, rid, name, sec, src, ver, body in cur.fetchall():
        refs.setdefault(bid, []).append((rid, name, sec, src, ver, body))
    eid_names = set()
    if eids:
        cur.execute("SELECT name FROM entities WHERE id = ANY(%s)", (eids,)); eid_names = {r[0] for r in cur.fetchall()}
    rows = {}
    for bid, rs in refs.items():
        # one text, many names (traceroute/traceroute6, hostname/dnsdomainname, 441 OpenSSL aliases): show it under the name
        # the query named, else the name the text itself uses most (whole word), shorter on ties
        low = rs[0][5].lower()
        def pick(r):
            return (r[1] in eid_names, len(re.findall(r"(?<![\w-])" + re.escape(r[1].lower()) + r"(?![\w-])", low)), -len(r[1]), -r[0])
        best = max(rs, key=pick)
        rows[bid] = (*best, list(dict.fromkeys(r[1] for r in rs if r[1] != best[1])))
    cand = [i for i in cand if i in rows]          # texts whose every chunk comes from an excluded source drop out
    if not cand: return []
    wants = {sec for kw, secs in SECTION_WORDS.items() if re.search(kw, query, re.I) for sec in secs}
    bare_id = bool(flags) and not re.findall(r"[A-Za-z]{3,}", ID_RE.sub("", query)) and not any(f.startswith("-") for f in flags)
    name_q = False   # the whole query is an entity's name or alias ("seccomp", "user namespaces"): the user asks what it is
    if eids:
        cur.execute("SELECT 1 FROM entities WHERE id = ANY(%s) AND (lower(name) = %s OR %s = ANY(SELECT lower(a) FROM unnest(aliases) a))",
                    (eids, query.strip().lower(), query.strip().lower()))
        name_q = cur.fetchone() is not None
    for i in cand:
        body, sec = rows[i][5], rows[i][2]
        if name_q and re.search(r"(^|: )(overview|name|introduction)$", sec): score[i] += 0.3    # the page's own summary first
        elif name_q and re.search(r"(^|: )description$", sec): score[i] += 0.1
        if any(re.search(r"(^|\n)\s*" + re.escape(f) + r"(?![\w-])", body) for f in flags if f.startswith("-")):
            score[i] += 0.1       # chunk defines a queried flag on a line of its own
            if any(re.match(r"\s*" + re.escape(f) + r"(?![\w-])", body) for f in flags if f.startswith("-")):
                score[i] += 0.1   # ...and opens with it: it is the definition
        if bare_id and sec == "overview": score[i] += 0.15   # a bare ID asks "what is this"
        if sec in wants: score[i] += 0.15                    # the query names the kind of info wanted
        if BOILER.search(sec) and not re.search(r"author|copyright|bug|histor|licen|see also|related", query, re.I):
            score[i] -= 1.0                                  # boilerplate goes last
    cand = sorted(cand, key=score.get, reverse=True)
    if rerank and not flags:   # literal flag/ID queries are already precise; the cross-encoder helps natural-language ones
        head = cand[:RERANK_DEPTH]
        cs = cross_scores(query, [f"{rows[i][1]} · {rows[i][2]}\n{rows[i][5][:RERANK_CHARS]}" for i in head])
        order = [i for _, i in sorted(zip(cs, head), key=lambda t: -t[0])]
        mix = {i: RERANK_W / (K + order.index(i) + 1) + 1 / (K + n + 1) for n, i in enumerate(head)}   # RRF of both orders
        head = sorted(head, key=lambda i: -mix[i])
        for i in head: score[i] = mix[i]
        cand = head + cand[RERANK_DEPTH:]
    # corpus balance: ~19k security chunks (ATT&CK/CWE/KEV) sit among ~270k man/kernel chunks, so a strong security answer
    # can be outvoted by many weak doc chunks ("zip slip path traversal" -> zipgrep). Give the best one a top-3 slot.
    sec_i = next((i for i in cand[:30] if rows[i][3] in SECURITY_SOURCES), None)
    if sec_i is not None and cand.index(sec_i) >= 3 and score[sec_i] >= SEC_SLOT_MIN * score[cand[0]]:
        cand.remove(sec_i); cand.insert(2, sec_i)
    top = cand[:limit]
    return [(rows[i], score[i], [n for n, ids in runs.items() if i in ids]) for i in top]

if __name__ == "__main__":
    VEC_IN_RAM = False   # one query per process: loading the codes (0.4 s) costs more than one SQL scan (26 ms warm)
    q = " ".join(a for a in sys.argv[1:] if not a.startswith("--tool=") and a != "--rerank")
    tool = next((a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith("--tool=")), None)
    for (cid, ent, sec, src, ver, body, also), sc, via in search(q, entity=tool, rerank="--rerank" in sys.argv):
        more = f"  (same text in: {', '.join(also[:5])}{f' +{len(also) - 5} more' if len(also) > 5 else ''})" if also else ""
        print(f"\n=== {ent} · {sec} · {src} · {ver}  [rrf {sc:.4f} via {','.join(via)}]{more}")
        print(body)
