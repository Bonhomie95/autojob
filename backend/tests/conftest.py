"""
Shared pytest fixtures.

Important: the ``client`` fixture must NOT run inside a held app context.
Flask-Login caches the current user on ``g``; if a single app context spans
multiple test-client requests, that cache leaks and an anonymous client looks
authenticated. So ``app`` yields with no context held (each client request
manages its own), while tests that touch the DB/repo directly depend on the
``db``/``app_context`` fixtures which push a context for the test body.

Each ``create_app(TestingConfig)`` call gets its own independent in-memory
mongomock database (``mongomock.MongoClient()`` instances don't share state
with each other), so there's no explicit setup/teardown needed the way
SQLite's ``create_all()``/``drop_all()`` used to require.
"""

from __future__ import annotations

import pytest

from autojob import create_app
from autojob.config import TestingConfig
from autojob.extensions import db as _db
from autojob.services import repository as repo


@pytest.fixture()
def app():
    application = create_app(TestingConfig)
    yield application


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
    def _make(email: str, password: str = "pw123456", **kw):
        name = kw.pop("name", email.split("@")[0])
        user = repo.create_user(email, name, password)
        if kw:
            repo.update_user(user.id, **kw)
            for key, value in kw.items():
                setattr(user, key, value)
        return user

    return _make
