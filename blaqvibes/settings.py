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
def _env_flag(name, default=False):
    raw = os.getenv(name)
    if raw is None or raw.strip() == '':
        return default
    return raw.strip().lower() in ('1', 'true', 'yes', 'on')

# Tests are detected BEFORE LOCAL_DEV, for two reasons: the suite runs with
# DEBUG=0, and a production-shaped LOCAL_DEV=False would switch on
# SECURE_SSL_REDIRECT so that every test request answers 301. It is also
# consumed by SEED_DEMO further down, so it must already be defined by then.
TESTING = any(arg == 'test' for arg in sys.argv) or os.getenv('DJANGO_TEST') == '1'

# Local development is EXPLICIT, or it is a genuine DEBUG run.
if os.getenv('DJANGO_LOCAL_DEV', '').strip() != '':
    LOCAL_DEV = _env_flag('DJANGO_LOCAL_DEV')
else:
    LOCAL_DEV = DEBUG or TESTING

# Sentry — backend only, no JS secrets. Do not dummy-init without a DSN:
# the Django/WSGI integrations still wrap exception handling, and older
# sentry-sdk builds crash on Python 3.13 FrameLocalsProxy while serializing
# locals. init_sentry also disables local-variable capture when a DSN is set.
try:
    from .sentry import init_sentry
    init_sentry()
except Exception:
    pass  # observability must never block boot

SECRET_KEY = os.getenv('SECRET_KEY', '')
if not SECRET_KEY:
    if LOCAL_DEV:
        SECRET_KEY = 'django-insecure-blaqvibes-dev-key-change-in-prod-07070A'
    else:
        # Never boot production (DEBUG=0) without a real key. LOCAL_DEV is only
        # true when an operator asked for it (DJANGO_LOCAL_DEV=1) or DEBUG is on,
        # so the dev key can no longer arrive by inferring a virtualenv.
        raise RuntimeError(
            'SECRET_KEY must be set. Add SECRET_KEY to your .env — or, for a '
            'local run only, DEBUG=1 / DJANGO_LOCAL_DEV=1.'
        )

# Arena / e2b live preview is HTTPS on {port}-{id}.e2b.app, often inside
# a cross-site iframe. Detect it independently of DEBUG so cookie flags
# and trusted origins stay correct even if an operator sets DEBUG=0.
PREVIEW = _env_flag('E2B_SANDBOX') or _env_flag('DJANGO_PREVIEW')

_raw_hosts = os.getenv('ALLOWED_HOSTS', 'localhost,127.0.0.1,0.0.0.0')
ALLOWED_HOSTS = [h.strip() for h in _raw_hosts.split(',') if h.strip()]
if '*' in ALLOWED_HOSTS and not (DEBUG or LOCAL_DEV):
    ALLOWED_HOSTS = ['blaqvibes.co.za', 'www.blaqvibes.co.za']
if DEBUG or LOCAL_DEV or PREVIEW:
    for extra in ('localhost', '127.0.0.1', '0.0.0.0', '.e2b.app', '.localhost', 'testserver'):
        if extra not in ALLOWED_HOSTS:
            ALLOWED_HOSTS.append(extra)

def csrf_trusted_origins(raw, *, preview, local):
    """Deduped Origin allow-list. Preview/local always trust e2b.app.
    """
    origins = [o.strip() for o in (raw or '').split(',') if o.strip()]
    if preview or local:
        origins += [
            'https://*.e2b.app',
            'http://localhost:8000',
            'http://127.0.0.1:8000',
        ]
    seen, out = set(), []
    for origin in origins:
        if origin not in seen:
            seen.add(origin)
            out.append(origin)
    return out

