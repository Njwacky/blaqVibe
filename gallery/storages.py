"""Private object storage for paid ZIPs (and everything else on the default store)."""
import logging
import os
from urllib.parse import quote

from django.conf import settings

try:
    from storages.backends.s3 import S3Storage
except ImportError:  # pragma: no cover - django-storages is a required dep
    S3Storage = None

logger = logging.getLogger(__name__)

# None = do not send a canned ACL (R2-safe). Privacy is bucket policy + signed URLs.
PRIVATE_S3_OPTIONS = {
    'default_acl': None,
    'querystring_auth': True,
    'querystring_expire': 300,
    'file_overwrite': False,
    'custom_domain': None,
    'signature_version': 's3v4',
}

class PrivateMediaStorage(S3Storage if S3Storage is not None else object):
    """Uploads are private objects with short signed URLs. Never a public CDN."""

    default_acl = None
    querystring_auth = True
    querystring_expire = 300
    file_overwrite = False
    custom_domain = None
    signature_version = 's3v4'

    def __init__(self, **kwargs):
        if S3Storage is None:
            raise RuntimeError('django-storages is required for S3/R2 uploads')
        kwargs['default_acl'] = None
        kwargs['querystring_auth'] = True
        kwargs.setdefault('querystring_expire', 300)
        kwargs.setdefault('file_overwrite', False)
        kwargs['custom_domain'] = None
        kwargs.setdefault('signature_version', 's3v4')
        super().__init__(**kwargs)

def is_s3_enabled():
    """True when S3/R2 credentials are set AND the site-level toggle is on."""
    s3_key = bool(os.getenv('AWS_ACCESS_KEY_ID') or getattr(settings, 'AWS_ACCESS_KEY_ID', None))
    if not s3_key:
        return False
    try:
        from users.models import SiteSettings
        return SiteSettings.get().r2_enabled
    except Exception:
        return True  # on error, trust the env var

def _get_s3_client():
    import boto3
    from botocore.config import Config
    return boto3.client(
        's3',
        endpoint_url=os.getenv('AWS_S3_ENDPOINT_URL') or getattr(settings, 'AWS_S3_ENDPOINT_URL', None),
        aws_access_key_id=os.getenv('AWS_ACCESS_KEY_ID') or getattr(settings, 'AWS_ACCESS_KEY_ID', None),
        aws_secret_access_key=os.getenv('AWS_SECRET_ACCESS_KEY') or getattr(settings, 'AWS_SECRET_ACCESS_KEY', None),
        region_name=os.getenv('AWS_S3_REGION_NAME') or getattr(settings, 'AWS_S3_REGION_NAME', 'auto'),
        config=Config(signature_version='s3v4'),
    )

def get_presigned_url(s3_key, expires=300, filename=None):
    """Return a short signed GET for s3_key. Cached to avoid CPU-heavy signing per request."""
    if not s3_key or not is_s3_enabled():
        return None

    # Try cache first — boto3 signing is CPU heavy and happens per thumbnail/zip
    cache_key = None
    try:
        from django.core.cache import cache
        import hashlib
        raw = f"{s3_key}:{filename or ''}"
        cache_key = f"presigned:v1:{hashlib.md5(raw.encode()).hexdigest()}"
        hit = cache.get(cache_key)
        if hit:
            return hit
    except Exception:
        cache_key = None

    try:
        s3 = _get_s3_client()
        bucket = os.getenv('AWS_STORAGE_BUCKET_NAME') or getattr(settings, 'AWS_STORAGE_BUCKET_NAME', None)
        params = {'Bucket': bucket, 'Key': s3_key}
        if filename:
            safe = quote(filename.replace('"', ''), safe='')
            params['ResponseContentDisposition'] = f'attachment; filename="{safe}"'
            params['ResponseContentType'] = 'application/zip'
        url = s3.generate_presigned_url('get_object', Params=params, ExpiresIn=int(expires))
        if url and cache_key:
            try:
                from django.core.cache import cache
                # Cache 240s when URL valid 300s — refresh early
                cache.set(cache_key, url, 240)
            except Exception:
                pass
        return url
    except Exception as exc:
        logger.warning('Presigned URL error: %s', exc)
        return None

def get_presigned_url_for_image(s3_key, expires=3600):
    """Presigned URL for images (thumbnails) — longer expiry, inline display."""
    if not s3_key or not is_s3_enabled():
        return None
    cache_key = None
    try:
        from django.core.cache import cache
        cache_key = f"presigned-img:v1:{s3_key}"
        hit = cache.get(cache_key)
        if hit:
            return hit
    except Exception:
        cache_key = None
    try:
        s3 = _get_s3_client()
        bucket = os.getenv('AWS_STORAGE_BUCKET_NAME') or getattr(settings, 'AWS_STORAGE_BUCKET_NAME', None)
        params = {'Bucket': bucket, 'Key': s3_key}
        url = s3.generate_presigned_url('get_object', Params=params, ExpiresIn=int(expires))
        if url and cache_key:
            try:
                from django.core.cache import cache
                cache.set(cache_key, url, 3000)  # 50 min, url valid 60 min
            except Exception:
                pass
        return url
    except Exception as exc:
        logger.debug('Presigned image URL error: %s', exc)
        return None
