"""
Builds the canary set: a fixed list of queries with their expected top-k
document ids, decided now while the corpus and embedding model are both
known-good. Every later health check re-runs these same queries and
compares actual results to this snapshot -- that comparison is what
"recall@k" means throughout this project.

Approach: for each category, embed a held-out probe sentence (written
by hand, not sampled from the corpus) with the current model, run a
real similarity search against the live documents table, and record
the top-k ids the *current, healthy* system returns. This is standard
practice when you don't have external human-labeled relevance judgments:
the baseline system's own output becomes the ground truth to detect
future regressions against.
"""
import json
import os

import psycopg2

from config import DB_DSN, EMBED_DIM
from embed_model import load_embedder
from search import exact_top_ids
from load_data import vec_to_pg

TOP_K = 10

PROBES = [
    ("technology", "a new AI model that reduces computing costs"),
    ("technology", "a smartphone update that improves battery performance"),
    ("sports", "a team winning a championship match"),
    ("sports", "an athlete setting a new record"),
    ("finance", "the central bank changing interest rates"),
    ("finance", "a stock price reacting to earnings"),
    ("health", "a clinical trial for a new treatment"),
    ("health", "a study on reducing hospital readmissions"),
    ("food", "a restaurant launching a new seasonal dish"),
    ("food", "a bakery using locally sourced ingredients"),
    ("travel", "a new flight route between two cities"),
    ("travel", "a resort becoming popular with tourists"),
]


def load_probes():
    path = os.environ.get("SELFHEAL_PROBES")
    return [tuple(p) for p in json.load(open(path))] if path else PROBES


def main():
    model = load_embedder()
    conn = psycopg2.connect(DB_DSN)
    cur = conn.cursor()
    cur.execute("TRUNCATE canary_set RESTART IDENTITY;")
    cur.execute("SELECT max(id) FROM documents")
    corpus_max_id = cur.fetchone()[0]

    for category, query_text in load_probes():
        vec = model.embed([query_text])[0]
        pgvec = vec_to_pg(vec)
        # Exact (brute-force) ground truth: an approximate index must never
        # define "correct", or a later index rebuild would look like drift.
        top_ids = exact_top_ids(cur, pgvec, TOP_K)
        cur.execute(
            """INSERT INTO canary_set (query_text, expected_doc_ids, category, corpus_max_id)
               VALUES (%s, %s, %s, %s)""",
            (query_text, top_ids, category, corpus_max_id),
        )
        print(f"[{category:10s}] {query_text!r} -> top-{TOP_K} ids {top_ids}")

    conn.commit()
    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
