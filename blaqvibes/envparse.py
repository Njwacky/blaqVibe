"""Readers for numeric environment variables — settings.py's knobs, done safely.

settings.py used to write `int(os.getenv(NAME, default))` inline. A typo in a
deploy then died with `invalid literal for int() with base 10: 'lots'` and no
variable name, so nobody could tell which of forty settings held the typo.

These readers fail just as loudly — a bad deploy should not start; the host
keeps serving the previous release — but the message names the variable and
shows what it held. A number that is merely too big or too small is NOT an
error: it is clamped, because a limit is a guard rail, not a mistake worth
taking the site down for.

Dependency-free on purpose (only Django's exception class), so settings.py can
import it before anything else is configured.
"""
import os
import re

from django.core.exceptions import ImproperlyConfigured

_WHOLE_NUMBER = re.compile(r'[+-]?[0-9]{1,9}')
# django-ratelimit's own pattern is `([\d]+)/([\d]*)([smhd])?` applied with
# .match(): it accepts "30/h; drop", raises AttributeError on "30 per hour"
# (at request time, where the role decorator would call it a 403), and treats
# "0/h" as "block everything". Stricter here, and checked when the process starts.
_RATE = re.compile(r'([1-9][0-9]{0,5})/([1-9][0-9]{0,3})?([smhd])')


def normalize_rate(value):
    """'30/h' -> '30/h'; ' 50 / 2H ' -> '50/2h'; anything else -> None."""
    if not isinstance(value, str):
        return None
    match = _RATE.fullmatch(re.sub(r'\s+', '', value).lower())
    if not match:
        return None
    count, every, unit = match.groups()
    return f'{count}/{every or ""}{unit}'


def env_int(name, default, *, minimum, maximum):
    """A whole number from the environment, clamped to [minimum, maximum].

    Unset or blank means `default`. Text that is not a whole number raises
    ImproperlyConfigured naming the variable.
    """
    raw = os.getenv(name)
    if raw is None or raw.strip() == '':
        value = default
    else:
        text = raw.strip()
        if not _WHOLE_NUMBER.fullmatch(text):
            raise ImproperlyConfigured(f'{name} must be a whole number, got {raw!r}.')
        value = int(text)
    return max(minimum, min(maximum, value))


def env_rate(name, default):
    """A rate such as '30/h' (count / optional multiple + s, m, h or d)."""
    raw = os.getenv(name)
    if raw is None or raw.strip() == '':
        return default
    rate = normalize_rate(raw)
    if rate is None:
        raise ImproperlyConfigured(
            f"{name} must look like '30/h' (a count, a slash, then s, m, h or d — "
            f"optionally with a multiple, '10/2h'), got {raw!r}."
        )
    return rate
