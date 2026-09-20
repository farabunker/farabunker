"""The availability signal: which surfaces have a model bound (UI-1).

TWO LAYERS, TESTED SEPARATELY. `models.registry.availability` answers
"which role keys are bound", including how that answer is cached;
`models.registry.context_processors.availability` turns that set into the
composite rules the shell and the landing page render from (Ask needs two
roles, Images needs the feature flag as well).

THE ENV HALF IS PINNED, NOT INHERITED. `availability()` unions in the two
roles `models.contracts.bindings.env_provider` can resolve
(`settings.LLM_MODEL`/`EMBED_MODEL`), so every test below that asserts a
role is NOT available has to say so about the environment too -- a
developer with `EMBED_MODEL` in their `.env` would otherwise turn those
assertions red for a reason that has nothing to do with the code. The
same ambient-dependency rule the repo already applies to
`FARABUNKER_FEATURES` (commit ea5157d).

THE CACHE TESTS RUN `django_db(transaction=True)` ON PURPOSE. Inside an
ordinary `django_db` test every query runs in an open atomic block, and
`bound_role_keys()` deliberately refuses to consult or fill the process
cache there (see that module's docstring: a value read inside a
transaction may be rolled back, and caching it would publish a binding
that never committed). That is also what keeps a cached set from leaking
between ordinary tests -- so the only way to exercise the cached path for
real is a test with no wrapping transaction, which is what
`transaction=True` gives.
"""
from __future__ import annotations

from time import monotonic

import pytest

from models.contracts.roles import (
    CHAT_CONVERSE_ROLE, RAG_ANSWER_ROLE, RAG_EMBED_ROLE, VISION_GENERATE_ROLE,
)
from models.registry import availability as availability_module
from models.registry.availability import bound_role_keys, invalidate
from models.registry.context_processors import availability
from models.registry.models import RoleBinding
from models.registry.tests._helpers import (
    bind, clear_seeded_rows, make_chat_connection, make_embed_connection,
)


@pytest.fixture(autouse=True)
def _clear_seeded_rows(db):
    """Migration 0002 seeds a connection+binding pair for every role
    pinned from the environment, so whether this database starts out with
    something bound depends on the machine. Clearing makes every "nothing
    is bound" assertion below a fact rather than a hope -- the same
    fixture body `test_discovery.py`, `test_db_bindings.py` and
    `test_views.py` already use."""
    clear_seeded_rows()


@pytest.fixture
def _clean_cache():
    """No cached set before or after. Only the `transaction=True` tests
    need this: those run with no wrapping transaction, so what they cache
    would otherwise outlive them and be visible to the next
    non-transactional test in the same process."""
    invalidate()
    yield
    invalidate()


@pytest.mark.django_db
class TestBoundRoleKeys:
    def test_an_unbound_box_reports_nothing_bound(self):
        assert bound_role_keys() == frozenset()

    def test_a_bound_role_is_reported(self):
        bind(CHAT_CONVERSE_ROLE, make_chat_connection())
        assert bound_role_keys() == frozenset({CHAT_CONVERSE_ROLE})

    def test_a_binding_row_with_no_connection_is_not_bound(self):
        """`RoleBinding.connection` is nullable and `SET_NULL` -- a row
        left behind by a deleted connection means UNBOUND, which is
        exactly what `models.registry.bindings._bound_connection` already
        answers for it."""
        RoleBinding.objects.create(role_key=CHAT_CONVERSE_ROLE, connection=None)
        assert bound_role_keys() == frozenset()

    def test_the_keys_come_back_lowercased(self):
        """`role_key` is CI-unique and matched with `iexact` everywhere
        else, so a row stored in another casing must still answer to the
        lowercase literals in `models.contracts.roles`."""
        bind(CHAT_CONVERSE_ROLE.upper(), make_chat_connection())
        assert CHAT_CONVERSE_ROLE in bound_role_keys()

    def test_it_reads_live_inside_a_transaction(self):
        """The anti-staleness half of the cache rule: a binding written
        in this transaction is visible to the very next call, with no
        invalidation step in between."""
        assert bound_role_keys() == frozenset()
        bind(CHAT_CONVERSE_ROLE, make_chat_connection())
        assert bound_role_keys() == frozenset({CHAT_CONVERSE_ROLE})


