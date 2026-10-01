"""Shared database cache with atomic counters; no Redis service required.

Django's stock DatabaseCache increments with get/set, which can lose requests
across Gunicorn workers. Counters here use UPDATE-first transactions, so both
Postgres (production/Supabase) and SQLite (development) serialize increments.
Live entries are never evicted to make room: evicting a rate-limit counter
would reset the limit. Only expired entries are periodically pruned.
"""
import base64
import logging
import pickle
from datetime import datetime, timezone

from django.core import signing
from django.core.cache.backends.base import BaseCache, DEFAULT_TIMEOUT
from django.db import models, transaction
from django.utils import timezone as django_timezone

logger = logging.getLogger(__name__)


class DatabaseCache(BaseCache):
    def __init__(self, location, params):
        super().__init__(params)
        self.database = location or 'default'
        self._writes = 0

    @property
    def entries(self):
        # Lazy: importing a settings/cache module must not query the database
        # or import models before Django's app registry is ready.
        from gallery.models import CacheEntry
        return CacheEntry.objects.using(self.database)

    def _key(self, key, version):
        return self.make_and_validate_key(key, version=version)

    def _expires(self, timeout):
        timestamp = self.get_backend_timeout(timeout)
        return None if timestamp is None else datetime.fromtimestamp(timestamp, timezone.utc)

    @staticmethod
    def _live():
        return models.Q(expires__isnull=True) | models.Q(expires__gt=django_timezone.now())

    @staticmethod
    def _encode(value):
        if type(value) is int and -(2 ** 63) <= value < 2 ** 63:
            return {'number': value, 'value': ''}
        raw = base64.b64encode(pickle.dumps(value, protocol=pickle.HIGHEST_PROTOCOL)).decode('ascii')
        # Verify the server signature BEFORE unpickling. Database contents are
        # not an executable input just because someone can write a cache row.
        return {'number': None, 'value': signing.Signer(salt='blaqvibes.database-cache').sign(raw)}

    @staticmethod
    def _decode(entry, default):
        if entry.number is not None:
            return entry.number
        try:
            raw = signing.Signer(salt='blaqvibes.database-cache').unsign(entry.value)
            return pickle.loads(base64.b64decode(raw))
        except Exception:
            logger.warning('Ignoring an invalid database cache value')
            return default

    def _prune(self):
        self._writes += 1
        if self._writes % 100 == 0:
            self.entries.filter(expires__lte=django_timezone.now()).delete()

    def get(self, key, default=None, version=None):
        entry = self.entries.filter(self._live(), pk=self._key(key, version)).first()
        return default if entry is None else self._decode(entry, default)

    def get_many(self, keys, version=None):
        key_map = {self._key(key, version): key for key in keys}
        missing = object()
        result = {}
        for entry in self.entries.filter(self._live(), pk__in=key_map):
            value = self._decode(entry, missing)
            if value is not missing:
                result[key_map[entry.pk]] = value
        return result

    def set(self, key, value, timeout=DEFAULT_TIMEOUT, version=None):
        defaults = {**self._encode(value), 'expires': self._expires(timeout)}
        self.entries.update_or_create(pk=self._key(key, version), defaults=defaults)
        self._prune()
        return True

    def add(self, key, value, timeout=DEFAULT_TIMEOUT, version=None):
        key = self._key(key, version)
        defaults = {**self._encode(value), 'expires': self._expires(timeout)}
        _, created = self.entries.get_or_create(pk=key, defaults=defaults)
        if not created:
            # A conditional UPDATE, not a read-then-unconditional-write: only
            # one contender can replace an expired key with a live counter.
            created = bool(self.entries.filter(pk=key, expires__lte=django_timezone.now()).update(**defaults))
        self._prune()
        return created

    def incr(self, key, delta=1, version=None):
        key = self._key(key, version)
        with transaction.atomic(using=self.database):
            # Acquire the write lock BEFORE reading. It is retained until the
            # count is returned, so concurrent callers each get their own count.
            changed = self.entries.filter(self._live(), pk=key, number__isnull=False).update(
                number=models.F('number') + delta,
            )
            if not changed:
                raise ValueError('Key is missing, expired, or not an integer')
            count = self.entries.values_list('number', flat=True).get(pk=key)
        self._prune()
        return count

    def touch(self, key, timeout=DEFAULT_TIMEOUT, version=None):
        return bool(self.entries.filter(self._live(), pk=self._key(key, version)).update(
            expires=self._expires(timeout),
        ))

    def delete(self, key, version=None):
        return bool(self.entries.filter(pk=self._key(key, version)).delete()[0])

    def delete_many(self, keys, version=None):
        self.entries.filter(pk__in=[self._key(key, version) for key in keys]).delete()

    def clear(self):
        # Clearing a UI cache must not also reset the separate rate-limit cache.
        self.entries.filter(cache_key__startswith=f'{self.key_prefix}:').delete()
        return True
