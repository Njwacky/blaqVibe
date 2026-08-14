"""Private object storage for paid ZIPs (and everything else on the default store).

5 Whys:
1. Why not a public bucket + ACL? `blaqvibes-public` plus a public-read policy
   makes `/apps/zips/...` world-readable and skips the Trade/Sale gate.
2. Why default_acl=None instead of 'private'? R2 and S3 "Bucket owner
   enforced" reject canned ACLs. A failed ACL upload looks like "storage is
   broken" and people flip the bucket to public to "make it work".
3. Why force custom_domain off? A CDN hostname makes django-storages emit
   unsigned URLs. That is a public object in one setting.
4. Why signed GET with Content-Disposition=attachment? A leaked URL should
   download, not render, and should die in 5 minutes.
5. Why not a second public store for thumbnails? Same bucket + one public
   prefix is how zips leak. Thumbs use the same private store and signed URLs.
"""
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
    return bool(os.getenv('AWS_ACCESS_KEY_ID') or getattr(settings, 'AWS_ACCESS_KEY_ID', None))


def get_presigned_url(s3_key, expires=300, filename=None):
    """Return a short signed GET for s3_key. None if S3 is off or signing fails."""
    if not s3_key or not is_s3_enabled():
        return None
    try:
        import boto3
        from botocore.config import Config
        s3 = boto3.client(
            's3',
            endpoint_url=os.getenv('AWS_S3_ENDPOINT_URL') or getattr(settings, 'AWS_S3_ENDPOINT_URL', None),
            aws_access_key_id=os.getenv('AWS_ACCESS_KEY_ID') or getattr(settings, 'AWS_ACCESS_KEY_ID', None),
            aws_secret_access_key=os.getenv('AWS_SECRET_ACCESS_KEY') or getattr(settings, 'AWS_SECRET_ACCESS_KEY', None),
            region_name=os.getenv('AWS_S3_REGION_NAME') or getattr(settings, 'AWS_S3_REGION_NAME', 'auto'),
            config=Config(signature_version='s3v4'),
        )
        bucket = os.getenv('AWS_STORAGE_BUCKET_NAME') or getattr(settings, 'AWS_STORAGE_BUCKET_NAME', None)
        params = {'Bucket': bucket, 'Key': s3_key}
        if filename:
            safe = quote(filename.replace('"', ''), safe='')
            params['ResponseContentDisposition'] = f'attachment; filename="{safe}"'
            params['ResponseContentType'] = 'application/zip'
        return s3.generate_presigned_url('get_object', Params=params, ExpiresIn=int(expires))
    except Exception as exc:
        logger.warning('Presigned URL error: %s', exc)
        return None
