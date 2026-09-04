from django.apps import AppConfig


class GalleryConfig(AppConfig):
    default = True
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'gallery'

    def ready(self):
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
        import logging
        logging.getLogger(__name__).error('post-migrate seed refused: %s', exc)
    except Exception:
        pass
