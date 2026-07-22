"""
Authentication: registration, login, logout.

Security properties:
- Passwords are hashed (werkzeug) — never stored or logged in plaintext.
- Login/registration are rate-limited to blunt credential-stuffing/brute force.
- The same generic "invalid email or password" is returned for both an unknown
  email and a wrong password, so the endpoint can't be used to enumerate which
  emails have accounts.
- CSRF is enforced on every POST via Flask-WTF.
- New users get a UserSettings row immediately, so the rest of the app can
  assume settings always exist.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from flask import (
    Blueprint,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user, login_required, login_user, logout_user

from ..extensions import db, limiter, login_manager
from ..forms import LoginForm, RegisterForm
from ..models import User
from ..services import repository as repo

logger = logging.getLogger(__name__)

auth_bp = Blueprint("auth", __name__, url_prefix="/auth")


@login_manager.user_loader
def load_user(user_id: str):
    return db.session.get(User, user_id)


def _safe_next(target: str | None) -> str:
    """Only allow same-site relative redirects — never an absolute URL."""
    if target and target.startswith("/") and not target.startswith("//"):
        return target
    return url_for("main.dashboard")


@auth_bp.route("/register", methods=["GET", "POST"])
@limiter.limit("10 per hour", methods=["POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))

    form = RegisterForm()
    if form.validate_on_submit():
        email = form.email.data.strip().lower()
        if db.session.scalar(db.select(User).filter_by(email=email)):
            flash("An account with that email already exists. Try signing in.", "error")
            return render_template("auth/register.html", form=form), 409

        user = User(email=email, name=form.name.data.strip())
        user.set_password(form.password.data)
        db.session.add(user)
        db.session.commit()

        # Guarantee a settings row exists from the very first request.
        repo.get_or_create_settings(user.id)

        login_user(user)
        logger.info("New user registered", extra={"user_id": user.id})
        flash("Welcome to AutoJob — let's get your CV working for you.", "success")
        return redirect(url_for("main.dashboard"))

    return render_template("auth/register.html", form=form)


@auth_bp.route("/login", methods=["GET", "POST"])
@limiter.limit("20 per 15 minutes", methods=["POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))

    form = LoginForm()
    if form.validate_on_submit():
        email = form.email.data.strip().lower()
        user = db.session.scalar(db.select(User).filter_by(email=email))
        if user is None or not user.check_password(form.password.data):
            flash("Invalid email or password.", "error")
            return render_template("auth/login.html", form=form), 401
        if not user.is_active:
            flash("This account is disabled. Contact support.", "error")
            return render_template("auth/login.html", form=form), 403

        login_user(user, remember=form.remember.data)
        user.last_login_at = datetime.now(UTC)
        db.session.commit()
        logger.info("User logged in", extra={"user_id": user.id})
        return redirect(_safe_next(request.args.get("next")))

    return render_template("auth/login.html", form=form)


@auth_bp.post("/logout")
@login_required
def logout():
    logout_user()
    flash("You've been signed out.", "success")
    return redirect(url_for("main.landing"))
