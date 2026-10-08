import csv
from pathlib import Path

import psycopg2
from psycopg2.extras import execute_values

import os

from config import DB_DSN, CURRENT_MODEL_VERSION, EMBEDDER
from embed_model import fit_and_save, load_embedder

CORPUS_PATH = Path(os.environ.get(
    "SELFHEAL_CORPUS",
    Path(__file__).resolve().parent.parent / "data" / "corpus.csv",
))


def load_corpus():
    with open(CORPUS_PATH) as f:
        return list(csv.DictReader(f))


def vec_to_pg(vec):
    return "[" + ",".join(f"{x:.6f}" for x in vec) + "]"


def main():
    rows = load_corpus()
    texts = [r["body"] for r in rows]

    if EMBEDDER == "tfidf":
        print(f"Fitting embedding model ({CURRENT_MODEL_VERSION}) on {len(texts)} documents...")
        model = fit_and_save(texts)
    else:
        print(f"Embedding {len(texts)} documents with {CURRENT_MODEL_VERSION}...")
        model = load_embedder()
    vectors = model.embed(texts)

    conn = psycopg2.connect(DB_DSN)
    cur = conn.cursor()
    cur.execute("TRUNCATE documents RESTART IDENTITY CASCADE;")

    records = [
        (r["category"], r["body"], vec_to_pg(vectors[i]), CURRENT_MODEL_VERSION)
        for i, r in enumerate(rows)
    ]
    execute_values(
        cur,
        """INSERT INTO documents (category, body, embedding, embedding_model_version)
           VALUES %s""",
        records,
        template="(%s, %s, %s::vector, %s)",
    )
    conn.commit()

    cur.execute("SELECT count(*) FROM documents;")
    print(f"Loaded {cur.fetchone()[0]} rows into documents.")
    cur.execute("SELECT category, count(*) FROM documents GROUP BY category ORDER BY category;")
    for cat, n in cur.fetchall():
        print(f"  {cat:12s} {n}")

    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
