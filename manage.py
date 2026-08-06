"""
Developer entrypoint for the AutoJob SaaS app.

    python manage.py run          # start the dev server
    python manage.py routes       # list registered routes
    python manage.py init-db      # safe migrate/bootstrap (used at deploy boot)

Production uses Gunicorn against ``autojob.wsgi:app`` instead — never this.
"""

from __future__ import annotations

import os
import sys

from autojob import create_app

app = create_app()


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    if cmd == "run":
        # Honour a harness/orchestrator-assigned PORT first, then FLASK_PORT.
        port = int(os.getenv("PORT") or os.getenv("FLASK_PORT") or "9000")
        app.run(host="127.0.0.1", port=port, debug=app.config.get("DEBUG", False))
    elif cmd == "routes":
        for rule in sorted(app.url_map.iter_rules(), key=lambda r: r.rule):
            methods = ",".join(sorted(rule.methods - {"HEAD", "OPTIONS"}))
            print(f"{rule.rule:30s} [{methods}] -> {rule.endpoint}")
    elif cmd in ("init-db", "init_db"):
        from autojob.db_bootstrap import bootstrap_database

        with app.app_context():
            result = bootstrap_database()
        print(f"Database {result}.")
    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)


if __name__ == "__main__":
    main()
