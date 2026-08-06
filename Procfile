# Heroku-style process model (Render Docker deploys use the Dockerfile CMD
# instead). One gunicorn worker with threads: the in-process progress bus and
# background run threads require a single process; threads serve concurrent SSE.
release: flask db upgrade
web: gunicorn autojob.wsgi:app --worker-class gthread --workers 1 --threads 8 --timeout 0 --bind 0.0.0.0:$PORT
