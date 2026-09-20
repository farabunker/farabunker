"""URL routes for the setup page, mounted at /setup/ (config/urls.py)."""
from django.urls import path

from foundation.setup.views import SetupView

urlpatterns = [
    path("", SetupView.as_view(), name="setup-index"),
]
