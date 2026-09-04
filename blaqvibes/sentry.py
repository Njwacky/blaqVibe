"""Sentry bootstrap for BlaqVibes.

Python 3.13 exposes frame locals as ``FrameLocalsProxy``. Older sentry-sdk
versions try to shallow-copy that proxy when serializing local variables, which
raises ``TypeError: cannot pickle 'FrameLocalsProxy' object`` and can mask the
real Django exception. Keep local-variable capture off until the SDK/runtime
combination is known-safe.
"""

import os


def init_sentry() -> None:
    dsn = os.getenv('SENTRY_DSN', '').strip()
    if not dsn:
        return

    try:
        import sentry_sdk
        from sentry_sdk.integrations.django import DjangoIntegration

        sentry_sdk.init(
            dsn=dsn,
            integrations=[DjangoIntegration()],
            traces_sample_rate=float(os.getenv('SENTRY_TRACES_SAMPLE_RATE', '0.2')),
            send_default_pii=False,
            include_local_variables=False,
        )
        print('Sentry enabled')
    except Exception as e:
        print(f'Sentry not enabled: {e}')
