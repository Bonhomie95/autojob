"""
WSGI entrypoint for production servers (Gunicorn/uWSGI).

    gunicorn "autojob.wsgi:app" --workers 4 --bind 0.0.0.0:9000

For local development you can still run ``flask --app autojob.wsgi run`` or use
the dev script in ``manage.py``.
"""

from autojob import create_app

app = create_app()
