"""WSGI entry point: `gunicorn wsgi:app` (production) or `flask --app wsgi run` (dev)."""
from app import create_app

app = create_app()
