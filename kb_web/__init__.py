"""VectorIT web app: Flask + SQLAlchemy front end for the PostgreSQL hybrid-search knowledge base."""
import gzip
import logging
import os
import threading
from pathlib import Path

import click
from flask import Flask, g, has_request_context, render_template
from flask_login import LoginManager
from flask_wtf.csrf import CSRFProtect
from sqlalchemy import event, text

from .config import Config
from .models import User, db

HERE = Path(__file__).resolve().parent
KB_SQL = HERE.parent / "knowledge_base" / "sql"
SEED = HERE / "seed" / "docs.sql.gz"   # built by scripts/export_seed.sh
login_manager = LoginManager()
csrf = CSRFProtect()


def create_app(overrides=None):
    app = Flask(__name__)
    app.config.from_object(Config)
    app.config.update(overrides or {})
    if not app.config.get("SECRET_KEY"):
        raise RuntimeError("SECRET_KEY is not set (see .env.example)")
    if not app.config.get("SQLALCHEMY_DATABASE_URI"):
        raise RuntimeError("DATABASE_URL is not set (see .env.example)")

    db.init_app(app)
    csrf.init_app(app)
    login_manager.init_app(app)
    login_manager.login_view = "auth.login"
    login_manager.login_message_category = "info"

    with app.app_context():
        # every transaction carries the logged-in username, so the audit trigger can record who changed a row
        @event.listens_for(db.session, "after_begin")
        def _tag_transaction(session, transaction, connection):
            # set by load_user; reading current_user here could query inside the BEGIN (expired attributes reload)
            name = g.get("kb_username", "") if has_request_context() else ""
            connection.execute(text("SELECT set_config('app.user', :u, true)"), {"u": name})

    from .admin import bp as admin_bp
    from .auth import bp as auth_bp
    from .explorer import bp as explorer_bp
    from .main import bp as main_bp
    csrf.exempt(explorer_bp)   # its POSTs are JSON-only (checked per view) and need login; SameSite=Lax cookies
    app.register_blueprint(explorer_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(main_bp)
    app.register_blueprint(admin_bp)

    for code in (400, 403, 404, 500):
        app.register_error_handler(code, lambda e, code=code: (render_template("error.html", code=code, error=e), code))

    @app.teardown_request
    def _rollback_on_error(exc):
        if exc is not None:
            db.session.rollback()

    register_cli(app)
    logging.basicConfig(level=logging.INFO)
    if os.environ.get("KB_WARMUP") == "1":   # load the embedding model and vector codes before the first user search
        threading.Thread(target=_warm_up, daemon=True).start()
    return app


def _warm_up():
    from . import explorer, kbsearch
    try:
        kbsearch.run("warm up", limit=1)
        explorer.state()
    except Exception:
        logging.getLogger(__name__).exception("search warm-up failed")


@login_manager.user_loader
def load_user(user_id):
    u = db.session.get(User, int(user_id))
    if u is not None:
        g.kb_username = u.username   # later transactions get it from _tag_transaction; this one is already open
        db.session.execute(text("SELECT set_config('app.user', :u, true)"), {"u": u.username})
    return u


def apply_sql(path):
    """Run one .sql file as a single transaction (psql-style: the whole file at once)."""
    raw = db.engine.raw_connection()
    try:
        with raw.cursor() as cur:
            cur.execute(Path(path).read_text())
        raw.commit()
    finally:
        raw.close()


def load_seed(cur, path):
    """Stream a gzipped `pg_dump --data-only` file (COPY format) into the database line by line, so memory stays flat
    (reading it whole needed ~480 MB, more than a free 512 MB instance has). COPY blocks go through cur.copy();
    every other line is a one-line SET/SELECT statement; psql meta-commands (\\restrict) are skipped."""
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        for line in fh:
            if line.startswith("COPY "):
                with cur.copy(line.rstrip().removesuffix(";").replace(" FROM stdin", " FROM STDIN")) as cp:
                    for row in fh:
                        if row == "\\.\n":
                            break
                        cp.write(row)
            elif line.strip() and not line.startswith(("--", "\\")):
                cur.execute(line)


def register_cli(app):
    @app.cli.command("init-db")
    @click.option("--with-kb-schema", is_flag=True, help="Also create the knowledge-base tables (empty database, tests/CI).")
    def init_db(with_kb_schema):
        """Create the app tables, trigger, procedure and views (idempotent)."""
        if with_kb_schema:
            for f in ("schema.sql", "002_links.sql", "003_vec_quant.sql", "004_lookup_indexes.sql", "005_term_df.sql"):
                apply_sql(KB_SQL / f)
        apply_sql(HERE / "sql" / "001_web.sql")
        click.echo("database ready")

    @app.cli.command("create-admin")
    @click.option("--username", prompt=True)
    @click.option("--email", prompt=True)
    @click.password_option()
    def create_admin(username, email, password):
        """Create an admin account (password is prompted, never passed in code)."""
        if len(password) < 10:
            raise click.ClickException("password must be at least 10 characters")
        u = User(username=username.lower(), email=email.lower(), role="admin")
        u.set_password(password)
        db.session.add(u)
        db.session.commit()
        click.echo(f"admin {u.username} created")

    @app.cli.command("bootstrap")
    def bootstrap():
        """First-start setup for a fresh hosted database, safe to run on every start: create every table, load the bundled
        seed data if the knowledge base is empty, and create the admin from ADMIN_USERNAME/ADMIN_EMAIL/ADMIN_PASSWORD
        if no admin exists yet."""
        for f in ("schema.sql", "002_links.sql", "003_vec_quant.sql", "004_lookup_indexes.sql", "005_term_df.sql"):
            apply_sql(KB_SQL / f)
        # the seed goes in before 001_web.sql creates the audit trigger, so it is not logged as thousands of edits
        empty = db.session.execute(text("SELECT NOT EXISTS (SELECT 1 FROM chunk_refs)")).scalar()
        db.session.rollback()   # hold no locks while the seed and the DDL below run on other connections
        if empty and SEED.exists():
            raw = db.engine.raw_connection()
            try:
                with raw.cursor() as cur:
                    load_seed(cur, SEED)
                    cur.execute("SET search_path = public")
                    cur.execute("SELECT setval('chunks_id_seq', greatest((SELECT max(id) FROM chunk_refs), (SELECT max(id) FROM bodies)))")
                    cur.execute("SELECT setval('entities_id_seq', (SELECT max(id) FROM entities))")
                    cur.execute("TRUNCATE term_df; INSERT INTO term_df SELECT word, ndoc FROM ts_stat('SELECT tsv FROM bodies')")
                raw.commit()   # all of the seed or none of it
            finally:
                raw.close()
            click.echo("seed data loaded")
        apply_sql(HERE / "sql" / "001_web.sql")
        name, email, pw = (os.environ.get(k, "") for k in ("ADMIN_USERNAME", "ADMIN_EMAIL", "ADMIN_PASSWORD"))
        if name and pw and not db.session.query(User.id).filter_by(role="admin").first():
            if len(pw) < 10:
                raise click.ClickException("ADMIN_PASSWORD must be at least 10 characters")
            u = User(username=name.lower(), email=(email or f"{name}@example.com").lower(), role="admin")
            u.set_password(pw)
            db.session.add(u)
            db.session.commit()
            click.echo(f"admin {u.username} created")
        click.echo("bootstrap done")