CSRF_TRUSTED_ORIGINS = csrf_trusted_origins(
    os.getenv(
        'CSRF_TRUSTED_ORIGINS',
        'https://*.e2b.app,https://blaqvibes.co.za,https://www.blaqvibes.co.za',
    ),
    preview=PREVIEW,
    local=LOCAL_DEV or DEBUG,
)

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
# Production stays empty until an operator runs `python manage.py seed_demo`
# with an explicit local/dev posture (see gallery/seed.py — the command itself
# refuses to create known-password accounts on a public host).
# `TESTING` is defined above, next to LOCAL_DEV, because both are needed there.
SEED_DEMO = os.getenv('SEED_DEMO', '1' if (LOCAL_DEV and not TESTING) else '0') == '1'

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
    'users.apps.UsersConfig',
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
# BlaqVibes owns email confirmation: users.views.send_verify_email sends it and
# /accounts/verify/<uid>/<token>/ consumes it, because confirming is what pays
# the welcome grant. allauth's own confirm view is NOT mounted, so letting
# allauth send its mail would build a link to a route that does not exist —
# reverse('account_confirm_email') raises and the OAuth callback 500s on an
# otherwise successful sign-in. 'none' keeps allauth out of a flow we own.
ACCOUNT_EMAIL_VERIFICATION = 'none'
ACCOUNT_UNIQUE_EMAIL = True
SOCIALACCOUNT_AUTO_SIGNUP = True
SOCIALACCOUNT_EMAIL_AUTHENTICATION = True
SOCIALACCOUNT_EMAIL_AUTHENTICATION_AUTO_CONNECT = True
# POST-only handshake start: a GET on /accounts/social/<p>/login/ would let a
# third-party <img> tag start an OAuth round-trip. The button posts a CSRF token.
SOCIALACCOUNT_LOGIN_ON_GET = False
SOCIALACCOUNT_QUERY_EMAIL = True
# Access tokens are a bearer credential for someone's GitHub/Facebook account.
# We only need identity at sign-in, so nothing is persisted. Explicit, not implied.
SOCIALACCOUNT_STORE_TOKENS = False

# The callback URL the provider is told to return to is built from the request.
# Behind a TLS-terminating proxy the request looks like plain http, so the
# generated redirect_uri would be http:// and the provider would reject it as a
# mismatch. Force the scheme of the canonical origin instead.
ACCOUNT_DEFAULT_HTTP_PROTOCOL = 'https' if SITE_URL.startswith('https://') else 'http'

# One row per provider: slug, button label, the two settings that hold its
# credentials, and the allauth provider config. users.social reads this to
# decide which buttons to render, so a button can never point at a provider
# allauth has no SocialApp for. The credentials are named rather than inlined
# so the check stays live (tests override GOOGLE_CLIENT_ID; the button follows).
SOCIAL_PROVIDER_CREDENTIALS = {
    'google': {
        'label': 'Continue with Google',
        'id_setting': 'GOOGLE_CLIENT_ID',
        'secret_setting': 'GOOGLE_CLIENT_SECRET',
        'settings': {
            'SCOPE': ['profile', 'email'],
            'AUTH_PARAMS': {'access_type': 'online'},
        },
    },
    'github': {
        'label': 'Continue with GitHub',
        'id_setting': 'GITHUB_CLIENT_ID',
        'secret_setting': 'GITHUB_CLIENT_SECRET',
        'settings': {
            # read:user for the profile, user:email because a GitHub account
            # can keep every address private — /user returns email: null and
            # allauth then reads /user/emails to find the verified primary.
            'SCOPE': ['read:user', 'user:email'],
        },
    },
    'facebook': {
        'label': 'Continue with Facebook',
        'id_setting': 'FACEBOOK_CLIENT_ID',
        'secret_setting': 'FACEBOOK_CLIENT_SECRET',
        'settings': {
            'METHOD': 'oauth2',
            'SCOPE': ['email', 'public_profile'],
            # Pin the Graph API version. allauth defaults to v19.0, which Meta
            # retired on 2026-05-21 — unpinned, every Facebook login fails.
            # v25.0 is supported until 2028-07-29.
            'VERSION': 'v25.0',
            # Only what we use. The upstream default asks for `verified`,
            # `locale`, `timezone` and `gender`, which need extra review and
            # make the /me call fail on current Graph versions.
            'FIELDS': ['id', 'name', 'first_name', 'last_name', 'email'],
            # Meta confirms an address before it can be added to an account, so
            # a Facebook email is treated as verified — this is what lets a
            # Facebook login land on the existing BlaqVibes account with the
            # same address instead of creating a duplicate.
            'VERIFIED_EMAIL': True,
        },
    },
}

