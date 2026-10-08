"""Pure diagnosis: turn health issue codes into a targeted, staged repair plan.

No database access here, so every rule is unit-testable. The healer only runs
actions this module returns. Recall/distance loss that no *specific* cause
explains is not guessed at: it triggers an exhaustive vector scan (an
investigation), and only if that finds nothing is it escalated to a human.
"""
from dataclasses import dataclass

REEMBED_STALE = "REEMBED_STALE"
REPAIR_VECTORS = "REPAIR_VECTORS"   # sentinel found silent corruption -> scan + fix
SCAN_VECTORS = "SCAN_VECTORS"       # symptoms but no known cause -> investigate
REBUILD_INDEX = "REBUILD_INDEX"
VACUUM = "VACUUM"

# Order: fix data first, then build the index over correct vectors, then vacuum
# the dead tuples the data repairs created.
ACTION_ORDER = (REEMBED_STALE, REPAIR_VECTORS, SCAN_VECTORS, REBUILD_INDEX, VACUUM)

# Symptoms: effects that several different causes can produce.
SYMPTOMS = {"LOW_RECALL", "DISTANCE_DRIFT"}
# Primary codes: each has exactly one supported repair and must clear after it.
PRIMARY = {"VERSION_SKEW", "VECTOR_MISMATCH", "INDEX_MISSING", "INDEX_DEGRADED", "TABLE_BLOAT"}

DATA_WRITERS = {REEMBED_STALE, REPAIR_VECTORS, SCAN_VECTORS}


@dataclass(frozen=True)
class Diagnosis:
    actions: tuple          # ordered action names to execute
    explained: frozenset    # primary codes the plan must clear (verification)
    investigating: frozenset  # symptoms with no known cause yet
    notes: tuple            # human-readable reasoning, stored in the audit row

    @property
    def signature(self):
        return ",".join(self.actions) + "|" + ",".join(sorted(self.investigating))

    @property
    def actionable(self):
        return bool(self.actions)


def diagnose(issues):
    issues = set(issues)
    actions, explained, notes = set(), set(), []
    symptoms = issues & SYMPTOMS
    covered = set()  # symptoms some specific cause accounts for

    if "VERSION_SKEW" in issues:
        actions.add(REEMBED_STALE)
        explained.add("VERSION_SKEW")
        covered |= symptoms
        notes.append("rows labelled with a non-current model -> re-embed them from their text")

    if "VECTOR_MISMATCH" in issues:
        actions.add(REPAIR_VECTORS)
        explained.add("VECTOR_MISMATCH")
        covered |= symptoms
        notes.append("sentinel re-embedding disagrees with stored vectors -> scan and repair mismatched rows")

    if "INDEX_MISSING" in issues:
        actions.add(REBUILD_INDEX)
        explained.add("INDEX_MISSING")
        notes.append("HNSW index absent or invalid -> rebuild it")

    if "INDEX_DEGRADED" in issues:
        actions.add(REBUILD_INDEX)
        explained.add("INDEX_DEGRADED")
        covered |= symptoms
        notes.append("ANN recall below exact search -> rebuild the HNSW index with canonical parameters")

    if "TABLE_BLOAT" in issues:
        actions.add(VACUUM)
        explained.add("TABLE_BLOAT")
        notes.append("dead-tuple ratio high -> vacuum")

    if "INDEX_NOT_USED" in issues:
        notes.append("INDEX_NOT_USED ignored: planner may legitimately seq-scan a small table")

    investigating = symptoms - covered
    if investigating:
        actions.add(SCAN_VECTORS)
        notes.append(
            "%s with no specific cause -> exhaustive vector scan; if vectors match their text, "
            "the frozen canaries are probably outdated -> escalate for recalibration"
            % ", ".join(sorted(investigating))
        )

    if actions & DATA_WRITERS:
        actions.add(VACUUM)  # data repairs create dead tuples
        if not any("vacuum" in n for n in notes):
            notes.append("data repair creates dead tuples -> vacuum afterwards")

    ordered = tuple(a for a in ACTION_ORDER if a in actions)
    return Diagnosis(ordered, frozenset(explained), frozenset(investigating), tuple(notes))
