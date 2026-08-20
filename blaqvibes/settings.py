from pathlib import Path
import os
import sys
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / '.env')

# Fail closed: DEBUG defaults OFF. Enable it explicitly with DEBUG=1 in dev.
DEBUG = os.getenv('DEBUG', '0') == '1'

# Detect local development in a virtual environment or debug mode.
# This avoids forcing production-only HTTPS handling when running locally.
LOCAL_DEV = bool(os.getenv('VIRTUAL_ENV') or os.getenv('PYTHONENV') or os.getenv('DJANGO_LOCAL_DEV') or DEBUG)

# Sentry — crush silently, backend only, no JS secrets
try:
    import sentry_sdk
    from sentry_sdk.integrations.django import DjangoIntegration
    _dsn = os.getenv('SENTRY_DSN', '')
    if _dsn:
        sentry_sdk.init(dsn=_dsn, integrations=[DjangoIntegration()], traces_sample_rate=0.2, send_default_pii=False)
    else:
        sentry_sdk.init(traces_sample_rate=0.0)  # no DSN → silent, no network
except Exception:
    pass  # crush silently

SECRET_KEY = os.getenv('SECRET_KEY', '')
if not SECRET_KEY:
    if LOCAL_DEV:
        SECRET_KEY = 'django-insecure-blaqvibes-dev-key-change-in-prod-07070A'
    else:
        # Never boot production (DEBUG=0) without a real key.
        raise RuntimeError('SECRET_KEY must be set. Add SECRET_KEY to your .env.')

_raw_hosts = os.getenv('ALLOWED_HOSTS', 'localhost,127.0.0.1,0.0.0.0')
ALLOWED_HOSTS = [h.strip() for h in _raw_hosts.split(',') if h.strip()]
if '*' in ALLOWED_HOSTS and not (DEBUG or LOCAL_DEV):
    ALLOWED_HOSTS = ['blaqvibes.co.za', 'www.blaqvibes.co.za']
if DEBUG or LOCAL_DEV:
    for extra in ('localhost', '127.0.0.1', '0.0.0.0', '.e2b.app', '.localhost'):
        if extra not in ALLOWED_HOSTS:
            ALLOWED_HOSTS.append(extra)

CSRF_TRUSTED_ORIGINS = [
    origin.strip() for origin in os.getenv(
        'CSRF_TRUSTED_ORIGINS',
        'https://*.e2b.app,https://blaqvibes.co.za,https://www.blaqvibes.co.za',
    ).split(',') if origin.strip()
]
if LOCAL_DEV or DEBUG:
    CSRF_TRUSTED_ORIGINS += ['http://localhost:8000', 'http://127.0.0.1:8000', 'https://*.e2b.app']

# Canonical public origin — used for emails, sitemap, Paystack callback.
# Override via env (e.g. a custom domain) instead of hardcoding URLs in views/tasks.
SITE_URL = os.getenv('SITE_URL', 'https://blaqvibes.co.za').rstrip('/')

# Paystack — real card checkout only when a secret key is present.
# Leave blank to run the stars path alone. We never fake a charge or a bank payout.
PAYSTACK_SECRET_KEY = os.getenv('PAYSTACK_SECRET_KEY', '').strip()
PAYSTACK_ENABLED = bool(PAYSTACK_SECRET_KEY)
# Optional Nolo backends. Claude is used only when this key is set — never faked.
ANTHROPIC_API_KEY = os.getenv('ANTHROPIC_API_KEY', '').strip()
ANTHROPIC_MODEL = os.getenv('ANTHROPIC_MODEL', 'claude-3-5-haiku-latest')

# Seed the demo catalog when the published grid is empty (local / CI).
# Production stays empty until you run `python manage.py seed_demo` or set SEED_DEMO=1.
# Tests stay empty unless a test calls seed_demo() itself.
TESTING = any(arg == 'test' for arg in sys.argv) or os.getenv('DJANGO_TEST') == '1'
SEED_DEMO = os.getenv('SEED_DEMO', '1' if (LOCAL_DEV and not TESTING) else '0') == '1'
if TESTING:
    # A test run must start from an empty grid even when .env exports
    # SEED_DEMO=1 for dev: the post_migrate hook fires while the test
    # database is being built — before any override_settings applies — and
    # the suite asserts exact catalog counts (SeedDemoTests,
    # DiscoveryFeedTests). Tests that want the catalog call seed_demo().
    SEED_DEMO = False

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django.contrib.sites',
    'allauth',
    'allauth.account',
    'allauth.socialaccount',
    'allauth.socialaccount.providers.google',
    'allauth.socialaccount.providers.github',
    'allauth.socialaccount.providers.facebook',
    'gallery.apps.GalleryConfig',
    'users',
]

