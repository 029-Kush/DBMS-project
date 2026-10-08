"""Turn benchmark.py matrix output into a detection + identifiability report.

  python analyze_matrix.py matrix.json

Detection: fraction of seeds in which each signal fired. Identifiability: which
faults leave an identical issue-code signature (so the signals alone cannot tell
them apart) -- the reason an unexplained-symptom case must be escalated or
investigated rather than guessed.
"""
import json
import sys
from collections import Counter, defaultdict


def main(path):
    cells = json.load(open(path))
    by = defaultdict(list)
    for c in cells:
        by[(c["fault"], c["pct"])].append(c)

    faults = sorted({c["fault"] for c in cells})
    pcts = sorted({c["pct"] for c in cells})
    print("DETECTION (share of seeds where ANY issue fired; sentinel / canary-recall in brackets)\n")
    print(f"{'fault':26s}" + "".join(f"{p:>16g}%" for p in pcts))
    for fault in faults:
        row = []
        for pct in pcts:
            cs = by[(fault, pct)]
            any_issue = sum(1 for c in cs if c["issues"]) / len(cs)
            sentinel = sum(1 for c in cs if "VECTOR_MISMATCH" in c["issues"]) / len(cs)
            recall = sum(1 for c in cs if "LOW_RECALL" in c["issues"]) / len(cs)
            row.append(f"{any_issue:4.0%} [{sentinel:3.0%}/{recall:3.0%}]")
        print(f"{fault:26s}" + "".join(f"{r:>17s}" for r in row))

    print("\nDOMINANT ISSUE SIGNATURE per fault x severity")
    sigs = {}
    for fault in faults:
        for pct in pcts:
            common = Counter(tuple(c["issues"]) for c in by[(fault, pct)]).most_common(1)[0][0]
            sigs[(fault, pct)] = common
            print(f"  {fault:26s} {pct:>5g}%  {', '.join(common) or '(nothing detected)'}")

    print("\nIDENTIFIABILITY: faults sharing an identical signature at the same severity")
    for pct in pcts:
        groups = defaultdict(list)
        for fault in faults:
            groups[sigs[(fault, pct)]].append(fault)
        for sig, members in groups.items():
            if len(members) > 1:
                print(f"  {pct:g}%: {{{', '.join(members)}}} all look like [{', '.join(sig) or 'nothing'}]")

    print("\nMISSED DETECTIONS (fault present, no issue raised)")
    missed = [(f, p, sum(1 for c in by[(f, p)] if not c['issues']), len(by[(f, p)])) for f in faults for p in pcts]
    for f, p, m, n in missed:
        if m:
            print(f"  {f:26s} {p:>5g}%  missed {m}/{n}")


if __name__ == "__main__":
    main(sys.argv[1])
