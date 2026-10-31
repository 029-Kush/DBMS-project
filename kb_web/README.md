# DocSearch — web application

A Flask + SQLAlchemy web app over the PostgreSQL knowledge base in `knowledge_base/`: search technical documentation
(man pages, tldr pages, command help) in plain words or by exact command/flag, browse and edit entries, and keep
bookmarks. The ranking is the hybrid engine in `knowledge_base/scripts/search.py`: exact flag/ID, full-text and
vector lanes fused with reciprocal rank fusion, with vectors stored as 1-bit + 4-bit codes instead of floats.

## Technology stack

| Layer | Choice |
|---|---|
| Database | PostgreSQL 16 + pgvector + pg_trgm (local Docker; Supabase in the cloud) |
| Backend | Python 3.11, Flask 3, Flask-SQLAlchemy (ORM), Flask-Login (sessions), Flask-WTF (forms + CSRF), bcrypt |
| Search | `knowledge_base/scripts/search.py` (psycopg 3, numpy), ONNX MiniLM query embedder via fastembed |
| UI | Server-rendered Jinja templates, one responsive stylesheet (light/dark) |
| Delivery | Docker image (gunicorn) → Docker Hub, GitHub Actions CI, Render web service |

## Roles

| Role | Can |
|---|---|
| viewer (every new account) | search, browse entities, manage *their own* bookmarks |
| editor | + create/edit entities, add passages (embedded and indexed on save) |
| admin | + delete entities, merge duplicates (stored procedure), change roles, enable/disable accounts, read the audit log |

## Database objects added by the app (`sql/001_web.sql`)

- Tables `users` (single users table, role column with CHECK), `bookmarks` (users M:N passages), `search_log`, `audit_log`.
- Trigger `audit_row()` on `entities` and `users`: every insert/update/delete is logged with the app user who made it
  (`SET LOCAL app.user` per transaction), password hashes excluded.
- Stored procedure `merge_entities(src, dst)`: moves passages and links, keeps the old name as an alias, deletes the
  duplicate — all or nothing.
- Views `v_source_stats` (aggregates per source, dashboard) and `v_user_activity` (admin users page).

## Run locally

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r kb_web/requirements.txt
cp .env.example .env            # fill SECRET_KEY and DATABASE_URL, then:
set -a; . ./.env; set +a
flask --app kb_web.wsgi init-db                      # app tables, trigger, procedure, views (idempotent)
#   on an empty database add --with-kb-schema to create the knowledge-base tables too
flask --app kb_web.wsgi create-admin                 # prompts for username, email, password
flask --app kb_web.wsgi run --port 5001              # or: gunicorn kb_web.wsgi:app
```

Tests (need an empty PostgreSQL with pgvector):

```bash
docker run -d --rm --name kbweb-test -e POSTGRES_PASSWORD=test -p 127.0.0.1:5440:5432 pgvector/pgvector:pg16
TEST_DATABASE_URL=postgresql://postgres:test@127.0.0.1:5440/postgres python -m pytest kb_web/tests -v
```

## Docker

```bash
docker build -t docsearch .
docker run --rm -p 8000:8000 -e SECRET_KEY -e DATABASE_URL docsearch
```

## Backup and restore

- `kb_web/scripts/export_cloud_subset.sh` — builds the trimmed cloud copy (`KEEP="tldr help man"`) from the local
  database and writes a `pg_dump -Fc` backup to the external drive.
- `kb_web/scripts/restore.sh <file.dump>` — `pg_restore` into whatever `DATABASE_URL` points at (local or Supabase).
- `knowledge_base/scripts/backup.sh` — full backup of the local database.

## Deployment (Render + Supabase, both free tiers)

1. Supabase: create a project, copy the **session pooler** connection string, then
   `DATABASE_URL='<that URI>' kb_web/scripts/restore.sh kbcloud-YYYY-MM-DD.dump`, then `flask ... create-admin` against it.
2. GitHub secrets `DOCKERHUB_USERNAME`, `DOCKERHUB_TOKEN`: every push to `main` runs the tests, then builds and pushes
   `<user>/docsearch:latest` to Docker Hub.
3. Render: New → Web Service → *Existing image* `docker.io/<user>/docsearch:latest`; environment `SECRET_KEY`,
   `DATABASE_URL`; health check path `/healthz`. Put its deploy hook URL in the GitHub secret `RENDER_DEPLOY_HOOK`
   so CI redeploys after each push.

Secrets live only in `.env` (git-ignored), GitHub secrets and the Render dashboard.

## Notes and limits

- The cloud copy uses the MiniLM query embedder (smaller, fits the 512 MB free instance); the local system's default
  is granite, which scored higher on the project's benchmark.
- Render's free service sleeps after 15 minutes idle; the first request after that takes ~30–60 s.