SITE_ID = int(os.getenv('SITE_ID', '1'))

GOOGLE_CLIENT_ID = os.getenv('GOOGLE_CLIENT_ID', '')
GOOGLE_CLIENT_SECRET = os.getenv('GOOGLE_CLIENT_SECRET', '')
GITHUB_CLIENT_ID = os.getenv('GITHUB_CLIENT_ID', '')
GITHUB_CLIENT_SECRET = os.getenv('GITHUB_CLIENT_SECRET', '')
FACEBOOK_CLIENT_ID = os.getenv('FACEBOOK_CLIENT_ID', '')
FACEBOOK_CLIENT_SECRET = os.getenv('FACEBOOK_CLIENT_SECRET', '')

AUTHENTICATION_BACKENDS = [
    'django.contrib.auth.backends.ModelBackend',
    'allauth.account.auth_backends.AuthenticationBackend',
]

ACCOUNT_ADAPTER = 'users.adapters.BlaqAccountAdapter'
SOCIALACCOUNT_ADAPTER = 'users.adapters.BlaqSocialAccountAdapter'
# allauth >= 65 names: SIGNUP_FIELDS (email* = required) and LOGIN_METHODS.
ACCOUNT_SIGNUP_FIELDS = ['email*', 'username*', 'password1*', 'password2*']
ACCOUNT_LOGIN_METHODS = {'username', 'email'}
ACCOUNT_EMAIL_VERIFICATION = 'optional'
ACCOUNT_UNIQUE_EMAIL = True
SOCIALACCOUNT_AUTO_SIGNUP = True
SOCIALACCOUNT_EMAIL_AUTHENTICATION = True
SOCIALACCOUNT_EMAIL_AUTHENTICATION_AUTO_CONNECT = True
SOCIALACCOUNT_LOGIN_ON_GET = False
SOCIALACCOUNT_QUERY_EMAIL = True

_social_providers = {}
if GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET:
    _social_providers['google'] = {
        'APP': {'client_id': GOOGLE_CLIENT_ID, 'secret': GOOGLE_CLIENT_SECRET, 'key': ''},
        'SCOPE': ['profile', 'email'],
        'AUTH_PARAMS': {'access_type': 'online'},
    }
if GITHUB_CLIENT_ID and GITHUB_CLIENT_SECRET:
    _social_providers['github'] = {
        'APP': {'client_id': GITHUB_CLIENT_ID, 'secret': GITHUB_CLIENT_SECRET, 'key': ''},
        'SCOPE': ['read:user', 'user:email'],
    }
if FACEBOOK_CLIENT_ID and FACEBOOK_CLIENT_SECRET:
    _social_providers['facebook'] = {
        'APP': {'client_id': FACEBOOK_CLIENT_ID, 'secret': FACEBOOK_CLIENT_SECRET, 'key': ''},
        'METHOD': 'oauth2',
        'SCOPE': ['email', 'public_profile'],
        'VERIFIED_EMAIL': True,
    }
SOCIALACCOUNT_PROVIDERS = _social_providers

# --- QUEUE: Celery + Redis — 5 Whys: Why queue not sync? ---
# 1. 10 users upload at same second → sync would timeout workers, drop scans. Queue serializes.
# 2. Why Redis not DB? Redis is in-memory, 10x faster for 100 jobs/sec, no DB lock.
# 3. Why separate queues? scan queue (slow, 1 worker) vs default (fast). Prevents starvation.
# 4. Why acks_late + prefetch 1? Worker crash mid-scan → job requeued, not lost.
# 5. Why eager fallback? Dev has no Redis, but .delay() must still work (eager sync).
CELERY_BROKER_URL = os.getenv('REDIS_URL', 'redis://localhost:6379/0')
CELERY_RESULT_BACKEND = os.getenv('REDIS_URL', 'redis://localhost:6379/0')
CELERY_TASK_ALWAYS_EAGER = os.getenv('CELERY_EAGER', '1') == '1'
CELERY_TASK_EAGER_PROPAGATES = True
CELERY_TASK_ACKS_LATE = True
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
CELERY_TASK_ROUTES = {
    'gallery.tasks.scan_zip_with_clamav': {'queue': 'scan'},
    'gallery.tasks.vulnerability_scan': {'queue': 'scan'},
    'gallery.tasks.process_upload_pipeline': {'queue': 'scan'},
    # Ranking is bulk background work. Its own queue so a long rescore can
    # never sit in front of a user waiting for their upload to be scanned.
    'gallery.tasks.refresh_appeal_scores': {'queue': 'rank'},
}
CELERY_TASK_TIME_LIMIT = 120  # 2min hard kill per scan
CELERY_TASK_SOFT_TIME_LIMIT = 90
CELERY_BROKER_TRANSPORT_OPTIONS = {'visibility_timeout': 3600}
from celery.schedules import crontab
CELERY_BEAT_SCHEDULE = {
    'generate-weekly-challenges': {
        'task': 'gallery.tasks.generate_weekly_challenges',
        'schedule': crontab(day_of_week='mon', hour=0, minute=0),
    },
    # Appeal decays with the clock, so it must be recomputed on the clock.
    # Every 10 minutes keeps a new upload's ranking fresh without making
    # ranking a write on the interaction path.
    'refresh-appeal-scores': {
        'task': 'gallery.tasks.refresh_appeal_scores',
        'schedule': crontab(minute='*/10'),
        'kwargs': {'limit': int(os.getenv('APPEAL_BATCH_LIMIT', '500'))},
    },
}

