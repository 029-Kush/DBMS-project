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
import psycopg2

from config import DB_DSN, EMBED_DIM
from embed_model import TfidfSvdEmbedder
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


def main():
    model = TfidfSvdEmbedder.load()
    conn = psycopg2.connect(DB_DSN)
    cur = conn.cursor()
    cur.execute("TRUNCATE canary_set RESTART IDENTITY;")

    for category, query_text in PROBES:
        vec = model.embed([query_text])[0]
        pgvec = vec_to_pg(vec)
        cur.execute(
            """
            SELECT id FROM documents
            ORDER BY embedding <=> %s::vector
            LIMIT %s
            """,
            (pgvec, TOP_K),
        )
        top_ids = [r[0] for r in cur.fetchall()]
        cur.execute(
            """INSERT INTO canary_set (query_text, expected_doc_ids, category)
               VALUES (%s, %s, %s)""",
            (query_text, top_ids, category),
        )
        print(f"[{category:10s}] {query_text!r} -> top-{TOP_K} ids {top_ids}")

    conn.commit()
    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
