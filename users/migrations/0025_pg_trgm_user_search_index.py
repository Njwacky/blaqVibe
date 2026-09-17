"""Trigram indexes for the admin user search (Postgres, best effort).

`users/user_search.py` ranks on `username`/`email` with `icontains` plus, on
Postgres, `TrigramSimilarity`, so a typo like "kwamme" still finds @kwame.
Without an index, `icontains` and `%` matching cannot use the btree index on
`auth_user.username`: at 40k users that is a sequential scan per keystroke.

Why best effort and not a hard migration:

* `CREATE EXTENSION pg_trgm` needs privileges a managed Postgres may not grant
  the app role. That is a fact about the host, not a bug in the deploy — and
  search already degrades to the portable path when trigram is unavailable.
* `CREATE INDEX CONCURRENTLY` cannot run inside a transaction, so this
  migration is `atomic = False` and every statement runs in its own
  transaction. A failure leaves the others applied, and the migration is
  recorded so the next deploy is not blocked by an optional index.
* Every statement is idempotent (`IF NOT EXISTS`), so re-running by hand after
  granting the extension is safe.

Operators who would rather not have the app role create extensions can run the
three statements in this file once, in the Supabase SQL editor, and the
migration then finds them already present.
"""
from django.db import migrations

ADD_EXTENSION = 'CREATE EXTENSION IF NOT EXISTS pg_trgm'

INDEXES = (
    # username: prefix/contains AND trigram similarity both use this one.
    ('users_user_username_trgm', 'auth_user', 'username'),
    ('users_user_email_trgm', 'auth_user', 'email'),
    # allauth stores confirmed alternative addresses here; the search matches
    # them too, so they need the same treatment.
    ('users_emailaddress_email_trgm', 'account_emailaddress', 'email'),
)

DROP_INDEXES = (
    'users_user_username_trgm',
    'users_user_email_trgm',
    'users_emailaddress_email_trgm',
)


def _run(schema_editor, statements):
    """Execute, logging and swallowing failures: an optional index must never
    fail a deploy. Runs outside a transaction (see the docstring)."""
    import logging
    logger = logging.getLogger(__name__)
    connection = schema_editor.connection
    with connection.cursor() as cursor:
        for statement in statements:
            try:
                cursor.execute(statement)
            except Exception as exc:  # privileges, missing extension, lock
                logger.warning(
                    'pg_trgm user-search index skipped (%s): %s', exc, statement)


def add_trigram_indexes(apps, schema_editor):
    if schema_editor.connection.vendor != 'postgresql':
        # SQLite (dev/CI) has no pg_trgm and no GIN. Search works there too —
        # it just is not typo-tolerant.
        return
    statements = [ADD_EXTENSION] + [
        f'CREATE INDEX CONCURRENTLY IF NOT EXISTS {name} '
        f'ON {table} USING gin ({column} gin_trgm_ops)'
        for name, table, column in INDEXES
    ]
    _run(schema_editor, statements)


def drop_trigram_indexes(apps, schema_editor):
    if schema_editor.connection.vendor != 'postgresql':
        return
    _run(schema_editor, [
        f'DROP INDEX CONCURRENTLY IF EXISTS {name}' for name in DROP_INDEXES
    ])


class Migration(migrations.Migration):

    # CONCURRENTLY cannot run inside a transaction; the extension and the index
    # builds are independent statements on purpose (see module docstring).
    atomic = False

    dependencies = [
        ('users', '0024_remove_sitesettings_legacy_footer_fields'),
        ('account', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(add_trigram_indexes, drop_trigram_indexes),
    ]
