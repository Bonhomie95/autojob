"""
Flask extension singletons, instantiated but unbound.

Each extension is created here once and bound to the application inside
``create_app()`` via ``init_app``. Keeping them in their own module avoids
circular imports: models import ``db`` from here, and the app factory imports
both.
"""

from __future__ import annotations

from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_login import LoginManager
from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy
from flask_wtf import CSRFProtect

# Database ORM + migrations
db = SQLAlchemy()
migrate = Migrate()

# Authentication / session management (Phase 3)
login_manager = LoginManager()
login_manager.login_view = "auth.login"
login_manager.login_message_category = "warning"
login_manager.session_protection = "strong"

# CSRF protection for all state-changing form/JSON POSTs (Phase 3)
csrf = CSRFProtect()

# Per-IP / per-user rate limiting (Phase 3)
limiter = Limiter(key_func=get_remote_address)
