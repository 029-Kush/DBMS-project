"""Run and log an observable user query against the vector database."""
import argparse

import psycopg2

from config import DB_DSN
from embed_model import TfidfSvdEmbedder
from search import search_documents


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("text", help="free-text search query")
    parser.add_argument("--top-k", type=int, default=10, help="results to return")
    return parser.parse_args()


def main():
    args = parse_args()
    if args.top_k < 1:
        raise SystemExit("--top-k must be at least 1")

    model = TfidfSvdEmbedder.load()
    conn = psycopg2.connect(DB_DSN)
    cur = conn.cursor()
    try:
        result = search_documents(
            cur,
            model,
            args.text,
            top_k=args.top_k,
            source="user",
            log_query=True,
        )
        cur.execute(
            "SELECT id, category, body FROM documents WHERE id = ANY(%s)",
            (result["ids"],),
        )
        documents = {row[0]: row[1:] for row in cur.fetchall()}
        conn.commit()

        print(f"Query: {args.text!r}")
        print(f"Latency: {result['latency_ms']:.2f} ms\n")
        for rank, (doc_id, distance) in enumerate(
            zip(result["ids"], result["distances"]), start=1
        ):
            category, body = documents[doc_id]
            print(
                f"{rank:2d}. id={doc_id:<3d} distance={distance:.4f} "
                f"category={category:<10s} {body}"
            )
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    main()
