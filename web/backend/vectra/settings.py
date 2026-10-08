"""Django API settings for the existing self-healing PostgreSQL database."""
import os
from pathlib import Path

from dotenv import load_dotenv
from psycopg2.extensions import parse_dsn

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR.parents[1] / ".env")
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "local-development-only-change-me")
DEBUG = os.environ.get("DJANGO_DEBUG", "false").lower() in ("1", "true", "yes")
ALLOWED_HOSTS = os.environ.get("DJANGO_ALLOWED_HOSTS", "127.0.0.1,localhost").split(",")

INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.postgres",
    "rest_framework",
    "knowledge",
]
MIDDLEWARE = ["django.middleware.security.SecurityMiddleware"]
ROOT_URLCONF = "vectra.urls"
TEMPLATES = []
WSGI_APPLICATION = "vectra.wsgi.application"

dsn = parse_dsn(os.environ.get("SELFHEAL_DSN", "dbname=selfheal user=svuser host=127.0.0.1 port=5432"))
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": dsn.get("dbname", "selfheal"),
        "USER": dsn.get("user", "svuser"),
        "PASSWORD": dsn.get("password", ""),
        "HOST": dsn.get("host", "127.0.0.1"),
        "PORT": dsn.get("port", "5432"),
        "CONN_MAX_AGE": 0,
    }
}

REST_FRAMEWORK = {
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_THROTTLE_CLASSES": ["rest_framework.throttling.AnonRateThrottle"],
    "DEFAULT_THROTTLE_RATES": {"anon": "120/min"},
}
USE_TZ = True
TIME_ZONE = "Asia/Kolkata"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
