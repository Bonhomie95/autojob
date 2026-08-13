"""
Authentication API: registration, login, logout, session check.

Security properties:
- Passwords are hashed (werkzeug) — never stored or logged in plaintext.
- Login/registration are rate-limited to blunt credential-stuffing/brute force.
- The same generic "invalid email or password" is returned for both an unknown
  email and a wrong password, so the endpoint can't be used to enumerate which
  emails have accounts.
- CSRF is enforced on every state-changing request via Flask-WTF's header
  check (GET /csrf hands the SPA a token; see extensions.py).
- New users get a UserSettings row immediately, so the rest of the app can
  assume settings always exist.

This is a JSON API — there's no server-rendered login/register page. The React
SPA owns that UI and talks to these endpoints directly.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime

from email_validator import EmailNotValidError, validate_email
from flask import Blueprint, jsonify, request
from flask_login import current_user, login_required, login_user, logout_user
from flask_wtf.csrf import generate_csrf

from ..extensions import limiter, login_manager
from ..services import repository as repo

logger = logging.getLogger(__name__)

auth_bp = Blueprint("auth", __name__, url_prefix="/api/auth")


@login_manager.user_loader
def load_user(user_id: str):
    return repo.get_user_by_id(user_id)


def _serialize_user(user) -> dict:
    return {
        "id": user.id,
        "email": user.email,
        "name": user.name,
        "plan": user.plan,
        "isAdmin": user.is_admin,
        "emailVerified": user.email_verified,
    }


def _is_valid_email(email: str) -> bool:
    try:
        validate_email(email, check_deliverability=False)
        return True
    except EmailNotValidError:
        return False


def _validate_register(data: dict) -> dict[str, str]:
    """Return {field: message} for the first problem found per field."""
    errors: dict[str, str] = {}
    name = (data.get("name") or "").strip()
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""
    confirm = data.get("confirm") or ""

    if not name:
        errors["name"] = "Full name is required."
    elif len(name) > 120:
        errors["name"] = "Full name is too long."

    if not email:
        errors["email"] = "Email is required."
    elif len(email) > 255 or not _is_valid_email(email):
        errors["email"] = "Enter a valid email."

    if not password:
        errors["password"] = "Password is required."
    elif len(password) < 8 or len(password) > 128:
        errors["password"] = "Use at least 8 characters."
    elif not re.search(r"[A-Za-z]", password):
        errors["password"] = "Include at least one letter."
    elif not re.search(r"\d", password):
        errors["password"] = "Include at least one number."

    if not confirm or confirm != password:
        errors["confirm"] = "Passwords must match."

    if not data.get("accept_terms"):
        errors["accept_terms"] = "You must accept the terms to continue."

    return errors


@auth_bp.get("/csrf")
def csrf_token():
    """
    The SPA fetches this once (no auth required) on load and sends the value
    back via the X-CSRFToken header on every state-changing request —
    Flask-WTF's CSRFProtect checks that header on its own, independent of forms.
    """
    return jsonify(csrfToken=generate_csrf())


@auth_bp.get("/me")
def me():
    """Session check — the SPA calls this on load to know if it's signed in."""
    if not current_user.is_authenticated:
        return jsonify(user=None)
    return jsonify(user=_serialize_user(current_user))


@auth_bp.post("/register")
@limiter.limit("10 per hour")
def register():
    if current_user.is_authenticated:
        return jsonify(user=_serialize_user(current_user))

    data = request.get_json(silent=True) or {}
    errors = _validate_register(data)
    if errors:
        return jsonify(errors=errors), 400

    email = (data.get("email") or "").strip().lower()
    if repo.get_user_by_email(email):
        return jsonify(
            errors={"email": "An account with that email already exists. Try signing in."}
        ), 409

    name = (data.get("name") or "").strip()
    user = repo.create_user(email, name, data.get("password"))

    # Guarantee a settings row exists from the very first request.
    repo.get_or_create_settings(user.id)

    login_user(user)
    logger.info("New user registered", extra={"user_id": user.id})
    return jsonify(user=_serialize_user(user)), 201


@auth_bp.post("/login")
@limiter.limit("20 per 15 minutes")
def login():
    if current_user.is_authenticated:
        return jsonify(user=_serialize_user(current_user))

    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""
    if not email or not password:
        return jsonify(error="Email and password are required."), 400

    user = repo.get_user_by_email(email)
    if user is None or not user.check_password(password):
        return jsonify(error="Invalid email or password."), 401
    if not user.is_active:
        return jsonify(error="This account is disabled. Contact support."), 403

    login_user(user, remember=bool(data.get("remember")))
    repo.update_user(user.id, last_login_at=datetime.now(UTC))
    logger.info("User logged in", extra={"user_id": user.id})
    return jsonify(user=_serialize_user(user))


@auth_bp.post("/logout")
@login_required
def logout():
    logout_user()
    return jsonify(status="logged_out")
