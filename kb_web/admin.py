"""Admin-only pages: user roles, account status, entity merge (stored procedure) and the audit log."""
from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from .auth import role_required
from .forms import MergeForm, RoleForm
from .models import AuditLog, User, db

bp = Blueprint("admin", __name__, url_prefix="/admin")


@bp.route("/users")
@role_required("admin")
def users():
    rows = db.session.execute(text("SELECT * FROM v_user_activity ORDER BY created_at")).mappings().all()
    return render_template("admin_users.html", rows=rows, form=RoleForm())


def other_user(user_id):
    u = db.session.get(User, user_id) or abort(404)
    if u.id == current_user.id:   # an admin cannot demote or lock themselves out
        flash("You cannot change your own account here.", "error")
        return None
    return u


@bp.route("/users/<int:user_id>/role", methods=["POST"])
@role_required("admin")
def set_role(user_id):
    form = RoleForm()
    u = other_user(user_id)
    if u and form.validate_on_submit():
        u.role = form.role.data
        db.session.commit()
        flash(f"{u.username} is now {u.role}.", "ok")
    return redirect(url_for("admin.users"))


@bp.route("/users/<int:user_id>/active", methods=["POST"])
@role_required("admin")
def toggle_active(user_id):
    u = other_user(user_id)
    if u:   # the CSRF token is checked by CSRFProtect before any POST view runs
        u.active = not u.active
        db.session.commit()
        flash(f"{u.username} {'enabled' if u.active else 'disabled'}.", "ok")
    return redirect(url_for("admin.users"))


@bp.route("/merge", methods=["GET", "POST"])
@role_required("admin")
def merge():
    form = MergeForm()
    if form.validate_on_submit():
        try:
            db.session.execute(text("CALL merge_entities(:s, :d)"), {"s": form.src.data, "d": form.dst.data})
            db.session.commit()
        except DBAPIError as exc:
            db.session.rollback()     # the procedure ran in this transaction: nothing of it is kept
            flash(f"Merge failed: {getattr(exc.orig, 'diag', None) and exc.orig.diag.message_primary or exc}", "error")
        else:
            flash(f"Entity {form.src.data} merged into {form.dst.data}.", "ok")
            return redirect(url_for("main.entity", entity_id=form.dst.data))
    return render_template("merge.html", form=form)


@bp.route("/audit")
@role_required("admin")
def audit():
    page = request.args.get("page", 1, type=int)
    table = request.args.get("table", "")
    q = db.session.query(AuditLog)
    if table in ("entities", "users"):
        q = q.filter(AuditLog.table_name == table)
    pg = q.order_by(AuditLog.changed_at.desc(), AuditLog.id.desc()).paginate(page=page, per_page=30, error_out=False)
    return render_template("audit.html", pg=pg, table=table)
