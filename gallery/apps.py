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
    """Always ensure the four base categories exist (needed for the publish
    dropdown). Run SEED_DEMO content only when explicitly requested."""
    # 1. Base categories — needed on every fresh deploy so the publish
    #    form dropdown is never empty.
    try:
        from gallery.seed import CATEGORIES
        from gallery.models import Category
        for slug, name, typ, order in CATEGORIES:
            cat, _ = Category.objects.get_or_create(
                slug=slug,
                defaults={'name': name, 'type': typ, 'order': order},
            )
            if cat.name != name or cat.order != order:
                cat.name = name
                cat.order = order
                cat.save(update_fields=['name', 'order'])
    except Exception:
        # Table not yet created during an early migrate step — safe to skip;
        # the next migrate invocation will retry.
        pass

    # 2. Demo content — only when operator explicitly sets SEED_DEMO=1.
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
