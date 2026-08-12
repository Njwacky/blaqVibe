from pathlib import Path
import os
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / '.env')

# Detect local development in a virtual environment or debug mode.
# This avoids forcing production-only HTTPS handling when running locally.
LOCAL_DEV = bool(os.getenv('VIRTUAL_ENV') or os.getenv('PYTHONENV') or os.getenv('DJANGO_LOCAL_DEV') or os.getenv('DEBUG', '1') == '1')

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

SECRET_KEY = os.getenv('SECRET_KEY', 'django-insecure-blaqvibes-dev-key-change-in-prod-07070A')
DEBUG = os.getenv('DEBUG', '1') == '1'
ALLOWED_HOSTS = os.getenv('ALLOWED_HOSTS', 'localhost,127.0.0.1').split(',') if LOCAL_DEV else os.getenv('ALLOWED_HOSTS', '*').split(',')
CSRF_TRUSTED_ORIGINS = ['https://*.e2b.app']
if LOCAL_DEV:
    CSRF_TRUSTED_ORIGINS += ['http://localhost:8000', 'http://127.0.0.1:8000']

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'gallery',
    'users',
]

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
}

# --- S3 / R2 ---
AWS_ACCESS_KEY_ID = os.getenv('AWS_ACCESS_KEY_ID', '')
AWS_SECRET_ACCESS_KEY = os.getenv('AWS_SECRET_ACCESS_KEY', '')
AWS_STORAGE_BUCKET_NAME = os.getenv('AWS_STORAGE_BUCKET_NAME', 'blaqvibes-public')
AWS_S3_ENDPOINT_URL = os.getenv('AWS_S3_ENDPOINT_URL', '')
AWS_S3_REGION_NAME = os.getenv('AWS_S3_REGION_NAME', 'auto')
AWS_S3_SIGNATURE_VERSION = 's3v4'
AWS_QUARANTINE_BUCKET = os.getenv('AWS_QUARANTINE_BUCKET', 'blaqvibes-quarantine')
if AWS_ACCESS_KEY_ID:
    STORAGES = {
        "default": {"BACKEND": "storages.backends.s3.S3Storage"},
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
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'blaqvibes.urls'
TEMPLATES = [{
    'BACKEND': 'django.template.backends.django.DjangoTemplates',
    'DIRS': [BASE_DIR / 'templates'],
    'APP_DIRS': True,
    'OPTIONS': {
        'context_processors': [
            'django.template.context_processors.debug',
            'django.template.context_processors.request',
            'django.contrib.auth.context_processors.auth',
            'django.contrib.messages.context_processors.messages',
        ],
    },
}]
WSGI_APPLICATION = 'blaqvibes.wsgi.application'
DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': BASE_DIR / 'db.sqlite3'}}
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator', 'OPTIONS': {'min_length': 8}},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
]
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
EMAIL_BACKEND = os.getenv('EMAIL_BACKEND', 'django.core.mail.backends.console.EmailBackend')
DEFAULT_FROM_EMAIL = os.getenv('DEFAULT_FROM_EMAIL', 'noreply@blaqvibes.co.za')
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
LOGIN_URL = '/accounts/login/'
LOGIN_REDIRECT_URL = '/'
LOGOUT_REDIRECT_URL = '/'
BLAQVIBES_MAX_ZIP_MB = 100
BLAQVIBES_MAX_FILES = 1000
# Security: No sensitive data in JS — all secrets stay in os.getenv, never passed to template context
