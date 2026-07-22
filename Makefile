# AutoJob SaaS — common developer tasks.
# Uses the project venv if present.
PY ?= venv/bin/python
export APP_ENV ?= development

.PHONY: help install dev worker beat test lint fmt migrate upgrade metrics

help:
	@echo "make install   - install dependencies"
	@echo "make dev       - run the web app (dev server)"
	@echo "make worker    - run a Celery worker"
	@echo "make beat      - run the Celery beat scheduler"
	@echo "make test      - run the test suite"
	@echo "make lint      - ruff lint"
	@echo "make upgrade   - apply DB migrations"
	@echo "make migrate m='msg' - generate a migration"

install:
	$(PY) -m pip install -r requirements.txt

dev:
	$(PY) manage.py run

worker:
	venv/bin/celery -A autojob.celery_app.celery worker --loglevel=info

beat:
	venv/bin/celery -A autojob.celery_app.celery beat --loglevel=info

test:
	APP_ENV=testing $(PY) -m pytest

lint:
	venv/bin/ruff check autojob tests

fmt:
	venv/bin/ruff format autojob tests

upgrade:
	FLASK_APP=autojob.wsgi:app venv/bin/flask db upgrade

migrate:
	FLASK_APP=autojob.wsgi:app venv/bin/flask db migrate -m "$(m)"
