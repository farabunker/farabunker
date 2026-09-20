"""Farabunker URL configuration. Modules mount their own UI/API surface here."""
from django.conf import settings
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    # The front door (UI-1). `/` used to 404 -- a visitor had to know a
    # path before the box would speak to them. An EXACT-match empty
    # prefix, so it shadows nothing mounted below it.
    path("", include("foundation.landing.urls")),
    path("admin/", admin.site.urls),
    # Sign in, sign out, change password. UNGATED, exactly like /chat/
    # and /rag/: accounts are not optional machinery, and this is the
    # page that gets you INTO every other one.
    path("identity/", include("identity.urls")),
    path("rag/", include("tools.rag.urls")),
    # The conversation surface. UNGATED, exactly like /rag/ -- spec
    # section 8.1: a third FARABUNKER_FEATURES token would leave this
    # page untested in one of the two supported suite states
    # (docs/DEV.md:235-246).
    path("chat/", include("agents.chat.urls")),
    path("inference/", include("models.registry.urls")),
    path("queue/", include("models.queue.urls")),
    # The universal setup page: how to install every registered engine. Not
    # feature-gated -- it explains the engines themselves, which exist
    # regardless of which features are enabled.
    path("setup/", include("foundation.setup.urls")),
    # The settings area's landing route (UI-2): a redirect to the first
    # section the viewer may open, so the app bar's one `Settings` entry
    # never lands on a page that would refuse them. Nothing occupied
    # `settings/` before this -- the identity posture page is at
    # `/identity/settings/` and the library's at `/rag/settings/`, both
    # behind their own module's prefix.
    path("settings/", include("foundation.settings_area")),
    # The settings assistant's three routes (spec §8.1). Mounted HERE,
    # beside `/settings/` itself, rather than under `/chat/`: they are a
    # settings-area surface whose views happen to read `agents/` rows,
    # and `config/` is the composition root that already imports every
    # column, so nothing crosses a boundary to put them in front.
    path("settings/assistant/", include("agents.chat.assistant_urls")),
]

# Feature-gated mount (D9): with the "vision" feature off, the page simply
# does not exist -- no route, no role, no half-wired UI.
if "vision" in settings.FARABUNKER_FEATURES:
    urlpatterns.append(path("vision/", include("tools.vision.urls")))
