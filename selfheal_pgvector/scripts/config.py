import os

DB_DSN = os.environ.get(
    "SELFHEAL_DSN",
    "dbname=selfheal user=svuser password=svpass host=127.0.0.1 port=5432",
)

EMBED_DIM = 64
CURRENT_MODEL_VERSION = "tfidf-svd-v1"
