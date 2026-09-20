"""URL route for the landing page, mounted at / (see config/urls.py)."""
from django.urls import path

from foundation.landing.views import LandingView

urlpatterns = [
    path("", LandingView.as_view(), name="landing"),
]
