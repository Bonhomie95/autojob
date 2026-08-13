# AutoJob SaaS — common developer tasks.
# Backend lives in backend/, frontend in frontend/; the venv stays at the repo
# root since Python venvs aren't safely relocatable (their scripts embed an
# absolute path), so every backend command cd's into backend/ and reaches it
# via a relative ../venv.
PY ?= ../venv/bin/python
export APP_ENV ?= development

.PHONY: help install dev dev-full worker beat test lint fmt \
        frontend-install frontend-dev frontend-build frontend-preview

help:
	@echo "Backend (deploys to Render — see render.yaml):"
	@echo "  make install          - install backend dependencies"
	@echo "  make dev              - run the Flask API only"
	@echo "  make dev-full         - run backend + Vite dev server together, with frontend hot-reload"
	@echo "  make worker           - run a Celery worker"
	@echo "  make beat             - run the Celery beat scheduler"
	@echo "  make test             - run the backend test suite"
	@echo "  make lint             - ruff lint"
	@echo "Frontend (deploys to Vercel — see frontend/vercel.json):"
	@echo "  make frontend-install - install frontend dependencies"
	@echo "  make frontend-dev     - run the Vite dev server"
	@echo "  make frontend-build   - production build (output: frontend/dist), for local testing"

install:
	cd backend && $(PY) -m pip install -r requirements.txt

dev:
	cd backend && $(PY) manage.py run

# Runs backend (port 9000) + Vite dev server (port 5173) together. Vite
# proxies /api to the backend (see frontend/vite.config.ts), so open
# http://localhost:5173 — frontend edits hot-reload, no rebuild needed.
# Ctrl+C stops both.
dev-full:
	@trap 'kill 0' EXIT INT TERM; \
	(cd backend && $(PY) manage.py run) & \
	(cd frontend && npm run dev) & \
	wait

worker:
	cd backend && ../venv/bin/celery -A autojob.celery_app.celery worker --loglevel=info

beat:
	cd backend && ../venv/bin/celery -A autojob.celery_app.celery beat --loglevel=info

test:
	cd backend && APP_ENV=testing $(PY) -m pytest

lint:
	cd backend && ../venv/bin/ruff check autojob tests

fmt:
	cd backend && ../venv/bin/ruff format autojob tests

frontend-install:
	cd frontend && npm install

frontend-dev:
	cd frontend && npm run dev

frontend-build:
	cd frontend && npm run build

frontend-preview:
	cd frontend && npm run preview
