"""DocSearch web app: Flask + SQLAlchemy front end for the PostgreSQL hybrid-search knowledge base."""
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
    from .main import bp as main_bp
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
    from . import kbsearch
    try:
        kbsearch.run("warm up", limit=1)
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