@pytest.mark.django_db(transaction=True)
class TestTheProcessCache:
    def test_a_second_call_runs_no_query(self, django_assert_num_queries, _clean_cache):
        bind(CHAT_CONVERSE_ROLE, make_chat_connection())
        assert bound_role_keys() == frozenset({CHAT_CONVERSE_ROLE})
        with django_assert_num_queries(0):
            assert bound_role_keys() == frozenset({CHAT_CONVERSE_ROLE})

    def test_binding_a_role_invalidates_it(self, _clean_cache):
        assert bound_role_keys() == frozenset()
        bind(CHAT_CONVERSE_ROLE, make_chat_connection())
        assert bound_role_keys() == frozenset({CHAT_CONVERSE_ROLE})

    def test_deleting_the_connection_invalidates_it(self, _clean_cache):
        """The bulk-UPDATE case `ModelConnection`'s own `post_delete`
        receiver exists for: `SET_NULL` clears `RoleBinding.connection`
        without emitting a single `post_save`."""
        connection = make_chat_connection()
        bind(CHAT_CONVERSE_ROLE, connection)
        assert bound_role_keys() == frozenset({CHAT_CONVERSE_ROLE})
        connection.delete()
        assert bound_role_keys() == frozenset()

    def test_deleting_the_binding_row_invalidates_it(self, _clean_cache):
        binding = bind(CHAT_CONVERSE_ROLE, make_chat_connection())
        assert bound_role_keys() == frozenset({CHAT_CONVERSE_ROLE})
        binding.delete()
        assert bound_role_keys() == frozenset()

    def test_an_expired_entry_is_re_read(self, _clean_cache):
        """The TTL fallback: the convergence path for a SECOND process,
        which never sees the first one's signals. Simulated by ageing the
        cached entry rather than by sleeping -- what matters is that an
        entry past its expiry is not served."""
        bind(CHAT_CONVERSE_ROLE, make_chat_connection())
        assert bound_role_keys() == frozenset({CHAT_CONVERSE_ROLE})
        availability_module._CACHE = (frozenset({"a.stale.role"}), monotonic() - 1.0)
        assert bound_role_keys() == frozenset({CHAT_CONVERSE_ROLE})

    def test_a_read_invalidated_mid_flight_is_not_published(self, _clean_cache, monkeypatch):
        """The read-then-store window. Without the generation check, a
        thread that read a pre-write snapshot would store it AFTER the
        writer's `invalidate()` had already cleared the cache --
        republishing a set that was known-stale before it was ever
        written, for a full TTL.

        Driven deterministically rather than with threads: the read
        itself invalidates, which is exactly the interleaving being
        pinned, and it needs no timing luck to reproduce."""
        real_read = availability_module._read_bound_role_keys

        def _read_then_invalidate():
            value = real_read()
            invalidate()
            return value

        monkeypatch.setattr(
            availability_module, "_read_bound_role_keys", _read_then_invalidate,
        )
        assert bound_role_keys() == frozenset()
        assert availability_module._CACHE is None

    def test_an_unexpired_entry_is_served_even_when_it_is_wrong(self, _clean_cache):
        """The honest statement of the trade: within the TTL, a set this
        process cached IS what every template on it sees. Pinned so the
        window is a decision on the record rather than a surprise."""
        availability_module._CACHE = (
            frozenset({"a.cached.role"}), monotonic() + 60.0,
        )
        assert bound_role_keys() == frozenset({"a.cached.role"})


