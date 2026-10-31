"""Session login (Flask-Login), registration, and the role check used by every protected view."""
from datetime import datetime, timezone
from functools import wraps
from urllib.parse import urlparse

import bcrypt
from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user
from sqlalchemy import func, or_

from .forms import LoginForm, RegisterForm
from .models import User, db

bp = Blueprint("auth", __name__)
# compared against when the username does not exist, so a wrong username costs the same time as a wrong password
_DUMMY_HASH = bcrypt.hashpw(b"not-a-password", bcrypt.gensalt(rounds=12)).decode()


def role_required(role):
    """Logged in AND at least `role` (viewer < editor < admin), else 403."""
    def deco(view):
        @wraps(view)
        @login_required
        def wrapped(*a, **kw):
            if not current_user.can(role):
                abort(403)
            return view(*a, **kw)
        return wrapped
    return deco


@bp.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("explorer.home"))
    form = RegisterForm()
    if form.validate_on_submit():
        username, email = form.username.data.lower(), form.email.data.strip().lower()
        taken = db.session.query(User.id).filter(or_(User.username == username, func.lower(User.email) == email)).first()
        if taken:
            flash("That username or email is already registered.", "error")
        else:
            u = User(username=username, email=email, role="viewer")   # new accounts are always viewers
            u.set_password(form.password.data)
            db.session.add(u)
            db.session.commit()
            flash("Account created. Please log in.", "ok")
            return redirect(url_for("auth.login"))
    return render_template("register.html", form=form)


@bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("explorer.home"))
    form = LoginForm()
    if form.validate_on_submit():
        u = db.session.query(User).filter_by(username=form.username.data.strip().lower()).first()
        ok = u.check_password(form.password.data) if u else User(password_hash=_DUMMY_HASH).check_password(form.password.data)
        if ok and u.is_active:
            u.last_login = datetime.now(timezone.utc)
            db.session.commit()
            login_user(u)
            nxt = request.args.get("next", "")
            # only same-site paths: an absolute URL here would be an open redirect
            if not nxt.startswith("/") or nxt.startswith("//") or urlparse(nxt).netloc:
                nxt = url_for("explorer.home")
            return redirect(nxt)
        flash("Wrong username or password, or the account is disabled.", "error")
    return render_template("login.html", form=form)


@bp.route("/logout", methods=["POST"])
@login_required
def logout():
    logout_user()
    flash("Logged out.", "ok")
    return redirect(url_for("auth.login"))
