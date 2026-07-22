"""
Shared pytest fixtures.

Important: the ``client`` fixture must NOT run inside a held app context.
Flask-Login caches the current user on ``g``; if a single app context spans
multiple test-client requests, that cache leaks and an anonymous client looks
authenticated. So ``app`` yields with no context held (each client request
manages its own), while tests that touch the DB/repo directly depend on the
``db``/``app_context`` fixtures which push a context for the test body.
"""

from __future__ import annotations

import pytest

from autojob import create_app
from autojob.config import TestingConfig
from autojob.extensions import db as _db
from autojob.models import User


@pytest.fixture()
def app():
    application = create_app(TestingConfig)
    with application.app_context():
        _db.create_all()
    yield application
    with application.app_context():
        _db.session.remove()
        _db.drop_all()


@pytest.fixture()
def app_context(app):
    with app.app_context():
        yield app


@pytest.fixture()
def db(app_context):
    return _db


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def make_user(db):
    def _make(email: str, password: str = "pw123456", **kw) -> User:
        u = User(email=email, name=kw.pop("name", email.split("@")[0]), **kw)
        u.set_password(password)
        db.session.add(u)
        db.session.commit()
        return u

    return _make