# Only providers with BOTH halves of their credentials are handed to allauth.
# Declaring a provider with a blank client_id is worse than omitting it: allauth
# happily redirects to `github.com/login/oauth/authorize?client_id=` and the
# user meets the provider's own error page. Omitted -> no button (users.social),
# and its callback route 404s through our own safe_404.
SOCIALACCOUNT_PROVIDERS = {
    slug: {
        'APP': {
            'client_id': globals()[cfg['id_setting']],
            'secret': globals()[cfg['secret_setting']],
            'key': '',
        },
        **cfg['settings'],
    }
    for slug, cfg in SOCIAL_PROVIDER_CREDENTIALS.items()
    if globals()[cfg['id_setting']] and globals()[cfg['secret_setting']]
}

CELERY_BROKER_URL = os.getenv('REDIS_URL', 'redis://localhost:6379/0')
CELERY_RESULT_BACKEND = os.getenv('REDIS_URL', 'redis://localhost:6379/0')
CELERY_TASK_ALWAYS_EAGER = os.getenv('CELERY_EAGER', '1' if LOCAL_DEV else '0') == '1'
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
    # The daily prompt has to exist at 00:00 whether or not anybody is
    # browsing, and yesterday's bounty has to be paid whether or not a
    # moderator remembers to click. 00:05 keeps it clear of the midnight
    # boundary (and of the weekly challenge job on Monday 00:00).
    'daily-challenge': {
        'task': 'gallery.tasks.run_daily_challenges',
        'schedule': crontab(hour=0, minute=5),
    },
}

# Program-kind classification is env-tunable: each LLM call costs real money,
# so the rate must be throttleable without a redeploy (0 disables LLM calls and
# leaves the heuristic). Defaults are safe with no key at all — nothing is
# called, nothing breaks.
KIND_LLM_CALLS_PER_MINUTE = int(os.getenv('KIND_LLM_CALLS_PER_MINUTE', '30'))
KIND_LLM_CONFIDENCE_FLOOR = float(os.getenv('KIND_LLM_CONFIDENCE_FLOOR', '0.55'))

# S3 / R2
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
    # Before CsrfView so process_response runs *after* the CSRF cookie is set.
    'gallery.middleware.PreviewEmbedMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    # AFTER AuthenticationMiddleware: the maintenance wall needs request.user
    # to exempt superadmins. It used to sit before auth, so the attribute
    # access raised whenever maintenance was on, the silent except swallowed
    # it, and maintenance mode NEVER served the 503 it promised. Now it short-
    # circuits every public path the same way, and the superadmin bypass works.
    'gallery.middleware.MaintenanceModeMiddleware',
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
if REDIS_URL and (not LOCAL_DEV or os.getenv('USE_REDIS', '0') == '1'):
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
SESSION_ENGINE = 'django.contrib.sessions.backends.db'
SECURITY_TRUSTED_PROXY_IPS = tuple(ip.strip() for ip in os.getenv('SECURITY_TRUSTED_PROXY_IPS', '').split(',') if ip.strip())
# Stolen browser sessions should not survive indefinitely. Active users renew
# the expiry; idle browsers are forced to authenticate again after eight hours.
SESSION_COOKIE_AGE = 8 * 60 * 60
SESSION_SAVE_EVERY_REQUEST = True
# CSRF token is exposed via {% csrf_token %} / forms, never read from the cookie,
# so keep it HttpOnly as defense-in-depth against token exfiltration.
CSRF_COOKIE_HTTPONLY = True
# Trust X-Forwarded-Proto ONLY when a TLS-terminating proxy is actually in
# front. The preview host (https://…e2b.app) always is; a real nginx deploy
# opts in with DJANGO_BEHIND_TLS_PROXY=1. The shipped docker-compose runs
# gunicorn directly on :8000 with no proxy — trusting a client-supplied
# X-Forwarded-Proto there would let an attacker claim "https" and defeat
# SECURE_SSL_REDIRECT. The header is client-controlled; only a proxy that
# OVERWRITES it makes it truth. PREVIEW is trusted implicitly (Arena
# terminates TLS per-request), while an nginx deploy opts in with the flag,
# which documents the contract: set it only after configuring the proxy to
# replace the header. Host/port trust (USE_X_FORWARDED_HOST) rides the same
# "a proxy is in front" decision.
BEHIND_TLS_PROXY = PREVIEW or _env_flag('DJANGO_BEHIND_TLS_PROXY')
if BEHIND_TLS_PROXY:
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
    USE_X_FORWARDED_HOST = True
    USE_X_FORWARDED_PORT = True

