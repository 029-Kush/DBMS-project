"""Dashboard, search, entity browsing/editing and bookmarks."""
import logging
import time

from flask import Blueprint, abort, current_app, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import desc, func, text
from sqlalchemy.exc import IntegrityError

from . import kbsearch
from .auth import role_required
from .forms import ENTITY_TYPES, EntityForm, NoteForm, PassageForm
from .models import Bookmark, ChunkRef, Entity, SearchLog, db

bp = Blueprint("main", __name__)
log = logging.getLogger(__name__)
_stats = {"at": 0, "rows": []}


def source_stats():
    """v_source_stats scans every passage, so it is cached for 10 minutes."""
    if time.time() - _stats["at"] > 600:
        rows = db.session.execute(text("SELECT * FROM v_source_stats WHERE source <> ALL(:h) ORDER BY passages DESC"),
                                  {"h": list(kbsearch.HIDDEN_SOURCES)}).mappings().all()
        _stats.update(at=time.time(), rows=[dict(r) for r in rows])
    return _stats["rows"]


def visible_entities():
    """Entities with at least one passage from a visible source, or with none at all (added by hand)."""
    hidden = db.session.query(ChunkRef.entity_id).filter(ChunkRef.source.in_(kbsearch.HIDDEN_SOURCES))
    return db.session.query(Entity).filter(~Entity.id.in_(hidden))


@bp.route("/healthz")
def healthz():
    db.session.execute(text("SELECT 1"))
    return {"status": "ok"}


@bp.route("/")
@login_required
def dashboard():
    stats = source_stats()
    totals = {"passages": sum(r["passages"] for r in stats), "texts": sum(r["distinct_texts"] for r in stats),
              "entities": visible_entities().count()}
    week = text("now() - interval '7 days'")
    top = (db.session.query(SearchLog.query, func.count().label("n"), func.round(func.avg(SearchLog.latency_ms)).label("ms"))
           .filter(SearchLog.created_at > week).group_by(SearchLog.query).order_by(desc("n")).limit(8).all())
    daily = db.session.execute(text("""
        SELECT d::date AS day, count(s.id) AS n
        FROM generate_series(current_date - 13, current_date, interval '1 day') d
        LEFT JOIN search_log s ON s.created_at >= d AND s.created_at < d + interval '1 day'
        GROUP BY d ORDER BY d""")).all()
    mine = (db.session.query(SearchLog).filter_by(user_id=current_user.id)
            .order_by(SearchLog.created_at.desc()).limit(5).all())
    n_bookmarks = db.session.query(func.count(Bookmark.id)).filter_by(user_id=current_user.id).scalar()
    return render_template("dashboard.html", stats=stats, totals=totals, top=top, daily=daily, mine=mine,
                           n_bookmarks=n_bookmarks, max_day=max([d.n for d in daily] + [1]))


