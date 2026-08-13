"""Back-compat wrapper. Prefer: python manage.py seed_demo"""
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'blaqvibes.settings')
django.setup()

from gallery.seed import seed_demo

print(seed_demo())