def cookie_security(*, production, preview):
    """CSRF/session cookie flags. Preview is an HTTPS iframe; laptop HTTP is not.
    """
    if preview:
        return {
            'CSRF_COOKIE_SECURE': True,
            'SESSION_COOKIE_SECURE': True,
            'CSRF_COOKIE_SAMESITE': 'None',
            'SESSION_COOKIE_SAMESITE': 'None',
            'partition_cookies': True,
        }
    if production:
        return {
            'CSRF_COOKIE_SECURE': True,
            'SESSION_COOKIE_SECURE': True,
            'CSRF_COOKIE_SAMESITE': 'Lax',
            'SESSION_COOKIE_SAMESITE': 'Lax',
            'partition_cookies': False,
        }
    return {
        'CSRF_COOKIE_SECURE': False,
        'SESSION_COOKIE_SECURE': False,
        'CSRF_COOKIE_SAMESITE': 'Lax',
        'SESSION_COOKIE_SAMESITE': 'Lax',
        'partition_cookies': False,
    }

_cookie = cookie_security(
    production=not DEBUG and not LOCAL_DEV,
    preview=PREVIEW,
)
if not DEBUG and not LOCAL_DEV:
    SECURE_SSL_REDIRECT = True
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_CONTENT_TYPE_NOSNIFF = True
    X_FRAME_OPTIONS = 'DENY'
    SECURE_REFERRER_POLICY = 'same-origin'
    SECURE_CROSS_ORIGIN_OPENER_POLICY = 'same-origin'
else:
    SECURE_SSL_REDIRECT = False
    SECURE_HSTS_SECONDS = 0
    SECURE_HSTS_INCLUDE_SUBDOMAINS = False
    SECURE_CONTENT_TYPE_NOSNIFF = False
    X_FRAME_OPTIONS = 'SAMEORIGIN'
    SECURE_REFERRER_POLICY = None
    SECURE_CROSS_ORIGIN_OPENER_POLICY = None
SESSION_COOKIE_SECURE = _cookie['SESSION_COOKIE_SECURE']
CSRF_COOKIE_SECURE = _cookie['CSRF_COOKIE_SECURE']
SESSION_COOKIE_SAMESITE = _cookie['SESSION_COOKIE_SAMESITE']
CSRF_COOKIE_SAMESITE = _cookie['CSRF_COOKIE_SAMESITE']
PARTITION_EMBED_COOKIES = _cookie['partition_cookies']
CSRF_FAILURE_VIEW = 'users.csrf.csrf_failure'

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

# LOGGING
LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO').upper()
if LOG_LEVEL not in ('DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'):
    LOG_LEVEL = 'INFO'

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            'format': (
                '%(asctime)s %(levelname)s %(name)s %(process)d '
                '[%(threadName)s] %(message)s'
            ),
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'verbose',
            'level': LOG_LEVEL,
        },
    },
    'root': {
        'handlers': ['console'],
        'level': LOG_LEVEL,
    },
    'loggers': {
        # App code at LOG_LEVEL; 4xx noise stays quiet, 5xx still surfaces.
        'django': {
            'handlers': ['console'],
            'level': LOG_LEVEL,
            'propagate': False,
        },
        'django.request': {
            'handlers': ['console'],
            'level': 'WARNING',
            'propagate': False,
        },
        'django.security': {
            'handlers': ['console'],
            'level': 'WARNING',
            'propagate': False,
        },
        # Query logs are pure noise unless debugging a slow page.
        'django.db.backends': {
            'handlers': ['console'],
            'level': 'WARNING',
            'propagate': False,
        },
        'gallery': {
            'handlers': ['console'],
            'level': LOG_LEVEL,
            'propagate': False,
        },
        'users': {
            'handlers': ['console'],
            'level': LOG_LEVEL,
            'propagate': False,
        },
        'celery': {
            'handlers': ['console'],
            'level': 'INFO',
            'propagate': False,
        },
    },
}

