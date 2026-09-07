import csv
from pathlib import Path

import psycopg2
from psycopg2.extras import execute_values

from config import DB_DSN, CURRENT_MODEL_VERSION
from embed_model import fit_and_save

CORPUS_PATH = Path(__file__).resolve().parent.parent / "data" / "corpus.csv"


def load_corpus():
    with open(CORPUS_PATH) as f:
        return list(csv.DictReader(f))


def vec_to_pg(vec):
    return "[" + ",".join(f"{x:.6f}" for x in vec) + "]"


def main():
    rows = load_corpus()
    texts = [r["body"] for r in rows]

    print(f"Fitting embedding model ({CURRENT_MODEL_VERSION}) on {len(texts)} documents...")
    model = fit_and_save(texts)
    vectors = model.embed(texts)

    conn = psycopg2.connect(DB_DSN)
    cur = conn.cursor()
    cur.execute("TRUNCATE documents RESTART IDENTITY;")

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
