"""Test infrastructure: a runner that isolates the cache between tests.

Django rolls the database back after every test but leaves the cache alone,
and the default LocMem cache lives for the whole ``manage.py test`` process.
The performance work caches data-driven UI for 30–600 s — footer contacts,
social buttons, the Nolo backend label, unread notification counts, trending
scores, remix totals, the cached anonymous feed. Once one test rendered a
page, later tests read that test's cached values (user pks collide across
tests and catalogues are rebuilt from scratch), so tests failed in the suite
that pass — and still pass — run alone.

Flushing the default cache after every test keeps caching fully functional
*inside* a test (the cached-feed tests still exercise real warm hits) while
guaranteeing each test starts from the cold-cache state a fresh process
would see. Only a local (locmem/dummy) default cache is flushed: tests never
run against Redis (settings select it only for non-local deployments), and a
test process must never flush a shared production cache backend.
"""
from unittest import TextTestResult

from django.conf import settings
from django.core.cache import cache
from django.test.runner import DebugSQLTextTestResult, DiscoverRunner, PDBDebugResult


def _default_cache_is_local():
    backend = settings.CACHES.get('default', {}).get('BACKEND', '')
    return 'locmem' in backend or 'dummy' in backend


def flush_default_cache():
    """Drop every key in the default cache — but only if it is a local one."""
    if not _default_cache_is_local():
        return
    try:
        cache.clear()
    except Exception:
        # A cache that refuses to clear must not mask the real test result.
        pass


class _CacheFlushingMixin:
    """Drop the cache the moment a test ends — pass, fail, error or skip."""

    def stopTest(self, test):
        super().stopTest(test)
        flush_default_cache()


class CacheFlushingTextTestResult(_CacheFlushingMixin, TextTestResult):
    pass


class CacheFlushingDebugSQLTextTestResult(_CacheFlushingMixin, DebugSQLTextTestResult):
    pass


class CacheFlushingPDBDebugResult(_CacheFlushingMixin, PDBDebugResult):
    pass


class CacheIsolatedDiscoverRunner(DiscoverRunner):
    """DiscoverRunner whose result class flushes the cache after each test.

    The result class is the one hook the stock runner invokes after *every*
    test (stopTest fires on all four outcomes), so no per-test mixin has to
    be threaded through hundreds of test classes. The debug-sql and pdb
    variants are wrapped too, so `--debug-sql` and `--pdb` keep the flush.
    """

    def get_resultclass(self):
        if self.debug_sql:
            return CacheFlushingDebugSQLTextTestResult
        if self.pdb:
            return CacheFlushingPDBDebugResult
        return CacheFlushingTextTestResult
