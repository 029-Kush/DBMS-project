from sqlalchemy import text

from kb_web import kbsearch
from kb_web.models import AuditLog, Bookmark, ChunkRef, Entity, SearchLog, User, db

from .conftest import login, make_user


def add_entity_with_passage(app, name="tar", body="tar -xf archive.tar -C dir   extract into dir"):
    """A minimal entity + body + passage, written with SQL as the ingest scripts would."""
    with app.app_context():
        e = Entity(name=name, type="tool", domain=[], aliases=[], ext_id="", facts={})
        db.session.add(e); db.session.flush()
        bid = db.session.execute(text("INSERT INTO bodies(id, hash, body) VALUES (nextval('chunks_id_seq'), md5(:b)::bytea, :b) "
                                      "RETURNING id"), {"b": body}).scalar()
        cid = db.session.execute(text("INSERT INTO chunk_refs(entity_id, section, source, content_hash, model_version, body_id) "
                                      "VALUES (:e, 'examples', 'tldr', md5(:b || :e)::bytea, 'test', :bid) RETURNING id"),
                                 {"e": e.id, "b": body, "bid": bid}).scalar()
        db.session.commit()
        return e.id, cid


def test_register_hashes_password_and_makes_viewer(app, client):
    r = client.post("/register", data={"username": "alice", "email": "alice@example.com",
                                       "password": "a long password", "confirm": "a long password"})
    assert r.status_code == 302
    with app.app_context():
        u = db.session.query(User).filter_by(username="alice").one()
        assert u.role == "viewer"
        assert u.password_hash.startswith("$2b$12$") and "a long password" not in u.password_hash


def test_register_validation(client):
    r = client.post("/register", data={"username": "Bad Name!", "email": "nope", "password": "short", "confirm": "x"})
    assert r.status_code == 200
    assert b"lowercase letters" in r.data and b"valid email" in r.data and b"10-72 characters" in r.data


def test_login_wrong_password_and_disabled_account(app, client):
    make_user(app, "bob")
    assert b"Wrong username or password" in login(client, "bob", "wrong password!").data
    with app.app_context():
        db.session.query(User).filter_by(username="bob").update({"active": False}); db.session.commit()
    assert b"Wrong username or password" in login(client, "bob").data


def test_pages_need_login(client):
    for path in ("/", "/search?q=x", "/entities", "/bookmarks", "/admin/users"):
        r = client.get(path)
        assert r.status_code == 302 and "/login" in r.headers["Location"], path


def test_rbac(app, client):
    eid, _ = add_entity_with_passage(app)
    make_user(app, "vic"); make_user(app, "eddie", "editor"); make_user(app, "ada", "admin")
    login(client, "vic")
    assert client.get("/entities/new").status_code == 403
    assert client.post(f"/entities/{eid}/delete").status_code == 403
    assert client.get("/admin/users").status_code == 403
    client.post("/logout")
    login(client, "eddie")
    assert client.post("/entities/new", data={"name": "rsync", "type": "tool"}).status_code == 302
    assert client.post(f"/entities/{eid}/delete").status_code == 403
    client.post("/logout")
    login(client, "ada")
    assert client.post(f"/entities/{eid}/delete").status_code == 302
    with app.app_context():
        assert db.session.get(Entity, eid) is None
        assert db.session.query(ChunkRef).filter_by(entity_id=eid).count() == 0   # ON DELETE CASCADE


def test_audit_trigger_records_app_user_and_hides_hash(app, client):
    make_user(app, "eddie", "editor")
    login(client, "eddie")
    client.post("/entities/new", data={"name": "rsync", "type": "tool", "summary": "fast copy"})
    with app.app_context():
        a = db.session.query(AuditLog).filter_by(table_name="entities").one()
        assert (a.action, a.actor, a.new_row["name"]) == ("INSERT", "eddie", "rsync")
        assert all("password_hash" not in (r.new_row or {}) for r in db.session.query(AuditLog).filter_by(table_name="users"))


def test_bookmarks_are_private(app, client):
    _, cid = add_entity_with_passage(app)
    make_user(app, "amy"); make_user(app, "ben")
    login(client, "amy")
    client.post("/bookmarks", data={"chunk_id": cid, "note": "useful"})
    with app.app_context():
        bid = db.session.query(Bookmark.id).scalar()
    client.post("/logout"); login(client, "ben")
    assert client.post(f"/bookmarks/{bid}/delete").status_code == 404
    assert b"useful" not in client.get("/bookmarks").data
    client.post("/logout"); login(client, "amy")
    assert b"useful" in client.get("/bookmarks").data
    assert client.post(f"/bookmarks/{bid}/delete").status_code == 302
    with app.app_context():
        assert db.session.query(Bookmark).count() == 0


def test_merge_procedure(app, client):
    a, cid = add_entity_with_passage(app, "tar", "tar body one long enough")
    b, _ = add_entity_with_passage(app, "gtar", "gtar body two long enough")
    make_user(app, "ada", "admin")
    login(client, "ada")
    assert client.post("/admin/merge", data={"src": b, "dst": a}).status_code == 302
    with app.app_context():
        assert db.session.get(Entity, b) is None
        assert db.session.query(ChunkRef).filter_by(entity_id=a).count() == 2
        assert "gtar" in db.session.get(Entity, a).aliases
    r = client.post("/admin/merge", data={"src": a, "dst": a}, follow_redirects=True)
    assert b"cannot merge an entity into itself" in r.data


def test_search_logs_and_injection_text_is_inert(app, client, monkeypatch):
    _, cid = add_entity_with_passage(app)
    monkeypatch.setattr(kbsearch, "run", lambda q, limit, source=None, sources=(): [
        (cid, "tar", "examples", "tldr", None, "tar -xf archive.tar", [], 0.05, ["vec"])])
    make_user(app, "vic"); login(client, "vic")
    q = "x'; DROP TABLE users; --"
    assert client.get("/search", query_string={"q": q}).status_code == 200
    assert client.get("/entities", query_string={"q": q}).status_code == 200
    with app.app_context():
        assert db.session.query(SearchLog).one().query == q
        assert db.session.query(User).count() == 1


def test_dashboard_renders(app, client):
    add_entity_with_passage(app)
    make_user(app, "vic"); login(client, "vic")
    r = client.get("/")
    assert r.status_code == 200 and b"Passages by source" in r.data
