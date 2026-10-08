import os

DB_DSN = os.environ.get(
    "SELFHEAL_DSN",
    "dbname=selfheal user=svuser password=svpass host=127.0.0.1 port=5432",
)

# Embedder presets: name -> (dimension, model-version label stored per row).
# 'tfidf' is the original offline baseline; the other two are real 384-d
# transformer models run locally through fastembed (ONNX).
EMBEDDER_PRESETS = {
    "tfidf": (64, "tfidf-svd-v1"),
    "bge": (384, "bge-small-en-v1.5"),
    "minilm": (384, "all-MiniLM-L6-v2"),
}
EMBEDDER = os.environ.get("SELFHEAL_EMBEDDER", "tfidf")
if EMBEDDER not in EMBEDDER_PRESETS:
    raise SystemExit(f"SELFHEAL_EMBEDDER must be one of {sorted(EMBEDDER_PRESETS)}")
EMBED_DIM, CURRENT_MODEL_VERSION = EMBEDDER_PRESETS[EMBEDDER]

# Phase 2 health thresholds. These are intentionally explicit so the demo can
# explain why a run was marked unhealthy instead of hiding the decision in a
# black-box score.
MIN_RECALL_AT_K = 0.95
MAX_VERSION_SKEW_PCT = float(os.environ.get("SELFHEAL_MAX_SKEW_PCT", 0.0))  # exact count, so 0 is safe for healing
MAX_DEAD_TUPLE_PCT = 20.0
MAX_DISTANCE_SHIFT_PCT = 20.0

# Phase 4/5 signals.
MIN_ANN_RECALL_AT_K = float(os.environ.get("SELFHEAL_MIN_ANN_RECALL", 0.90))
ANN_RECALL_MARGIN = 0.03        # allowed drop below the first (healthy) ANN recall
ANN_PROBE_QUERIES = 40          # random stored vectors used as extra ANN probes
SENTINEL_SAMPLE = int(os.environ.get("SELFHEAL_SENTINEL_SAMPLE", 64))
SENTINEL_TOLERANCE = 1e-3       # cosine distance; ONNX/float noise is ~1e-6

# Healer behaviour.
HEAL_BATCH_SIZE = int(os.environ.get("SELFHEAL_BATCH_SIZE", 500))
RETRY_COOLDOWN_SECONDS = int(os.environ.get("SELFHEAL_RETRY_COOLDOWN", 3600))
HEAL_LOCK_KEY = 0x5E1F4EA1      # pg advisory lock: one healer at a time
