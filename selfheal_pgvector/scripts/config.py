import os

DB_DSN = os.environ.get(
    "SELFHEAL_DSN",
    "dbname=selfheal user=svuser password=svpass host=127.0.0.1 port=5432",
)

EMBED_DIM = 64
CURRENT_MODEL_VERSION = "tfidf-svd-v1"

# Phase 2 health thresholds. These are intentionally explicit so the demo can
# explain why a run was marked unhealthy instead of hiding the decision in a
# black-box score.
MIN_RECALL_AT_K = 0.95
MAX_VERSION_SKEW_PCT = 5.0
MAX_DEAD_TUPLE_PCT = 20.0
MAX_DISTANCE_SHIFT_PCT = 20.0
