"""Settings come only from environment variables (see .env.example); nothing secret lives in the code."""
import os


def sqlalchemy_url(url):
    """postgres://... (Supabase/Render style) -> the psycopg 3 driver SQLAlchemy should use."""
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY")
    DATABASE_URL = os.environ.get("DATABASE_URL", "")
    SQLALCHEMY_DATABASE_URI = sqlalchemy_url(DATABASE_URL)
    # small pool: free hosted Postgres tiers allow few connections
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True, "pool_size": 3, "max_overflow": 2, "pool_recycle": 280}
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = os.environ.get("SESSION_COOKIE_SECURE", "1") == "1"   # 0 only for local http
    REMEMBER_COOKIE_HTTPONLY = True
    WTF_CSRF_TIME_LIMIT = None
    PER_PAGE = 20
    SEARCH_PAGE = 10
    SEARCH_MAX = 50          # results ranked per query (5 pages)
