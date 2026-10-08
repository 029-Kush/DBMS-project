"""Apply a SQL file with the vector dimension taken from EMBED_DIM.

The checked-in SQL says VECTOR(64) (the TF-IDF baseline). Real embedders are
384-d, so this substitutes the dimension at apply time instead of forking the
schema:  python db_setup.py ../sql/schema.sql [../sql/phase4_migration.sql ...]
"""
import sys
from pathlib import Path

import psycopg2

from config import DB_DSN, EMBED_DIM


def apply(path):
    sql = Path(path).read_text().replace("VECTOR(64)", f"VECTOR({EMBED_DIM})")
    conn = psycopg2.connect(DB_DSN)
    conn.autocommit = True
    try:
        conn.cursor().execute(sql)
    finally:
        conn.close()
    print(f"applied {path} (dim={EMBED_DIM})")


if __name__ == "__main__":
    for file in sys.argv[1:]:
        apply(file)
