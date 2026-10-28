from pathlib import Path
import os

BASE_DIR = Path(__file__).resolve().parent.parent

def load_env_file():
    env_file = BASE_DIR / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))

load_env_file()
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "development-only-change-before-deployment")
DEBUG = os.environ.get("DJANGO_DEBUG", "false").lower() == "true"
ALLOWED_HOSTS = [item.strip() for item in os.environ.get("DJANGO_ALLOWED_HOSTS", "127.0.0.1,localhost,JEET-PC").split(",") if item.strip()]
CSRF_TRUSTED_ORIGINS = [f"https://{host.lower()}:3000" for host in ALLOWED_HOSTS if host != "*"]
INSTALLED_APPS = [
    "django.contrib.admin", "django.contrib.auth", "django.contrib.contenttypes",
    "django.contrib.sessions", "django.contrib.messages", "django.contrib.staticfiles",
    "workbench.apps.WorkbenchConfig",
]
MIDDLEWARE = [
    "oraplay.host_middleware.PrivateNetworkHostMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.gzip.GZipMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "workbench.middleware.StudentSessionLimitMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "workbench.middleware.LoginAutofillPolicyMiddleware",
]
ROOT_URLCONF = "oraplay.urls"
TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "DIRS": [BASE_DIR, BASE_DIR / "templates", BASE_DIR / "online-oracle-compiler" / "templates"], "APP_DIRS": True,
    "OPTIONS": {"context_processors": [
        "django.template.context_processors.debug", "django.template.context_processors.request",
        "django.contrib.auth.context_processors.auth", "django.contrib.messages.context_processors.messages",
    ]},
}]
WSGI_APPLICATION = "oraplay.wsgi.application"
ASGI_APPLICATION = "oraplay.asgi.application"
if os.environ.get("LMS_DB_ENGINE", "sqlite").lower() == "oracle":
    DATABASES = {"default": {
        "ENGINE": "django.db.backends.oracle",
        "NAME": os.environ.get("LMS_ORACLE_DSN", ""),
        "USER": os.environ.get("LMS_ORACLE_USER", ""),
        "PASSWORD": os.environ.get("LMS_ORACLE_PASSWORD", ""),
        "HOST": "", "PORT": "",
        "CONN_MAX_AGE": 60,
        "CONN_HEALTH_CHECKS": True,
    }}
else:
    DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": BASE_DIR / "lms.sqlite3"}}
AUTH_USER_MODEL = "workbench.PortalUser"
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
LANGUAGE_CODE = "en-us"
TIME_ZONE = "Asia/Kolkata"
USE_I18N = True
USE_TZ = True
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "static-collected"
STATICFILES_DIRS = []
MEDIA_ROOT = BASE_DIR / "media"
MEDIA_URL = "/media/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
SESSION_COOKIE_NAME = "ora_session"
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Strict"
SESSION_COOKIE_AGE = 8 * 60 * 60
CSRF_COOKIE_NAME = "ora_csrf"
TLS_ENABLED = bool(os.environ.get("TLS_KEY_FILE") and os.environ.get("TLS_CERT_FILE"))
SESSION_COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "false").lower() == "true" or TLS_ENABLED
CSRF_COOKIE_SAMESITE = "Strict"
CSRF_COOKIE_SECURE = SESSION_COOKIE_SECURE
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {
        "csrf_console": {"class": "logging.StreamHandler"},
        "request_errors_console": {
            "class": "logging.StreamHandler",
            "level": "ERROR",
            "formatter": "request_error",
        },
    },
    "formatters": {
        "request_error": {
            "format": "%(asctime)s %(levelname)s %(name)s: %(message)s",
        },
    },
    "loggers": {
        "django.security.csrf": {
            "handlers": ["csrf_console"],
            "level": "WARNING",
            "propagate": False,
        },
        "django.request": {
            "handlers": ["request_errors_console"],
            "level": "ERROR",
            "propagate": False,
        },
    },
}
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"
