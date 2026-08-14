import os
import logging
from django.conf import settings

try:
    from storages.backends.s3 import S3Storage
except ImportError:  # pragma: no cover - django-storages is a required dep
    S3Storage = None

logger = logging.getLogger(__name__)

# Paid ZIPs must never become public objects. custom_domain is forced off so a
# public CDN hostname cannot silently disable signed URLs.
PRIVATE_S3_OPTIONS = {
    'default_acl': 'private',
    'querystring_auth': True,
    'querystring_expire': 300,
    'file_overwrite': False,
    'custom_domain': None,
}


class PrivateMediaStorage(S3Storage if S3Storage is not None else object):
    """Uploads (including paid ZIPs) are private objects with short signed URLs."""

    default_acl = 'private'
    querystring_auth = True
    querystring_expire = 300
    file_overwrite = False
    custom_domain = None

    def __init__(self, **kwargs):
        if S3Storage is None:
            raise RuntimeError('django-storages is required for S3/R2 uploads')
        kwargs['default_acl'] = 'private'
        kwargs['querystring_auth'] = True
        kwargs.setdefault('querystring_expire', 300)
        kwargs.setdefault('file_overwrite', False)
        kwargs['custom_domain'] = None
        super().__init__(**kwargs)

# 5 Whys for S3/R2 Signed URLs:
# 1. Why not public S3? Public = spider scrapes all ZIPS, no clone count, DMCA abuse. 2. Why presigned 5min? Short expiry = leaked link dies, rate limit enforced. 3. Why R2 not S3? R2 zero egress = 1000 downloads free, S3 $0.09/GB. 4. Why boto3 not django-storages? boto3 presigned is one call, storages config is for upload. 5. Why fallback to local? Dev without keys must still run — no shortcut that breaks local.

def get_presigned_url(s3_key, expires=300):
    """Return presigned URL for s3_key. If no AWS creds, return local MEDIA_URL fallback."""
    # Prod path: R2/S3 via boto3
    aws_key = os.getenv('AWS_ACCESS_KEY_ID') or getattr(settings, 'AWS_ACCESS_KEY_ID', None)
    if aws_key:
        try:
            import boto3
            from botocore.config import Config
            s3 = boto3.client(
                's3',
                endpoint_url=os.getenv('AWS_S3_ENDPOINT_URL') or getattr(settings, 'AWS_S3_ENDPOINT_URL', None), # R2: https://<account>.r2.cloudflarestorage.com
                aws_access_key_id=aws_key,
                aws_secret_access_key=os.getenv('AWS_SECRET_ACCESS_KEY') or getattr(settings, 'AWS_SECRET_ACCESS_KEY', None),
                region_name=os.getenv('AWS_S3_REGION_NAME','auto'),
                config=Config(signature_version='s3v4')
            )
            bucket = os.getenv('AWS_STORAGE_BUCKET_NAME') or getattr(settings, 'AWS_STORAGE_BUCKET_NAME', None)
            return s3.generate_presigned_url('get_object', Params={'Bucket': bucket, 'Key': s3_key}, ExpiresIn=expires)
        except Exception as e:
            # Log and fallback to local serving
            logger.warning("Presigned URL error: %s", e)
    # Dev fallback: serve via Django MEDIA_URL (still logs clone count)
    # The view will serve file directly if no S3
    return None

def is_s3_enabled():
    return bool(os.getenv('AWS_ACCESS_KEY_ID') or getattr(settings, 'AWS_ACCESS_KEY_ID', None))