@bp.route("/search")
@login_required
def search():
    q = request.args.get("q", "").strip()
    source = request.args.get("source", "")
    page = request.args.get("page", 1, type=int)
    sources = [r["source"] for r in source_stats()]
    if source not in sources:
        source = ""
    if len(q) > 300:
        flash("Query is too long (300 characters at most).", "error")
        q = ""
    per, cap = current_app.config["SEARCH_PAGE"], current_app.config["SEARCH_MAX"]
    page = max(1, min(page, cap // per))
    hits, ms, error = [], 0, None
    if q:
        t0 = time.perf_counter()
        try:
            hits = kbsearch.run(q, limit=cap, source=source or None, sources=sources)
        except Exception:
            log.exception("search failed for %r", q)
            error = "The search engine is unavailable right now. Please try again in a minute."
        ms = int((time.perf_counter() - t0) * 1000)
        if page == 1 and error is None:
            db.session.add(SearchLog(user_id=current_user.id, query=q[:300], n_results=len(hits), latency_ms=ms))
            db.session.commit()
    marked = set()
    shown = hits[(page - 1) * per: page * per]
    if shown:
        marked = {b for (b,) in db.session.query(Bookmark.chunk_id).filter(
            Bookmark.user_id == current_user.id, Bookmark.chunk_id.in_([h[0] for h in shown]))}
    pages = (len(hits) + per - 1) // per
    return render_template("search.html", q=q, source=source, sources=sources, hits=shown, page=page, pages=pages,
                           total=len(hits), ms=ms, error=error, marked=marked, offset=(page - 1) * per)


@bp.route("/api/search")
@login_required
def api_search():
    q = request.args.get("q", "").strip()
    if not q or len(q) > 300:
        return jsonify(error="q is required (1-300 characters)"), 400
    limit = min(max(request.args.get("limit", 10, type=int), 1), 50)
    hits = kbsearch.run(q, limit=limit)
    return jsonify(query=q, results=[dict(chunk_id=h[0], entity=h[1], section=h[2], source=h[3], version=h[4],
                                          text=h[5], score=round(h[7], 5), lanes=h[8]) for h in hits])


@bp.route("/entities")
@login_required
def entities():
    q = request.args.get("q", "").strip()[:100]
    etype = request.args.get("type", "")
    page = request.args.get("page", 1, type=int)
    query = visible_entities()
    if q:   # bound parameter: the ORM never splices user text into SQL
        query = query.filter(Entity.name.ilike("%" + q + "%", escape="\\") if not any(c in q for c in "%_\\")
                             else Entity.name.contains(q, autoescape=True))
    if etype:
        query = query.filter(Entity.type == etype)
    types = [t for (t,) in db.session.query(Entity.type).distinct().order_by(Entity.type)]
    pg = query.order_by(Entity.name).paginate(page=page, per_page=current_app.config["PER_PAGE"], error_out=False)
    counts = dict(db.session.query(ChunkRef.entity_id, func.count()).filter(
        ChunkRef.entity_id.in_([e.id for e in pg.items])).group_by(ChunkRef.entity_id).all())
    return render_template("entities.html", pg=pg, q=q, etype=etype, types=types, counts=counts)


def get_visible_entity(entity_id):
    e = db.session.get(Entity, entity_id)
    if e is None or e.chunks.filter(ChunkRef.source.in_(kbsearch.HIDDEN_SOURCES)).first():
        abort(404)
    return e


@bp.route("/entities/<int:entity_id>")
@login_required
def entity(entity_id):
    e = get_visible_entity(entity_id)
    page = request.args.get("page", 1, type=int)
    pg = e.chunks.order_by(ChunkRef.id).paginate(page=page, per_page=10, error_out=False)
    return render_template("entity.html", e=e, pg=pg)


def fill_entity(e, form):
    e.name, e.type = form.name.data.strip(), form.type.data
    e.aliases = sorted({a.strip().lower() for a in (form.aliases.data or "").split(",") if a.strip()})
    e.version = (form.version.data or "").strip() or None
    e.summary = (form.summary.data or "").strip() or None


@bp.route("/entities/new", methods=["GET", "POST"])
@role_required("editor")
def entity_new():
    form = EntityForm()
    if form.validate_on_submit():
        e = Entity(domain=[], ext_id="", facts={})
        fill_entity(e, form)
        db.session.add(e)
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            flash("An entity with that name and type already exists.", "error")
        else:
            flash(f"Created {e.name}.", "ok")
            return redirect(url_for("main.entity", entity_id=e.id))
    return render_template("entity_form.html", form=form, e=None)


@bp.route("/entities/<int:entity_id>/edit", methods=["GET", "POST"])
@role_required("editor")
def entity_edit(entity_id):
    e = get_visible_entity(entity_id)
    form = EntityForm(obj=e)
    if e.type not in ENTITY_TYPES:   # keep types that came from ingestion selectable
        form.type.choices = [(e.type, e.type)] + form.type.choices
    if request.method == "GET":
        form.aliases.data = ", ".join(e.aliases or [])
    if form.validate_on_submit():
        fill_entity(e, form)
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            flash("An entity with that name and type already exists.", "error")
        else:
            flash("Saved.", "ok")
            return redirect(url_for("main.entity", entity_id=e.id))
    return render_template("entity_form.html", form=form, e=e)


@bp.route("/entities/<int:entity_id>/delete", methods=["POST"])
@role_required("admin")
def entity_delete(entity_id):
    e = get_visible_entity(entity_id)
    name = e.name
    db.session.delete(e)          # chunk_refs, links and bookmarks go with it (ON DELETE CASCADE)
    db.session.commit()
    flash(f"Deleted {name}.", "ok")
    return redirect(url_for("main.entities"))


@bp.route("/entities/<int:entity_id>/passages/new", methods=["GET", "POST"])
@role_required("editor")
def passage_new(entity_id):
    e = get_visible_entity(entity_id)
    form = PassageForm()
    if form.validate_on_submit():
        try:
            new = kbsearch.add_passage(e.id, e.name, form.section.data.strip(), form.text.data)
        except Exception:
            log.exception("adding a passage to entity %s failed", e.id)
            flash("Could not add the passage; nothing was saved.", "error")
        else:
            flash("Passage added and indexed." if new else "That passage already exists.", "ok")
            return redirect(url_for("main.entity", entity_id=e.id))
    return render_template("passage_form.html", form=form, e=e)


@bp.route("/bookmarks")
@login_required
def bookmarks():
    page = request.args.get("page", 1, type=int)
    pg = (db.session.query(Bookmark).filter_by(user_id=current_user.id)   # a user only ever sees their own rows
          .order_by(Bookmark.created_at.desc()).paginate(page=page, per_page=current_app.config["PER_PAGE"], error_out=False))
    return render_template("bookmarks.html", pg=pg, form=NoteForm())


def own_bookmark(bookmark_id):
    b = db.session.query(Bookmark).filter_by(id=bookmark_id, user_id=current_user.id).first()
    if b is None:
        abort(404)   # someone else's bookmark looks the same as a missing one
    return b


@bp.route("/bookmarks", methods=["POST"])
@login_required
def bookmark_add():
    form = NoteForm()
    chunk_id = request.form.get("chunk_id", type=int)
    if not form.validate_on_submit() or chunk_id is None or db.session.get(ChunkRef, chunk_id) is None:
        abort(400)
    db.session.add(Bookmark(user_id=current_user.id, chunk_id=chunk_id, note=form.note.data or None))
    try:
        db.session.commit()
        flash("Bookmarked.", "ok")
    except IntegrityError:
        db.session.rollback()
        flash("Already bookmarked.", "info")
    return redirect(request.referrer if request.referrer and request.referrer.startswith(request.host_url)
                    else url_for("main.bookmarks"))


@bp.route("/bookmarks/<int:bookmark_id>", methods=["POST"])
@login_required
def bookmark_edit(bookmark_id):
    b = own_bookmark(bookmark_id)
    form = NoteForm()
    if form.validate_on_submit():
        b.note = form.note.data or None
        db.session.commit()
        flash("Note saved.", "ok")
    else:
        flash("; ".join(form.note.errors) or "Invalid form.", "error")
    return redirect(url_for("main.bookmarks"))


@bp.route("/bookmarks/<int:bookmark_id>/delete", methods=["POST"])
@login_required
def bookmark_delete(bookmark_id):
    db.session.delete(own_bookmark(bookmark_id))
    db.session.commit()
    flash("Bookmark removed.", "ok")
    return redirect(url_for("main.bookmarks"))
