"""Django settings for the AVAIBE LinkedIn Intelligence backend.

All secrets and environment-specific values come from server/.env (see .env.example).
"""

import os
from pathlib import Path

import dj_database_url
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def env_bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def env_list(name: str, default: str = "") -> list[str]:
    return [v.strip() for v in os.getenv(name, default).split(",") if v.strip()]


DEBUG = env_bool("DJANGO_DEBUG", True)
SECRET_KEY = os.getenv("DJANGO_SECRET_KEY") or ("dev-only-insecure-key" if DEBUG else "")
if not SECRET_KEY:
    raise RuntimeError("DJANGO_SECRET_KEY must be set when DJANGO_DEBUG is false")

ALLOWED_HOSTS = env_list("ALLOWED_HOSTS", "localhost,127.0.0.1")
CORS_ORIGINS = env_list("CORS_ORIGINS", "http://localhost:5173")
# Browser origins allowed to send changes (the React app). In development, also the local demo copy.
CSRF_TRUSTED_ORIGINS = env_list("CSRF_TRUSTED_ORIGINS", ",".join(CORS_ORIGINS))
if DEBUG:
    CSRF_TRUSTED_ORIGINS += [o for o in ("http://localhost:5173", "http://localhost:5174") if o not in CSRF_TRUSTED_ORIGINS]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "core",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
# The React app calls /api/accounts (no trailing slash): don't redirect
APPEND_SLASH = False

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# DATABASE_URL e.g. postgresql://user:pass@localhost:5432/avaibe_django  (or sqlite:///path for tests)
_db_url = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/avaibe_django")
_db_url = _db_url.replace("postgresql+psycopg://", "postgresql://")  # accept the old SQLAlchemy-style URL too
DATABASES = {"default": dj_database_url.parse(_db_url, conn_max_age=60)}

AUTH_USER_MODEL = "core.User"
AUTHENTICATION_BACKENDS = ["django.contrib.auth.backends.ModelBackend"]

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# Sessions: httpOnly cookie, Lax (sent on the Microsoft redirect back, not on cross-site requests)
SESSION_DAYS = int(os.getenv("SESSION_DAYS", "7"))
SESSION_COOKIE_AGE = SESSION_DAYS * 86400
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
COOKIE_SECURE = env_bool("COOKIE_SECURE", False)  # true when served over HTTPS
# Hosting (Render) terminates HTTPS in front of Django and says so in this header
if env_bool("BEHIND_PROXY", False):
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SESSION_COOKIE_SECURE = COOKIE_SECURE
CSRF_COOKIE_SECURE = COOKIE_SECURE
CSRF_COOKIE_SAMESITE = "Lax"

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"  # everything is stored in UTC; UAE time is applied when displaying/analysing
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": ["core.auth.AppAuthentication"],
    "DEFAULT_PERMISSION_CLASSES": ["core.auth.IsSignedIn"],
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_PARSER_CLASSES": [
        "rest_framework.parsers.JSONParser",
        "rest_framework.parsers.MultiPartParser",
        "rest_framework.parsers.FormParser",
    ],
    "EXCEPTION_HANDLER": "core.api.exception_handler",
    # The API uses ?format= for the post format (image, video…); don't let DRF treat it as a renderer choice
    "URL_FORMAT_OVERRIDE": None,
    "UNAUTHENTICATED_USER": None,
}

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": "INFO"},
}
