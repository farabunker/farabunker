"""Query-count coverage for the endpoint-override path on `ConsoleView`
(Minor, whole-branch review final wave).

`models/registry/tests/test_views_machine_add_and_dropdowns.py::
TestConsoleViewQueryCount::test_query_count_does_not_grow_with_more_
registered_roles` already pins the NO-OVERRIDE GET at 10 queries. This
module is its sibling for a GET carrying a PERMITTED loopback override
(`?endpoint=http://127.0.0.1:9999`, distinct from the box's resolved
default `settings.OLLAMA_BASE_URL`) -- added as its own module rather
than appended to that one, per this plan's constraint 18: the
`test_views_*.py` split is another session's stewarded directory, and a
task needing a registry-console test adds a new module instead of
editing one of the six.

A LOOPBACK override is the case that matters here: `_override_is_permitted`
short-circuits on `_host_is_loopback` before it ever runs the
`ModelConnection.objects.values_list("endpoint", flat=True)` query its
non-loopback branch uses to check a registered connection/default-endpoint
match -- so a loopback override adds no query of its own beyond what the
no-override path already pays. This test is what pins that fact: it
asserts the SAME 10, so a future change that makes the override
resolution path (`_requested_override`/`_endpoint_context`) do its own
extra fetch -- rather than reusing `IdentityGateMiddleware`'s already-
stashed settings row, as `_requested_override`'s own docstring promises
-- is caught here rather than discovered as an N+1 later.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest
from django.urls import reverse

from models.registry.models import ModelConnection
from models.registry.tests._helpers import (
    ENDPOINT,
    _mock_engine,
    clean_probe_cache,
    client,  # noqa: F401 -- requested by name as a fixture
    clear_seeded_rows,
)


@pytest.fixture(autouse=True)
def _clear_seeded_rows_fixture(db):
    """Same body as every `test_views_*.py` module's own copy (C-56a) --
    this file is deliberately a new module, not an addition to one of
    those six, so it carries its own copy rather than importing theirs."""
    clear_seeded_rows()


@pytest.fixture(autouse=True)
def _clean_probe_cache_fixture():
    """Same body/rationale as `test_views_machine_add_and_dropdowns.py`'s
    own copy: `probe_cache` is not itself atomic-block-inert, so it is
    invalidated before AND after every test in this module."""
    clean_probe_cache()
    yield
    clean_probe_cache()


@pytest.mark.django_db
class TestConsoleViewQueryCountWithOverride:
    @patch("models.registry.views.discover")
    @patch("models.registry.views.ENGINES")
    def test_a_permitted_loopback_override_costs_the_same_ten_queries(
        self, mock_engines, mock_discover, client, django_assert_num_queries
    ):
        # Same 10 as the no-override baseline
        # (test_views_machine_add_and_dropdowns.py::TestConsoleViewQueryCount),
        # and the same breakdown: (1) IdentityGateMiddleware's one
        # `accounts_on()` read of the posture singleton (primed below so
        # its one-time row-creation cost isn't counted in the assertion
        # window) -- reused, not re-fetched, by `_requested_override`'s
        # own `settings_row_for(request)`; (2) `connections`
        # (`ModelConnection.objects.all()`'s plain name order, feeding
        # "Registered connections"); (3) `picker_connections`
        # (`ModelConnection.objects.picker_order()`, feeding role
        # dropdowns); (4) `all_model_sets` (every `ModelSet` row, for the
        # membership multiselect); (5) `_connection_views`'s own bulk
        # `ModelSetMember` read (every connection's current memberships,
        # in ONE query); (6)
        # `models.registry.context_processors.availability`'s
        # `values_list("role_key")` over `RoleBinding`, run live here
        # because a `django_db` test sits inside an atomic block where
        # that module's process cache deliberately refuses to cache; and
        # (7) `agents.chat.context_processors.settings_assistant`'s one
        # agent-row read for the COLLAPSED panel -- `inference-console`
        # ("Models") is one of the settings-area card routes, so the
        # processor does not early-out on it (spec S11's `+1`, merged in
        # with the settings assistant). The
        # loopback override itself adds NOTHING: `_override_is_permitted`
        # returns True on `_host_is_loopback` alone, before it would ever
        # reach its own `ModelConnection.objects.values_list("endpoint",
        # flat=True)` branch.
        from identity.models import IdentitySettings
        IdentitySettings.get_solo()
        ModelConnection.objects.create(
            name="conn", engine="ollama", endpoint=ENDPOINT, model_id="llama3.1:8b", capabilities=["chat"],
        )
        mock_engines.values.return_value = [_mock_engine(healthy=True)]
        mock_discover.return_value = []

        with django_assert_num_queries(10):
            client.get(reverse("inference-console"), {"endpoint": "http://127.0.0.1:9999"})
