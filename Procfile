# Heroku-style process model — an alternative to the Docker deploy (Dockerfile
# CMD) for a native Python runtime. Backend only; the frontend deploys
# separately (see frontend/vercel.json). One gunicorn worker with threads: the
# in-process progress bus and background run threads require a single
# process; threads serve concurrent SSE. Paths are relative to backend/ — set
# that as the app's root directory.
release: cd backend && python manage.py init-db
web: cd backend && gunicorn autojob.wsgi:app --worker-class gthread --workers 1 --threads 8 --timeout 0 --bind 0.0.0.0:$PORT
