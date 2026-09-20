"""The principal vocabulary -- kinds, constants, the anonymous sentinel,
and the payload round-trip.

NO Django, NO database: every assertion here is about plain values, so
this module carries no `pytest.mark.django_db` and no settings override.
"""
from __future__ import annotations

import pytest

from identity.contracts.principals import (
    ANONYMOUS, OPEN_PRINCIPAL, PRINCIPAL_KINDS, Principal, SERVICE_PRINCIPAL,
    payload_fields, principal_from_payload,
)


class TestKinds:
    def test_the_vocabulary_is_closed_and_carries_the_two_historical_kinds(self):
        """`resident_agent`/`user_agent` stay in the tuple after the
        acting rule stops minting them: `ToolInvocation` rows written
        before IA-1 carry them, and a closed vocabulary that cannot
        describe its own stored data is a vocabulary that lies."""
        assert PRINCIPAL_KINDS == (
            "open", "user", "service", "resident_agent", "user_agent",
        )

    def test_an_unknown_kind_is_a_construction_error(self):
        with pytest.raises(ValueError) as exc:
            Principal("nobody", "x")
        assert "nobody" in str(exc.value)

    def test_a_blank_key_is_a_construction_error(self):
        with pytest.raises(ValueError):
            Principal("user", "")

    def test_a_principal_is_frozen_and_hashable(self):
        """Hashable so a later grant cache can key on it; un-repointable
        so nothing downstream can change who a half-finished turn is
        acting as."""
        principal = Principal("user", "7")
        assert {principal: 1}[Principal("user", "7")] == 1
        with pytest.raises(Exception):
            principal.key = "8"


class TestTheTwoConstants:
    def test_the_open_principal_is_the_box(self):
        assert OPEN_PRINCIPAL == Principal("open", "box")

    def test_there_is_exactly_one_service_principal(self):
        """One kind for every machine caller (the watcher, `ingest`,
        `ask`, `agent_turn`), not one per caller: WHICH of them acted is
        recorded in the audit row's `detail`, not in a key that would
        become a grants-table join value."""
        assert SERVICE_PRINCIPAL == Principal("service", "local")


class TestAnonymous:
    def test_it_is_not_a_principal_and_anonymous_is_not_a_kind(self):
        """A SEPARATE TYPE, deliberately. Making it a fifth kind would
        mean the first person to write `Q(owner_kind="anonymous")` gave
        "nobody" a set of rows to own."""
        assert not isinstance(ANONYMOUS, Principal)
        assert "anonymous" not in PRINCIPAL_KINDS
        with pytest.raises(ValueError):
            Principal("anonymous", "x")

    def test_it_reads_like_a_principal_for_the_two_attributes_that_matter(self):
        """So every access function reads it with the same two attribute
        lookups and needs no isinstance branch."""
        assert ANONYMOUS.kind == "anonymous"
        assert ANONYMOUS.key == ""


class TestPayloadRoundTrip:
    def test_the_fields_are_the_two_keys_every_job_payload_carries(self):
        assert payload_fields(Principal("user", "42")) == {
            "actor_kind": "user", "actor_key": "42",
        }

    def test_it_round_trips(self):
        for principal in (OPEN_PRINCIPAL, SERVICE_PRINCIPAL, Principal("user", "9")):
            assert principal_from_payload(payload_fields(principal)) == principal

    def test_a_payload_with_no_actor_reads_back_as_the_open_principal(self):
        """Every job enqueued before this phase. The open principal is
        the honest answer for a row written by a box that had no users --
        and `models/queue/visibility.py` still refuses it to a member,
        because a member is not the open principal."""
        assert principal_from_payload({}) == OPEN_PRINCIPAL

    def test_a_payload_with_a_junk_kind_reads_back_as_the_open_principal(self):
        """Never a raise: this is called from a job handler on the
        worker, where a raise is a failed job, and from the queue page,
        where a raise is a 500."""
        assert principal_from_payload(
            {"actor_kind": "wizard", "actor_key": "x"}) == OPEN_PRINCIPAL

    def test_a_non_dict_payload_reads_back_as_the_open_principal(self):
        assert principal_from_payload(None) == OPEN_PRINCIPAL
        assert principal_from_payload([1, 2]) == OPEN_PRINCIPAL
