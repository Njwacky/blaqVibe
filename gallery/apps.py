from django.apps import AppConfig

class GalleryConfig(AppConfig):
    default = True
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'gallery'

    def ready(self):
        # Skills live in their own module so the growing product model file
        # does not become a second monolith. Importing here registers them
        # with Django's app registry before checks/migrations run.
        from . import skill_models  # noqa: F401
        from django.db.models.signals import post_migrate
        post_migrate.connect(_seed_after_migrate, sender=self)

def _seed_after_migrate(sender, **kwargs):
    from django.conf import settings
    if not getattr(settings, 'SEED_DEMO', False):
        return
    try:
        from gallery.models import AppProject
        if AppProject.objects.filter(status='published').exists():
            return
        from gallery.seed import seed_demo
        seed_demo()
    except RuntimeError as exc:
        # `seed_demo()` refuses outside a dev posture. Never break `migrate`
        # over it — the deploy that needs its schema must still get its schema —
        # but say so: a swallowed refusal is how "the demo data is missing"
        # arrives as a database bug three days later.
        import logging
        logging.getLogger(__name__).error('post-migrate seed refused: %s', exc)
    except Exception:
        # Empty DB during some migrate steps — command / first request will retry.
        pass
