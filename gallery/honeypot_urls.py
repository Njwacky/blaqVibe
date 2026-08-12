from django.http import HttpResponse
from django.urls import path
def honeypot(request):
    return HttpResponse("Admin not found", status=404)
urlpatterns = [path('', honeypot)]
