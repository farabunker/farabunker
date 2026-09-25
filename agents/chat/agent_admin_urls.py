"""The agent library's one route, mounted at `/settings/agents/` by
`config/urls.py`.

ITS OWN URLconf rather than one more entry in `agents/chat/urls.py`,
EXACTLY the `agents/chat/assistant_urls.py` precedent and for that
module's own stated reason: this is not a `/chat/` route, it is a
settings-area surface whose view happens to read `agents/` rows.
Mounting it beside `/settings/` is what makes the URL an operator sees
match the page they are on, and `config/` is the composition root that
already imports every column, so nothing crosses a boundary to put it
in front.

THE COST OF THAT CHOICE IS ONE TEST MODULE. `agents/chat/tests/
test_never_500.py` derives its sweep from `agents.chat.urls.urlpatterns`
and cannot see a route mounted from anywhere else, so this route's
never-500 proof lives in `agents/chat/tests/test_settings_agents.py` --
exactly the position the three `/settings/assistant/` routes are in.
"""
from django.urls import path

from agents.chat.views import agents_admin_list

urlpatterns = [
    path("", agents_admin_list, name="settings-agents"),
]