# --- Discovery / program-kind classification ---
# 5 Whys: Why env-tunable? 1. Cost per LLM call is real money. 2. A traffic
# spike must be throttleable without a redeploy. 3. Zero is a valid value —
# it disables LLM classification entirely and leaves the heuristic. 4. The
# floor lets ops trade accuracy against spend. 5. Defaults are safe for a
# deployment with no key at all: nothing is called, nothing breaks.
KIND_LLM_CALLS_PER_MINUTE = int(os.getenv('KIND_LLM_CALLS_PER_MINUTE', '30'))
KIND_LLM_CONFIDENCE_FLOOR = float(os.getenv('KIND_LLM_CONFIDENCE_FLOOR', '0.55'))

# --- S3 / R2 ---
AWS_ACCESS_KEY_ID = os.getenv('AWS_ACCESS_KEY_ID', '')
AWS_SECRET_ACCESS_KEY = os.getenv('AWS_SECRET_ACCESS_KEY', '')
AWS_STORAGE_BUCKET_NAME = os.getenv('AWS_STORAGE_BUCKET_NAME', 'blaqvibes-media')
AWS_S3_ENDPOINT_URL = os.getenv('AWS_S3_ENDPOINT_URL', '')
AWS_S3_REGION_NAME = os.getenv('AWS_S3_REGION_NAME', 'auto')
AWS_S3_SIGNATURE_VERSION = 's3v4'
AWS_QUARANTINE_BUCKET = os.getenv('AWS_QUARANTINE_BUCKET', 'blaqvibes-quarantine')
# Fail closed: no canned ACL (R2 rejects them), no public CDN hostname.
# Privacy is a private bucket policy + signed URLs. Never set AWS_S3_CUSTOM_DOMAIN.
AWS_DEFAULT_ACL = None
AWS_QUERYSTRING_AUTH = True
AWS_QUERYSTRING_EXPIRE = 300
AWS_S3_FILE_OVERWRITE = False
AWS_S3_CUSTOM_DOMAIN = ''
if AWS_ACCESS_KEY_ID:
    STORAGES = {
        "default": {
            "BACKEND": "gallery.storages.PrivateMediaStorage",
            "OPTIONS": {
                "default_acl": None,
                "querystring_auth": True,
                "querystring_expire": 300,
                "file_overwrite": False,
                "custom_domain": None,
                "signature_version": "s3v4",
            },
        },
        "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
    }

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'allauth.account.middleware.AccountMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'blaqvibes.urls'
TEMPLATES = [{
    'BACKEND': 'django.template.backends.django.DjangoTemplates',
    'DIRS': [BASE_DIR / 'templates'],
    'APP_DIRS': False,
    'OPTIONS': {
        'context_processors': [
            'django.template.context_processors.debug',
            'django.template.context_processors.request',
            'django.contrib.auth.context_processors.auth',
            'django.contrib.messages.context_processors.messages',
            'django.template.context_processors.csrf',
            'gallery.context_processors.extras',
        ],
        # Cache templates in production; re-read them from disk in DEBUG so
        # local template edits show up without a server restart.
        'loaders': (
            [('django.template.loaders.cached.Loader', [
                'django.template.loaders.filesystem.Loader',
                'django.template.loaders.app_directories.Loader',
            ])] if not DEBUG else [
                'django.template.loaders.filesystem.Loader',
                'django.template.loaders.app_directories.Loader',
            ]
        ),
    },
}]
WSGI_APPLICATION = 'blaqvibes.wsgi.application'

