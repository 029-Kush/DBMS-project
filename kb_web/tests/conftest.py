"""Tests run against a real, empty PostgreSQL with pgvector (TEST_DATABASE_URL); the schema is created fresh each session.
The search engine itself is not loaded here (it needs the embedding model); its adapter is replaced where a test needs it."""
import os

import pytest
from sqlalchemy import text

from kb_web import create_app
from kb_web.models import User, db

URL = os.environ.get("TEST_DATABASE_URL")


@pytest.fixture(scope="session")
def app():
    if not URL:
        pytest.skip("TEST_DATABASE_URL not set")
    os.environ["DATABASE_URL"] = URL
    from kb_web.config import sqlalchemy_url
    app = create_app({"TESTING": True, "SECRET_KEY": "test-only", "SQLALCHEMY_DATABASE_URI": sqlalchemy_url(URL),
                      "WTF_CSRF_ENABLED": False, "SESSION_COOKIE_SECURE": False})
    with app.app_context():
        db.session.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public"))
        db.session.commit()
        app.test_cli_runner().invoke(args=["init-db", "--with-kb-schema"], catch_exceptions=False)
    yield app


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture(autouse=True)
def clean(app):
    yield
    with app.app_context():
        db.session.execute(text("TRUNCATE users, bookmarks, search_log, audit_log, entity_links, chunk_refs, bodies, entities "
                                "RESTART IDENTITY CASCADE"))
        db.session.commit()


def make_user(app, username, role="viewer", password="correct horse battery"):
    with app.app_context():
        u = User(username=username, email=f"{username}@example.com", role=role)
        u.set_password(password)
        db.session.add(u)
        db.session.commit()
        return u.id


def login(client, username, password="correct horse battery"):
    return client.post("/login", data={"username": username, "password": password})
