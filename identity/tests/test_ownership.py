"""The owned-rows registry -- five tables in three columns, named as
strings so `identity/` never imports `agents` or `tools`.

This module uses a MODULE-LEVEL autouse fixture that snapshots and
restores the registry, matching how every other registry in this
codebase is isolated in tests. No `conftest.py`.
"""
import pytest

from identity.contracts import ownership
from identity.contracts.ownership import OwnedRows, all_owned_rows, register_owned_rows


@pytest.fixture(autouse=True)
def _isolated_registry():
    saved = dict(ownership._OWNED)
    ownership._OWNED.clear()
    yield
    ownership._OWNED.clear()
    ownership._OWNED.update(saved)


class TestRegistry:
    def test_a_spec_is_plain_data_naming_its_model_as_a_string(self):
        """`app_label.ModelName`, resolved at command time by
        `django.apps.apps.get_model` -- never imported, exactly as a
        JobKind's handler is a dotted-path string. This is what lets
        `identity/` walk five tables in three columns it may not
        import."""
        spec = OwnedRows(key="agents.conversation", label="Conversations",
                         model="agents.Conversation")
        register_owned_rows(spec)
        assert all_owned_rows() == [spec]

    def test_registration_is_idempotent_and_last_wins(self):
        """Same shape as `register_role`/`register_job_kind`/
        `register_tool`: re-importing a module that registers at import
        time is safe."""
        register_owned_rows(OwnedRows("k", "First", "agents.Agent"))
        register_owned_rows(OwnedRows("k", "Second", "agents.Agent"))
        assert [s.label for s in all_owned_rows()] == ["Second"]

    def test_it_returns_registration_order(self):
        """The order the adoption command prints its per-table counts in.
        Stable, so an operator comparing two runs compares two identical
        lists."""
        register_owned_rows(OwnedRows("a", "A", "agents.Agent"))
        register_owned_rows(OwnedRows("b", "B", "agents.Flow"))
        assert [s.key for s in all_owned_rows()] == ["a", "b"]

    def test_a_blank_key_or_model_is_a_construction_error(self):
        with pytest.raises(ValueError):
            OwnedRows("", "A", "agents.Agent")
        with pytest.raises(ValueError):
            OwnedRows("a", "A", "")

    def test_a_model_string_without_a_dot_is_a_construction_error(self):
        """`apps.get_model` needs `app_label.ModelName`; catching the
        shape HERE means a bad registration fails at app-ready time,
        naming itself, rather than inside a transaction that is halfway
        through rewriting owners."""
        with pytest.raises(ValueError) as exc:
            OwnedRows("a", "A", "Agent")
        assert "app_label.ModelName" in str(exc.value)
