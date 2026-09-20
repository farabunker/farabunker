"""The landing page at `/` (UI-1 decision 3).

THE CLAIM: this page offers exactly the surfaces this box can actually
use, and says so honestly when that set is empty. So the tests below are
about which cards render, not about how they look.

`/` used to 404 -- `config/urls.py` had no `""` mount at all -- so
`test_the_root_url_answers` is not a formality: it is the regression this
page exists to fix.
"""
from __future__ import annotations

import pytest
from django.conf import settings as django_settings
from django.urls import reverse

from foundation.landing.tests._helpers import (
    bind_role, clear_bindings, make_user, posture, seed_sweep_posture, sign_in,
)
from identity.contracts.postures import POSTURE_ENTERPRISE, POSTURE_OPEN
from identity.routes import ROUTE_RULES
from models.contracts.roles import (
    CHAT_CONVERSE_ROLE, RAG_ANSWER_ROLE, RAG_EMBED_ROLE, VISION_GENERATE_ROLE,
)

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _fresh_box(db, settings):
    """Nothing bound -- from the DB OR from the environment.
    `availability()` unions in the roles `LLM_MODEL`/`EMBED_MODEL`
    resolve, so a developer with either in their `.env` would otherwise
    turn the empty-state tests red for a reason that has nothing to do
    with this page (the ambient-dependency rule of commit ea5157d)."""
    seed_sweep_posture()
    clear_bindings()
    settings.LLM_MODEL = None
    settings.EMBED_MODEL = None


def _body(client) -> str:
    response = client.get(reverse("landing"))
    assert response.status_code == 200, response.status_code
    return response.content.decode()


class TestTheRootUrl:
    def test_the_root_url_answers(self, client):
        with posture(POSTURE_OPEN):
            assert client.get("/").status_code == 200

    def test_it_is_classified_as_an_ordinary_page(self):
        """Class A, like /chat/ -- not P like Setup. The landing page is
        an inventory of what this box can do, and an accounts-on box does
        not hand that to somebody who has not signed in."""
        assert ROUTE_RULES["landing"] == "A"


class TestTheEmptyState:
    def test_nothing_bound_says_so_and_points_at_setup(self, client):
        with posture(POSTURE_OPEN):
            body = _body(client)
        assert "No models are set up yet." in body
        assert reverse("setup-index") in body
        assert reverse("inference-console") in body

    def test_a_bound_surface_removes_the_empty_state(self, client):
        bind_role(CHAT_CONVERSE_ROLE)
        with posture(POSTURE_OPEN):
            body = _body(client)
        assert "No models are set up yet." not in body


class TestTheCards:
    def test_the_document_library_card_is_always_offered(self, client):
        """Storage needs no model bound. Telling somebody with a fresh
        box that they cannot even put a file in it would be false."""
        with posture(POSTURE_OPEN):
            body = _body(client)
        assert f'href="{reverse("rag-documents")}"' in body

    def test_no_card_for_an_unbound_surface(self, client):
        with posture(POSTURE_OPEN):
            body = _body(client)
        assert f'class="card" href="{reverse("chat-index")}"' not in body
        assert f'class="card" href="{reverse("rag-ask-page")}"' not in body
        assert f'class="card" href="{reverse("rag-search")}"' not in body

    def test_the_conversation_role_offers_the_chat_card(self, client):
        bind_role(CHAT_CONVERSE_ROLE)
        with posture(POSTURE_OPEN):
            body = _body(client)
        assert f'class="card" href="{reverse("chat-index")}"' in body

    def test_the_embedding_role_alone_offers_search_but_not_ask(self, client):
        bind_role(RAG_EMBED_ROLE)
        with posture(POSTURE_OPEN):
            body = _body(client)
        assert f'class="card" href="{reverse("rag-search")}"' in body
        assert f'class="card" href="{reverse("rag-ask-page")}"' not in body

    def test_both_rag_roles_offer_the_ask_card(self, client):
        bind_role(RAG_ANSWER_ROLE)
        bind_role(RAG_EMBED_ROLE)
        with posture(POSTURE_OPEN):
            body = _body(client)
        assert f'class="card" href="{reverse("rag-ask-page")}"' in body


# `reverse("vision-create")` raises `NoReverseMatch` -- a collection-time
# error, not a skip -- when the flag is off, exactly as
# `identity/tests/test_zero_queries.py` guards its own vision entry.
@pytest.mark.skipif(
    "vision" not in django_settings.FARABUNKER_FEATURES,
    reason="`/vision/` is only mounted with the vision feature on",
)
class TestTheImageCardWithTheFeatureOn:
    """The FLAG-OFF half of the same rule is pinned one layer down, in
    `models/registry/tests/test_availability.py` -- deliberately, not by
    omission. `tools/rag/tests/test_flag_hygiene.py` forbids a
    `FARABUNKER_FEATURES` override without "vision" in any file that also
    touches the test `Client`/`reverse()`: the URLconf's `vision/` mount
    is built once, off whatever the flag is at the first resolution in
    the process, so an override here could poison every later vision test
    in the run. The availability module's own tests touch neither, and
    the template does nothing with the flag but read the one key they
    pin."""

    def test_the_generate_role_offers_the_image_card(self, client):
        bind_role(VISION_GENERATE_ROLE)
        with posture(POSTURE_OPEN):
            body = _body(client)
        assert f'class="card" href="{reverse("vision-create")}"' in body

    def test_no_image_card_without_the_role(self, client):
        with posture(POSTURE_OPEN):
            body = _body(client)
        assert f'class="card" href="{reverse("vision-create")}"' not in body


class TestTheGate:
    def test_an_anonymous_visitor_on_an_accounts_on_box_is_sent_to_sign_in(self, client):
        with posture(POSTURE_ENTERPRISE):
            response = client.get(reverse("landing"))
        assert response.status_code == 302
        # `endswith`, not `in`: `reverse("landing")` is "/", which is a
        # substring of the login path itself -- an `in` check here would
        # pass with `?next=` dropped entirely, pinning its own fixture
        # rather than the round-trip it claims to be about.
        assert response.url.endswith(f"?next={reverse('landing')}")
        assert response.url.startswith(reverse("identity-login"))

    def test_a_signed_in_member_reaches_it(self, client):
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, make_user())
            assert client.get(reverse("landing")).status_code == 200
