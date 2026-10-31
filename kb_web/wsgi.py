"""Entry point: gunicorn kb_web.wsgi:app   |   flask --app kb_web.wsgi run / init-db / create-admin"""
from kb_web import create_app

app = create_app()
