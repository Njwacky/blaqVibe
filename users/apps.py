from django.apps import AppConfig


class UsersConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'users'

    def ready(self):
        # Registers the socialaccount signal receivers. Imported here (not at
        # module import) because they touch models, which are not loaded yet
        # when Django builds the app registry.
        from . import signals  # noqa: F401
        from django.db.models.signals import post_migrate
        post_migrate.connect(_provision_after_migrate, sender=self)


def _provision_after_migrate(sender, **kwargs):
    """Mint the operator account when DJANGO_SUPERADMIN_PASSWORD is set.

    5 Whys: why here, not only the management command? Operators put the
    password in .env and expect login to work after `migrate`. The command
    exists; nothing called it on boot, so "admin / my password" never
    resolved to a row.
    """
    try:
        from users.provision import maybe_provision_from_env
        maybe_provision_from_env()
    except Exception:
        import logging
        logging.getLogger(__name__).exception('superadmin provision failed')
