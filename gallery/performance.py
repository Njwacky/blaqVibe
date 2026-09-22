"""
Performance helpers — caching, query optimization, and network speed.

Every function here is crush-safe: cache failures never break a page,
they just make it slower for one request.

Goals:
- Cut DB queries per page from ~15-20 to ~5-8 for anonymous users
- Cache expensive aggregations (trending, remix stats) for 60-300s
- Avoid loading entire tables into memory (search typo fallback)
- Cache presigned S3 URLs (boto3 signing is CPU-heavy)
- Provide helpers for feed pagination without COUNT(*) where possible
"""
import hashlib
import logging
from functools import wraps

from django.core.cache import cache

logger = logging.getLogger(__name__)

# ----------------------------------------------------------------------
# Generic cache helper with versioning and safe fallback
# ----------------------------------------------------------------------
def cached(timeout, key_prefix, version=1):
    """Decorator: cache function result with timeout.

    Key = f"{key_prefix}:{version}:{args_hash}"
    Failures in cache get/set never raise.
    """
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            # Build deterministic key from args
            try:
                raw = f"{args}:{sorted(kwargs.items())}"
                h = hashlib.md5(raw.encode("utf-8")).hexdigest()[:12]
                cache_key = f"{key_prefix}:v{version}:{h}"
            except Exception:
                # If key building fails, just run function
                return fn(*args, **kwargs)

            try:
                hit = cache.get(cache_key)
                if hit is not None:
                    return hit
            except Exception:
                logger.debug("cache get failed %s", cache_key, exc_info=True)

            result = fn(*args, **kwargs)

            try:
                cache.set(cache_key, result, timeout)
            except Exception:
                logger.debug("cache set failed %s", cache_key, exc_info=True)

            return result
        return wrapper
    return decorator


def cache_get_or_set(key, fn, timeout):
    """Get from cache or compute via fn(), set, and return."""
    try:
        hit = cache.get(key)
        if hit is not None:
            return hit
    except Exception:
        pass
    try:
        value = fn()
    except Exception:
        logger.exception("cache_get_or_set fn failed key=%s", key)
        return None
    try:
        cache.set(key, value, timeout)
    except Exception:
        pass
    return value


# ----------------------------------------------------------------------
# Specific cached data for feed / discover
# ----------------------------------------------------------------------
CATEGORY_CACHE_KEY = "perf:categories:v1"
CATEGORY_TIMEOUT = 600  # 10 min

def get_cached_categories():
    """Categories ordered by 'order' — cheap to cache, changes rarely."""
    def _load():
        from .models import Category
        return list(Category.objects.all().order_by("order"))
    return cache_get_or_set(CATEGORY_CACHE_KEY, _load, CATEGORY_TIMEOUT) or []


TRENDING_CACHE_KEY = "perf:trending_scores:v2"
TRENDING_TIMEOUT = 120  # 2 min — trending is time-windowed anyway

ACTIVITY_CACHE_KEY = "perf:activity_summary:v1"
ACTIVITY_TIMEOUT = 180

REMIX_TOTALS_CACHE_KEY = "perf:remix_totals:v1"
REMIX_TOTALS_TIMEOUT = 300

# SiteSettings singleton — DB hit on every request via context processor
SITE_SETTINGS_CACHE_KEY = "perf:site_settings:v1"
SITE_SETTINGS_TIMEOUT = 120

def get_cached_site_settings():
    def _load():
        from users.models import SiteSettings
        try:
            return SiteSettings.get()
        except Exception:
            return None
    return cache_get_or_set(SITE_SETTINGS_CACHE_KEY, _load, SITE_SETTINGS_TIMEOUT)


FOOTER_CONTACTS_CACHE_KEY = "perf:footer_contacts:v1"
FOOTER_CONTACTS_TIMEOUT = 600

def get_cached_footer_contacts():
    def _load():
        try:
            from users.footer_contacts import public_footer_contacts
            return public_footer_contacts() or []
        except Exception:
            return []
    return cache_get_or_set(FOOTER_CONTACTS_CACHE_KEY, _load, FOOTER_CONTACTS_TIMEOUT)


SOCIAL_PROVIDERS_CACHE_KEY = "perf:social_providers:v1"
SOCIAL_PROVIDERS_TIMEOUT = 600

def get_cached_social_providers():
    def _load():
        try:
            from users.social import configured_social_providers
            return configured_social_providers()
        except Exception:
            return []
    return cache_get_or_set(SOCIAL_PROVIDERS_CACHE_KEY, _load, SOCIAL_PROVIDERS_TIMEOUT)


# Presigned URL cache — boto3 signing is CPU heavy
PRESIGNED_CACHE_TIMEOUT = 240  # S3 URL valid 300s, cache 240s so we refresh early

def presigned_cache_key(s3_key, filename):
    raw = f"{s3_key}:{filename}"
    return f"presigned:v1:{hashlib.md5(raw.encode()).hexdigest()}"


# Feed count cache for anonymous users — Paginator COUNT(*) is expensive
FEED_COUNT_CACHE_KEY = "perf:feed_count:v1"
FEED_COUNT_TIMEOUT = 60


def get_cached_feed_count():
    def _load():
        from .models import AppProject
        return AppProject.objects.filter(status="published").count()
    return cache_get_or_set(FEED_COUNT_CACHE_KEY, _load, FEED_COUNT_TIMEOUT) or 0
