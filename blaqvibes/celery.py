import os
from celery import Celery
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'blaqvibes.settings')
app = Celery('blaqvibes')
app.config_from_object('django.conf:settings', namespace='CELERY')
app.autodiscover_tasks()

# 5 Whys: Why Celery? Why not run scan in request? 
# 1. Scan takes 2-8s — blocks HTTP worker. 2. Why Celery not thread? Thread dies on deploy. 3. Why Redis? Lightweight broker for 10k jobs. 4. Why autodiscover? No manual import. 5. Why eager fallback? Dev without Redis must still work.