@pytest.mark.django_db
class TestTheContextProcessor:
    @pytest.fixture(autouse=True)
    def _no_environment_override(self, settings):
        """No role pinned from the environment unless a test says so --
        see this module's docstring."""
        settings.LLM_MODEL = None
        settings.EMBED_MODEL = None

    def test_nothing_bound_offers_no_surface(self, settings):
        settings.FARABUNKER_FEATURES = frozenset({"vision"})
        context = availability(None)
        assert context["bound_roles"] == frozenset()
        assert context["surface_available"] == {
            "chat": False, "ask": False, "search": False, "images": False,
            "any_model_surface": False,
        }

    def test_the_conversation_role_opens_chat_and_nothing_else(self, settings):
        settings.FARABUNKER_FEATURES = frozenset({"vision"})
        bind(CHAT_CONVERSE_ROLE, make_chat_connection())
        surfaces = availability(None)["surface_available"]
        assert surfaces["chat"] is True
        assert surfaces["any_model_surface"] is True
        assert (surfaces["ask"], surfaces["search"], surfaces["images"]) == (
            False, False, False,
        )

    def test_the_answer_role_alone_does_not_open_ask(self, settings):
        """Ask retrieves before it answers: an answer model with no
        embedding model behind it can only ever say "nothing matched"."""
        settings.FARABUNKER_FEATURES = frozenset({"vision"})
        bind(RAG_ANSWER_ROLE, make_chat_connection())
        surfaces = availability(None)["surface_available"]
        assert surfaces["ask"] is False
        assert surfaces["search"] is False

    def test_the_embedding_role_alone_opens_search_but_not_ask(self, settings):
        settings.FARABUNKER_FEATURES = frozenset({"vision"})
        bind(RAG_EMBED_ROLE, make_embed_connection())
        surfaces = availability(None)["surface_available"]
        assert surfaces["search"] is True
        assert surfaces["ask"] is False

    def test_both_rag_roles_open_ask(self, settings):
        settings.FARABUNKER_FEATURES = frozenset({"vision"})
        bind(RAG_ANSWER_ROLE, make_chat_connection())
        bind(RAG_EMBED_ROLE, make_embed_connection())
        surfaces = availability(None)["surface_available"]
        assert surfaces["ask"] is True
        assert surfaces["search"] is True

    def test_an_environment_pinned_pair_opens_ask_and_search_with_no_binding_row(
            self, settings):
        """`resolve()` is a db -> env chain: a role an operator pinned
        from the environment IS bound, the Models console already
        says so, and Ask/Search answer perfectly well on that path. A nav
        that hid them -- and a front door announcing "No models are set
        up yet" -- would be this box contradicting itself."""
        settings.FARABUNKER_FEATURES = frozenset({"vision"})
        settings.LLM_MODEL = "an-identifier"
        settings.EMBED_MODEL = "another-identifier"

        context = availability(None)

        assert {RAG_ANSWER_ROLE, RAG_EMBED_ROLE} <= context["bound_roles"]
        surfaces = context["surface_available"]
        assert surfaces["ask"] is True
        assert surfaces["search"] is True
        assert surfaces["any_model_surface"] is True

    def test_an_answer_model_alone_in_the_environment_opens_neither(self, settings):
        """The same composite rule as the DB path: Ask retrieves before it
        answers, so an environment-pinned answer model with no embedding
        model behind it is still a dead end."""
        settings.FARABUNKER_FEATURES = frozenset({"vision"})
        settings.LLM_MODEL = "an-identifier"

        surfaces = availability(None)["surface_available"]

        assert surfaces["ask"] is False
        assert surfaces["search"] is False

    def test_the_environment_half_mixes_with_the_bound_half(self, settings):
        """One role from each source is still both roles."""
        settings.FARABUNKER_FEATURES = frozenset({"vision"})
        settings.EMBED_MODEL = "another-identifier"
        bind(RAG_ANSWER_ROLE, make_chat_connection())

        assert availability(None)["surface_available"]["ask"] is True

    def test_the_environment_half_never_enters_the_cached_set(self, settings):
        """Placement, pinned: the env roles are unioned in by THIS
        function, not by `bound_role_keys()`. If they leaked into the
        cached set, a `settings` change would take up to a TTL to show
        up instead of applying on the next render."""
        settings.FARABUNKER_FEATURES = frozenset({"vision"})
        settings.LLM_MODEL = "an-identifier"

        assert RAG_ANSWER_ROLE in availability(None)["bound_roles"]
        assert RAG_ANSWER_ROLE not in bound_role_keys()

        settings.LLM_MODEL = None
        assert RAG_ANSWER_ROLE not in availability(None)["bound_roles"]

    def test_images_needs_the_feature_flag_as_well_as_the_role(self, settings):
        bind(VISION_GENERATE_ROLE, make_chat_connection(name="an image model"))
        settings.FARABUNKER_FEATURES = frozenset({"vision"})
        assert availability(None)["surface_available"]["images"] is True
        settings.FARABUNKER_FEATURES = frozenset()
        assert availability(None)["surface_available"]["images"] is False
