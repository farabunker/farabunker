"""The mount, the label, and the nav -- proven by a real request.

NO `FARABUNKER_FEATURES` OVERRIDE ANYWHERE IN THIS PACKAGE. Every test
here uses `reverse()` or the test `Client`, which makes it exactly the
kind of test THE VISION-FLAG RULE (`tools/rag/tests/_helpers.py:9-38`)
exists to police: one flag override without `"vision"` in the set
poisons URL resolution for the whole process. `/chat/` is ungated, so
no test here has any reason to touch the flag at all.
"""
from __future__ import annotations

import pytest
from django.apps import apps
from django.urls import reverse

from models.contracts.roles import CHAT_CONVERSE_ROLE
from models.registry.models import ModelConnection, RoleBinding

pytestmark = pytest.mark.django_db


def _bind_converse():
    """Give `chat.converse` a model.

    UI-1 decision 2: the shell renders the Chat entry only when that role
    is bound, so a test about the entry has to bind it first. Nothing
    about this connection matters beyond its existing -- availability
    asks whether a binding HAS a connection, never whether the engine
    behind it answers.
    """
    connection = ModelConnection.objects.create(
        name="nav chat conn", engine="ollama", endpoint="http://localhost:11434",
        model_id="an-identifier", capabilities=["chat"],
    )
    RoleBinding.objects.create(role_key=CHAT_CONVERSE_ROLE, connection=connection)


class TestTheAppExists:
    def test_the_chat_app_is_installed_under_its_own_label(self):
        """Its own label, not `agents` -- template discovery
        (`APP_DIRS: True` finds `agents/chat/templates/`) and a future
        model both depend on it (spec section 7 preamble, deviation
        P3-D1)."""
        assert apps.get_app_config("chat").name == "agents.chat"

    def test_the_chat_app_owns_no_models(self):
        """P3 adds no chat table and no chat migration. A model
        appearing here is a migration nobody planned."""
        assert list(apps.get_app_config("chat").get_models()) == []


class TestTheMount:
    def test_chat_index_resolves_and_renders(self, client):
        response = client.get(reverse("chat-index"))
        assert response.status_code == 200

    def test_the_mount_is_ungated(self):
        """Section 8.1: not behind a FARABUNKER_FEATURES token,
        because `docs/DEV.md:235-246` fixes the two supported suite
        states and a third token would leave this surface untested in
        one of them. Read as source, so a later `if` around the mount
        fails here rather than silently in one gate state."""
        from pathlib import Path

        from django.conf import settings

        text = (Path(settings.BASE_DIR) / "config" / "urls.py").read_text()
        mount = 'path("chat/", include("agents.chat.urls"))'
        assert mount in text
        head, _, tail = text.partition(mount)
        # The one conditional block in this file is the vision mount at
        # the very end; the chat mount must sit ABOVE it, inside the
        # unconditional list.
        assert "if \"vision\" in settings.FARABUNKER_FEATURES" in tail
        assert "if \"vision\" in settings.FARABUNKER_FEATURES" not in head


class TestTheNav:
    def test_every_page_links_to_chat(self, client):
        """The shell owns ONE app bar so every page agrees on every
        destination. A link added to one page's own template would be
        the drift that shared bar exists to prevent."""
        _bind_converse()
        response = client.get(reverse("rag-ask-page"))
        assert reverse("chat-index") in response.content.decode()

    def test_no_page_links_to_chat_while_the_role_is_unbound(self, client):
        """UI-1 decision 2. `/chat/` is ungated by feature flag -- the
        route is always mounted -- but the ENTRY is availability-gated
        like every other use-surface: with no `chat.converse` binding
        there is nothing to converse with, and offering the link anyway
        would only take somebody to a page that apologises."""
        assert ">Chat</a>" not in client.get(reverse("rag-ask-page")).content.decode()

    def test_the_chat_page_has_the_standard_top_bar(self, client):
        """ROUND 15 (OWNER FEEDBACK), a PARTIAL REVERSAL of round 14's
        own amendment -- owner, verbatim: "the navigation bar needs to
        be consistant on the top, you should not override the main
        navigation template for main, what the fuck, this looks like
        dog shit." `chat/base.html` no longer overrides `{%
        templatetag openblock %} block nav {% templatetag closeblock
        %}` at all (round 14's own empty override is gone), so every
        chat page falls through to `foundation/templates/_shell.html`'s
        own DEFAULT block content -- the SAME top bar every other page
        on the box renders, byte-consistent rather than merely similar.
        `test_the_nav_stays_on_every_non_chat_page`, below, is this
        test's own other direction (never broken by round 14 or round
        15 alike -- non-chat pages never lost their bar)."""
        _bind_converse()
        response = client.get(reverse("chat-index"))
        body = response.content.decode()
        assert "<header class=\"app-bar\">" in body
        assert "<nav class=\"top-nav\"" in body
        # "Chat" marks itself current on its own page, the SAME
        # `nav_current_chat` override (unrelated to round 15's own
        # `{% templatetag openblock %} block nav {% templatetag
        # closeblock %}` change, and never touched by it) already gave
        # it before round 14 ever existed.
        assert '<a href="/chat/" class="current">Chat</a>' in body
        # The rail is SLIMMED, round 15, to chat-domain content only --
        # no brand/app-nav/account rows duplicating the restored bar.
        assert 'class="chat-rail-primary"' in body
        assert 'class="chat-rail-brand"' not in body

    def test_the_nav_stays_on_every_non_chat_page(self, client):
        """UNCHANGED BY EITHER ROUND: every page that does NOT extend
        `chat/base.html` keeps the ordinary top bar exactly as it
        always has. `rag-documents` is a page from a DIFFERENT column
        entirely (`tools/rag`), the least chat-adjacent choice
        available, so this is not merely proving the bar survives on a
        page nobody would have suspected of losing it."""
        response = client.get(reverse("rag-documents"))
        body = response.content.decode()
        assert "<header class=\"app-bar\">" in body
        assert "<nav class=\"top-nav\"" in body
