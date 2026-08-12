import os
try:
    import sentry_sdk
    from sentry_sdk.integrations.django import DjangoIntegration
    dsn = os.getenv('SENTRY_DSN', '')
    if dsn:
        sentry_sdk.init(dsn=dsn, integrations=[DjangoIntegration()], traces_sample_rate=0.2, send_default_pii=False)
        print("Sentry enabled")
    else:
        # No DSN — still init with dummy to catch silently, no network
        sentry_sdk.init(traces_sample_rate=0.0)
except Exception as e:
    print(f"Sentry not enabled: {e}")
