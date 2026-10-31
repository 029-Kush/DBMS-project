"""Show one entity (by ID or name) with its facts and relationships. Usage: show.py T1055.012 | CWE-79 | CVE-2021-44228 | nmap"""
import sys
from common import *

def main(q):
    conn = connect(); cur = conn.cursor()
    cur.execute("""SELECT id,type,name,ext_id,aliases,summary,version,facts FROM entities
                   WHERE upper(ext_id)=upper(%s) OR lower(name)=lower(%s) ORDER BY (ext_id<>'') DESC, type LIMIT 3""", (q, q))
    rows = cur.fetchall()
    if not rows: print("not found"); return
    for eid, t, name, xid, aliases, summ, ver, facts in rows:
        print(f"\n## {xid or ''} {name}  [{t}]  ({ver})")
        if aliases: print("aliases:", ", ".join(aliases))
        print("facts:", {k: v for k, v in facts.items() if k != "stix_id"})
        cur.execute("""SELECT l.rel, 'out', e.type, e.ext_id, e.name FROM entity_links l JOIN entities e ON e.id=l.dst WHERE l.src=%s
                       UNION ALL
                       SELECT l.rel, 'in', e.type, e.ext_id, e.name FROM entity_links l JOIN entities e ON e.id=l.src WHERE l.dst=%s
                       ORDER BY 1,2,3,4""", (eid, eid))
        groups = {}
        for rel, d, et, xi, en in cur.fetchall(): groups.setdefault((rel, d, et), []).append(f"{xi or ''} {en}".strip())
        for (rel, d, et), names in groups.items():
            arrow = f"--{rel}-->" if d == "out" else f"<--{rel}--"
            print(f"  {arrow} {et} ({len(names)}): {'; '.join(names[:8])}{' …' if len(names) > 8 else ''}")
        cur.execute("SELECT section, count(*) FROM chunks WHERE entity_id=%s GROUP BY 1 ORDER BY 1", (eid,))
        print("  chunks:", dict(cur.fetchall()))

if __name__ == "__main__":
    main(" ".join(sys.argv[1:]))
