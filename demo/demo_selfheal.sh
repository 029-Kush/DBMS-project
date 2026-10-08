#!/usr/bin/env bash
# Live demo of the self-healing loop on Linux/macOS. Press Enter between steps (or run with --auto).
#
#   SELFHEAL_DSN="dbname=... user=... password=... host=127.0.0.1 port=5432" \
#   SELFHEAL_EMBEDDER=minilm ./demo/demo_selfheal.sh
#
# Story: a share of the stored vectors gets silently replaced by vectors from a different
# embedding model. Nothing errors, the version column still looks fine, but search quietly
# returns wrong documents. The healer finds it from the data itself, repairs only the bad
# rows from the document text, verifies, and an independent audit confirms every row.
set -euo pipefail
cd "$(dirname "$0")/../selfheal_pgvector/scripts"
PY="${PYTHON:-python3}"
QUERY="${DEMO_QUERY:-an athlete breaks a world record at the games}"
STATE="$(mktemp -t selfheal_demo.XXXXXX)"; trap 'rm -f "$STATE"' EXIT
PCT="${DEMO_PERCENT:-25}"
AUTO=0; [ "${1:-}" = "--auto" ] && AUTO=1
step() { echo; echo "=== $1"; [ "$AUTO" = 1 ] || read -r -p "    [Enter to run] " _; }

top5() {  # top-10 for the demo query; "save" remembers the healthy answer, later calls flag what changed
  $PY - "$QUERY" "$STATE" "${1:-show}" <<'EOF2' 2>&1 | grep -v -i warn
import json, sys, psycopg2
from config import DB_DSN
from embed_model import load_embedder
from load_data import vec_to_pg
q, state, mode = sys.argv[1:4]; v = vec_to_pg(load_embedder().embed([q])[0])
c = psycopg2.connect(DB_DSN); cur = c.cursor()
cur.execute("""SELECT id, category, round((1-(embedding <=> %s::vector))::numeric,3), left(body,64)
               FROM documents ORDER BY embedding <=> %s::vector LIMIT 10""", (v, v))
rows = cur.fetchall()
if mode == "save": json.dump([r[0] for r in rows], open(state, "w"))
base = set(json.load(open(state)))
print(f'  query: "{q}"')
for i, (id_, cat, sc, body) in enumerate(rows, 1):
    flag = "" if id_ in base else "   <-- not in the healthy answer"
    print(f"  {i:2d}. {sc}  [{cat:8s}] {body}{flag}")
print(f"  {len({r[0] for r in rows} & base)}/10 results match the healthy answer")
EOF2
}
health() { $PY eval_recall.py "$1" 2>&1 | grep -E "status:|issues:|avg recall|ANN recall|sentinel|version skew" | sed 's/^/  /'; }

step "1. Healthy database: search results and health snapshot"
top5 save; health baseline_demo

step "2. Inject the fault: ${PCT}% of rows silently get vectors from another model (labels untouched)"
$PY - "$PCT" <<'EOF' 2>&1 | grep -v -i warn
import sys, fault_lab
other = "bge" if __import__("config").EMBEDDER != "bge" else "minilm"
fault_lab.inject_cross_model(float(sys.argv[1]), 42, other, labelled=False)
print("  injected (silent: embedding_model_version still reads as current)")
EOF
echo "  version labels now:"; $PY -c "
import psycopg2; from config import DB_DSN
c=psycopg2.connect(DB_DSN); cur=c.cursor(); cur.execute('select embedding_model_version,count(*) from documents group by 1'); print('   ', cur.fetchall())"

step "3. The harm: same query, no error, wrong answers; metadata alone cannot see it"
top5

step "4. Observe only (no repair): the monitor flags it from the vectors themselves"
$PY monitor.py 2>&1 | grep -E "status|issues|Health|HEALTHY|CRITICAL|DEGRADED|VECTOR|LOW_RECALL" | head -8 | sed 's/^/  /'

step "5. Heal: diagnose, repair only the affected rows from document text, verify, or roll back"
$PY - <<'EOF3' 2>&1 | grep -v -i warn | grep -v "recall@10=" | grep -E "status:|issues:|sentinel|avg recall|  cycle|  final"
import heal
hist = heal.heal_until_stable("demo")
for i, o in enumerate(hist, 1):
    a = o.get("diagnosis") or o.get("actions") or ""
    print(f"  cycle {i}: {o['outcome']}  {str(a)[:150]}")
print("  final  :", hist[-1]["outcome"])
EOF3

step "6. After: same query, health snapshot, and an independent audit of every row"
top5; health after_demo
$PY - <<'EOF' 2>&1 | grep -v -i warn
from run_phase4_experiment import inconsistent_rows
print("  audit (re-embed every row's text and compare):", inconsistent_rows(), "rows inconsistent  (0 = fully correct)")
EOF
echo; echo "Done. Optional: open the explorer before step 2 and after step 5 to see the map change."
