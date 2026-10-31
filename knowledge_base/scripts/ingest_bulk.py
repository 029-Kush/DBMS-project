"""Bulk-ingest every installed man page (sections 1-8) and the local kernel Documentation/*.rst tree.
Resumable: an entity that already has chunks is skipped. Man pages -> entity type 'manpage' (ext_id = section);
kernel docs -> type 'kernel-doc' (name = path under Documentation/). Pages that are only a '.so' redirect are skipped.
    .venv/bin/python scripts/ingest_bulk.py man [sections...] | kdoc [subdir]"""
import gzip, os, re, subprocess, sys, time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from common import *
from ingest_tools import split_blocks, man_sections
from ingest_concepts import rst_sections, KERNEL

MAN = Path("/usr/share/man")
ENVV = {"PATH": os.environ["PATH"], "LANG": "C", "MANWIDTH": "100", "TERM": "dumb"}
BATCH = 32      # pages per transaction / embedding call group

def render(path):
    try:
        raw = gzip.open(path, "rt", errors="replace").read(200) if str(path).endswith(".gz") else open(path, errors="replace").read(200)
        if raw.lstrip().startswith(".so "): return None
        p = subprocess.run(["man", "-P", "cat", "-l", str(path)], stdin=subprocess.DEVNULL, capture_output=True, text=True,
                           timeout=30, env=ENVV)
        return re.sub(r".\x08", "", p.stdout)
    except Exception:
        return None

def done_set(cur, typ):
    cur.execute("SELECT e.name, e.ext_id FROM entities e WHERE e.type=%s AND EXISTS (SELECT 1 FROM chunk_refs c WHERE c.entity_id=e.id)", (typ,))
    return set(cur.fetchall())

def flush(conn, cur, work, ver, source):
    """work: [(eid, name, [(section, text)])] -> one embedding call, then inserts."""
    flat = [(eid, name, s, t) for eid, name, items in work for s, t in items]
    if not flat: return 0
    vecs = embed([f"{n} {s}: {t}" for _, n, s, t in flat]); n = 0
    for (eid, name, sec, body), v in zip(flat, vecs):
        h = hashlib.sha256(f"{eid}|{sec}|{body}".encode()).hexdigest()
        n += store_chunk(cur, eid, sec, source, ver, 1, False, body, extract_flags(body), h, v)
    conn.commit(); return n

def run_man(sections):
    conn = connect(); cur = conn.cursor()
    mver = subprocess.run(["rpm", "-q", "--qf", "%{VERSION}-%{RELEASE}", "man-pages"], capture_output=True, text=True).stdout or None
    total = chunks = skipped = 0; t0 = time.time()
    for sec_no in sections:
        have = done_set(cur, "manpage")
        files = sorted(p for p in (MAN / f"man{sec_no}").glob("*") if p.is_file())
        todo = [p for p in files if (re.sub(r"\.(\d\w*)(\.gz)?$", "", p.name), sec_no) not in have]
        print(f"man{sec_no}: {len(files)} pages, {len(todo)} to do", flush=True)
        with ThreadPoolExecutor(4) as pool:
            for i in range(0, len(todo), BATCH):
                group = todo[i:i + BATCH]; work = []
                for path, text in zip(group, pool.map(render, group)):
                    name = re.sub(r"\.(\d\w*)(\.gz)?$", "", path.name)
                    if not text or not text.strip(): skipped += 1; continue
                    items = [(f"{name}({sec_no}): {s}", p.strip()) for s, b in man_sections(text) for p in split_blocks(b)]
                    items = [(s, t) for s, t in items if len(t) > 20]
                    if not items: skipped += 1; continue
                    eid = upsert_entity(cur, "manpage", name, ext_id=sec_no, version=mver)
                    work.append((eid, name, items))
                chunks += flush(conn, cur, work, mver, "man"); total += len(work)
                if (i // BATCH) % 10 == 0:
                    print(f"  man{sec_no} {i + len(group)}/{len(todo)}  pages={total} chunks={chunks} skipped={skipped}  {time.time()-t0:.0f}s", flush=True)
    print(f"man done: pages={total} chunks={chunks} skipped={skipped} {time.time()-t0:.0f}s")

def run_kdoc(sub):
    conn = connect(); cur = conn.cursor()
    ver = "linux-" + ".".join(re.search(rf"^{k} = (\S+)", (KERNEL / "Makefile").read_text(), re.M).group(1) for k in ("VERSION", "PATCHLEVEL"))
    base = KERNEL / "Documentation"; have = {n for n, _ in done_set(cur, "kernel-doc")}
    files = [f for f in sorted((base / sub).rglob("*.rst")) if str(f.relative_to(base).with_suffix("")) not in have]
    print(f"kdoc: {len(files)} files to do", flush=True); total = chunks = 0; t0 = time.time(); work = []
    for i, f in enumerate(files):
        name = str(f.relative_to(base).with_suffix(""))
        try: text = f.read_text(errors="replace")
        except Exception: continue
        items = [(s, p.strip()) for s, b in rst_sections(text) for p in split_blocks(b)]
        items = [(s, t) for s, t in items if len(t) > 20]
        if items: work.append((upsert_entity(cur, "kernel-doc", name, version=ver), name, items))
        if len(work) >= BATCH or i == len(files) - 1:
            chunks += flush(conn, cur, work, ver, "kernel-doc"); total += len(work); work = []
            print(f"  kdoc {i+1}/{len(files)} files={total} chunks={chunks} {time.time()-t0:.0f}s", flush=True)
    print(f"kdoc done: files={total} chunks={chunks} {time.time()-t0:.0f}s")

if __name__ == "__main__":
    what = sys.argv[1] if len(sys.argv) > 1 else "man"
    if what == "man": run_man(sys.argv[2:] or ["1", "8", "5", "7", "2", "4", "6", "3"])
    else: run_kdoc(sys.argv[2] if len(sys.argv) > 2 else "")
