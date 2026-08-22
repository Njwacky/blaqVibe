from django.apps import AppConfig


class UsersConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'users'

    def ready(self):
        # Registers the socialaccount signal receivers. Imported here (not at
        # module import) because they touch models, which are not loaded yet
        # when Django builds the app registry.
        from . import signals  # noqa: F401