def _db_from_url(url: str):
    from urllib.parse import urlparse, unquote
    parsed = urlparse(url)
    scheme = (parsed.scheme or '').split('+')[0]
    if scheme.startswith('postgres'):
        engine = 'django.db.backends.postgresql'
    elif scheme.startswith('mysql'):
        engine = 'django.db.backends.mysql'
    else:
        engine = 'django.db.backends.sqlite3'
    return {
        'ENGINE': engine,
        'NAME': unquote(parsed.path.lstrip('/')),
        'USER': unquote(parsed.username or ''),
        'PASSWORD': unquote(parsed.password or ''),
        'HOST': parsed.hostname or '',
        'PORT': str(parsed.port or ''),
    }

DATABASE_URL = os.getenv('DATABASE_URL', '')
if DATABASE_URL:
    DATABASES = {'default': _db_from_url(DATABASE_URL)}
else:
    DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': BASE_DIR / 'db.sqlite3'}}

REDIS_URL = os.getenv('REDIS_URL', '')
if REDIS_URL:
    # Shared Redis-backed rate-limit cache so limits hold across gunicorn workers.
    RATELIMIT_CACHE = {
        'BACKEND': 'django.core.cache.backends.redis.RedisCache',
        'LOCATION': REDIS_URL,
        'KEY_PREFIX': 'blaqvibes-ratelimit',
    }
else:
    # Dev fallback (single process) — per-worker cache is fine without Redis.
    RATELIMIT_CACHE = {
        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
        'LOCATION': 'blaqvibes-ratelimit',
    }

CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
        'LOCATION': 'blaqvibes',
    },
    'ratelimit': RATELIMIT_CACHE,
}
RATELIMIT_ENABLE = os.getenv('RATELIMIT_ENABLE', '1') == '1'
RATELIMIT_USE_CACHE = 'ratelimit'

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator', 'OPTIONS': {'min_length': 8}},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]
SESSION_COOKIE_HTTPONLY = True
# CSRF token is exposed via {% csrf_token %} / forms, never read from the cookie,
# so keep it HttpOnly as defense-in-depth against token exfiltration.
CSRF_COOKIE_HTTPONLY = True
if not DEBUG and not LOCAL_DEV:
    SECURE_SSL_REDIRECT = True
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_CONTENT_TYPE_NOSNIFF = True
    X_FRAME_OPTIONS = 'DENY'
    SECURE_REFERRER_POLICY = 'same-origin'
    SECURE_CROSS_ORIGIN_OPENER_POLICY = 'same-origin'
else:
    SECURE_SSL_REDIRECT = False
    SECURE_HSTS_SECONDS = 0
    SECURE_HSTS_INCLUDE_SUBDOMAINS = False
    SESSION_COOKIE_SECURE = False
    CSRF_COOKIE_SECURE = False
    SECURE_CONTENT_TYPE_NOSNIFF = False
    X_FRAME_OPTIONS = 'SAMEORIGIN'
    SECURE_REFERRER_POLICY = None
    SECURE_CROSS_ORIGIN_OPENER_POLICY = None

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'Africa/Johannesburg'
USE_I18N = True
USE_TZ = True
STATIC_URL = '/static/'
STATICFILES_DIRS = [BASE_DIR / 'static']
STATIC_ROOT = BASE_DIR / 'staticfiles'
MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'
# Email — defined exactly once. Dev has no SMTP daemon: default to the
# console backend so signup / password-reset / queue-done mail lands in the
# runserver logs. Production does NOT silently swallow mail into a console
# void — it falls back to Django's SMTP default and real deployments set
# EMAIL_BACKEND + host credentials explicitly (see .env.example).
EMAIL_BACKEND = os.getenv(
    'EMAIL_BACKEND',
    'django.core.mail.backends.console.EmailBackend'
    if LOCAL_DEV else 'django.core.mail.backends.smtp.EmailBackend',
)
DEFAULT_FROM_EMAIL = os.getenv('DEFAULT_FROM_EMAIL', 'noreply@blaqvibes.co.za')
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
LOGIN_URL = '/accounts/login/'
LOGIN_REDIRECT_URL = '/'
LOGOUT_REDIRECT_URL = '/'
BLAQVIBES_MAX_ZIP_MB = 100
BLAQVIBES_MAX_FILES = 1000
# Security: No sensitive data in JS — all secrets stay in os.getenv, never passed to template context
