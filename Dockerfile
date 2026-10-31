# VectorIT web app (kb_web) + the hybrid search engine it calls (knowledge_base/scripts).
# The database is external (DATABASE_URL); the image holds only code and the query embedding model.
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    KB_EMBEDDER=minilm KB_MODEL_DIR=/app/models KB_WARMUP=1
WORKDIR /app

COPY kb_web/requirements.txt kb_web/requirements.txt
RUN pip install --no-cache-dir -r kb_web/requirements.txt
# bake the ONNX query embedder into the image: no download at start-up, and the container needs no outbound access
RUN python -c "from fastembed import TextEmbedding; TextEmbedding('sentence-transformers/all-MiniLM-L6-v2', cache_dir='/app/models')"

COPY knowledge_base/scripts knowledge_base/scripts
COPY knowledge_base/sql knowledge_base/sql
COPY kb_web kb_web
# the explorer page and the two modules it needs (3-D map UI, traced HNSW, display formatting)
COPY selfheal_pgvector/explorer/static selfheal_pgvector/explorer/static
COPY selfheal_pgvector/explorer/readable.py selfheal_pgvector/explorer/hnsw_trace.py selfheal_pgvector/explorer/

RUN useradd --system --uid 10001 app && chown -R app /app/models
USER app
EXPOSE 8000
# bootstrap: tables + bundled seed data on a fresh database (a no-op afterwards); then one worker (the model and vector
# codes are loaded once per process; free hosts give 512 MB), threads for concurrency
CMD ["sh", "-c", "KB_WARMUP=0 flask --app kb_web.wsgi bootstrap && exec gunicorn kb_web.wsgi:app --bind 0.0.0.0:${PORT:-8000} --workers 1 --threads 4 --timeout 120 --access-logfile -"]
