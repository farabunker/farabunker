"""The settings assistant's three routes, mounted at
`/settings/assistant/` by `config/urls.py`.

ITS OWN URLconf rather than three more entries in `agents/chat/urls.py`,
because these are not `/chat/` routes: they are a settings-area surface
whose views happen to read `agents/` rows. Mounting them beside
`/settings/` is what makes the URL an operator sees match the page they
are on.
"""
from django.urls import path

from agents.chat.views import assistant_ask, assistant_panel, assistant_reset

urlpatterns = [
    path("ask/", assistant_ask, name="settings-assistant-ask"),
    path("reset/", assistant_reset, name="settings-assistant-reset"),
    path("panel/", assistant_panel, name="settings-assistant-panel"),
]
