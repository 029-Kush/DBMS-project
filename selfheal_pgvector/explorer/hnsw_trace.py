"""A small HNSW index whose search records every step it takes.

pgvector does not expose its internal graph walk, so the explorer serves its
results from this index (same m / ef_construction as the pgvector index) and
checks them against pgvector and brute force. The trace is therefore the real
search that produced the answer, not a replay of a different one.

Distances are cosine distance (1 - dot of unit vectors), like pgvector's <=>.
"""
import heapq
import math
import pickle

import numpy as np


class TraceHNSW:
    def __init__(self, vectors, m=16, ef_construction=64, seed=7):
        self.v = np.ascontiguousarray(vectors, dtype=np.float32)
        norms = np.linalg.norm(self.v, axis=1, keepdims=True)
        self.v = self.v / np.maximum(norms, 1e-12)
        self.n = len(self.v)
        self.m, self.m0, self.efc = m, 2 * m, ef_construction
        self.mult = 1.0 / math.log(m)
        self.rng = rng = np.random.default_rng(seed)
        self.level = np.floor(-np.log(1.0 - rng.random(self.n)) * self.mult).astype(int)
        self._touched, self._pruned = set(), []
        self.links = [[[] for _ in range(self.level[i] + 1)] for i in range(self.n)]
        self.entry, self.top = 0, int(self.level[0])
        for i in range(1, self.n):
            self._insert(i)

    def dist(self, q, ids):
        return 1.0 - self.v[ids] @ q

    # -- construction -----------------------------------------------------
    def _search_layer(self, q, eps, ef, layer):
        visited = set(eps)
        d0 = self.dist(q, list(eps))
        cand = [(d, e) for d, e in zip(d0, eps)]
        heapq.heapify(cand)
        best = [(-d, e) for d, e in cand]
        heapq.heapify(best)
        while cand:
            d, c = heapq.heappop(cand)
            if d > -best[0][0] and len(best) >= ef:
                break
            nb = [x for x in self.links[c][layer] if x not in visited]
            if not nb:
                continue
            visited.update(nb)
            for dn, x in zip(self.dist(q, nb), nb):
                if len(best) < ef or dn < -best[0][0]:
                    heapq.heappush(cand, (dn, x))
                    heapq.heappush(best, (-dn, x))
                    if len(best) > ef:
                        heapq.heappop(best)
        return sorted((-d, e) for d, e in best)

    def _select(self, cands, m):
        """Heuristic neighbour selection (keeps diverse directions)."""
        chosen = []
        for d, e in cands:
            if len(chosen) >= m:
                break
            if all(d < 1.0 - float(self.v[e] @ self.v[c]) for c in chosen):
                chosen.append(e)
        for d, e in cands:  # fill up if the heuristic left room
            if len(chosen) >= m:
                break
            if e not in chosen:
                chosen.append(e)
        return chosen

    def _insert(self, i):
        q, lvl = self.v[i], int(self.level[i])
        ep = [self.entry]
        for layer in range(self.top, lvl, -1):
            ep = [self._search_layer(q, ep, 1, layer)[0][1]]
        for layer in range(min(lvl, self.top), -1, -1):
            found = self._search_layer(q, ep, self.efc, layer)
            mm = self.m0 if layer == 0 else self.m
            chosen = self._select(found, self.m)
            self.links[i][layer] = list(chosen)
            for c in chosen:
                lk = self.links[c][layer]
                lk.append(i)
                self._touched.add((c, layer))
                if len(lk) > mm:
                    dd = self.dist(self.v[c], lk)
                    order = sorted(zip(dd, lk))
                    kept = self._select(order, mm)
                    self._pruned += [(c, r, layer) for r in lk if r not in kept and r != i]
                    self.links[c][layer] = kept
            ep = [e for _, e in found]
        if lvl > self.top:
            self.entry, self.top = i, lvl

    def add_batch(self, vectors):
        """Insert new vectors one by one. Returns, per new node, the links it formed and which existing
        nodes' neighbour lists changed (gained the new node, or dropped an old neighbour)."""
        vecs = np.ascontiguousarray(vectors, dtype=np.float32)
        vecs = vecs / np.maximum(np.linalg.norm(vecs, axis=1, keepdims=True), 1e-12)
        start = self.n
        lv = np.floor(-np.log(1.0 - self.rng.random(len(vecs))) * self.mult).astype(int)
        self.v = np.vstack([self.v, vecs]); self.level = np.concatenate([self.level, lv])
        self.links += [[[] for _ in range(int(l) + 1)] for l in lv]
        self.n += len(vecs)
        out = []
        for i in range(start, self.n):
            self._touched, self._pruned = set(), []
            self._insert(i)
            out.append({"node": i, "layer": int(self.level[i]), "links": [int(x) for x in self.links[i][0]],
                        "touched": sorted({int(c) for c, l in self._touched if l == 0}),
                        "pruned": [[int(c), int(r)] for c, r, l in self._pruned if l == 0]})
        return out

    # -- traced search ----------------------------------------------------
    def search(self, qvec, k=10, ef=40):
        """Return (top_k, trace). top_k = [(id, dist)]; trace = list of step dicts."""
        q = np.asarray(qvec, dtype=np.float32)
        q = q / max(float(np.linalg.norm(q)), 1e-12)
        steps = []
        cur = self.entry
        dcur = float(self.dist(q, [cur])[0])
        steps.append({"kind": "entry", "layer": int(self.top), "node": int(cur), "dist": dcur})
        # upper layers: greedy descent, one step per node we stand on
        for layer in range(self.top, 0, -1):
            while True:
                nb = self.links[cur][layer]
                if not nb:
                    break
                dd = self.dist(q, nb)
                j = int(np.argmin(dd))
                ev = [[int(x), float(d)] for x, d in zip(nb, dd)]
                moved = float(dd[j]) < dcur
                steps.append({"kind": "greedy", "layer": layer, "node": int(cur), "dist": dcur,
                              "evaluated": ev, "moved_to": int(nb[j]) if moved else None})
                if not moved:
                    break
                cur, dcur = int(nb[j]), float(dd[j])
        # bottom layer: beam search of width ef
        l0_parent = {int(cur): None}
        visited = {int(cur)}
        cand = [(dcur, int(cur))]
        best = [(-dcur, int(cur))]
        while cand:
            d, c = heapq.heappop(cand)
            if d > -best[0][0] and len(best) >= ef:
                steps.append({"kind": "stop", "layer": 0, "node": c, "dist": float(d),
                              "worst_kept": float(-best[0][0])})
                break
            nb = [x for x in self.links[c][0] if x not in visited]
            if not nb:
                continue
            visited.update(nb)
            ev = []
            for dn, x in zip(self.dist(q, nb), nb):
                dn = float(dn)
                if len(best) < ef or dn < -best[0][0]:
                    heapq.heappush(cand, (dn, int(x)))
                    heapq.heappush(best, (-dn, int(x)))
                    l0_parent[int(x)] = c
                    ev.append([int(x), dn, "kept"])
                    if len(best) > ef:
                        heapq.heappop(best)
                else:
                    ev.append([int(x), dn, "rejected"])
            steps.append({"kind": "expand", "layer": 0, "node": int(c), "dist": float(d), "evaluated": ev})
        ranked = sorted((-d, e) for d, e in best)
        top = [(int(e), float(d)) for d, e in ranked[:k]]
        # "almost" results: the next best on the shortlist (ranks k+1..k+3). Each was added to the
        # shortlist through an expanded parent, so it has a real chain back to the entry point.
        near_miss = [(int(e), float(d)) for d, e in ranked[k:k + 3]]

        def chain(node):
            out, x = [], node
            while x is not None:
                out.append(int(x))
                x = l0_parent.get(x)
            return out[::-1]

        upper = [s["node"] for s in steps if s["kind"] in ("entry", "greedy")]
        upper = list(dict.fromkeys(upper))  # entry -> ... -> node entering layer 0
        paths = {int(e): upper[:-1] + chain(e) for e, _ in top + near_miss}
        return top, {"steps": steps, "paths": paths, "near_miss": near_miss,
                     "visited_layer0": len(visited), "ef": ef, "k": k}


def save(index, path):
    with open(path, "wb") as f:
        pickle.dump(index, f, protocol=4)


def load(path):
    with open(path, "rb") as f:
        return pickle.load(f)
