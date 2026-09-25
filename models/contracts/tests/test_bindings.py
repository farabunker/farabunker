"""`effective_context_window` — the number the engine will actually be
asked to allocate, and where it came from."""
from __future__ import annotations

from models.contracts.bindings import ResolvedModel, effective_context_window
from models.contracts.engines.ollama import DEFAULT_CONTEXT_WINDOW, OllamaEngine


def _resolved(**config):
    return ResolvedModel(OllamaEngine.name, "a-chat-model", "http://localhost:11434",
                         config=config)


class TestTheEffectiveContextWindow:
    def test_an_operator_set_value_wins(self):
        assert effective_context_window(_resolved(context_window=32768)) == (32768, "connection")

    def test_an_unset_value_falls_back_to_the_engines_own_bounded_default(self):
        assert effective_context_window(_resolved()) == (
            DEFAULT_CONTEXT_WINDOW, "engine-default")

    def test_an_engine_that_declares_no_default_says_unknown(self):
        other = ResolvedModel("some-other-engine", "m", "http://localhost:1", config={})
        assert effective_context_window(other) == (0, "unknown")

    def test_a_value_that_will_not_cast_degrades_rather_than_raising(self):
        assert effective_context_window(_resolved(context_window="wide")) == (0, "unknown")

    def test_a_zero_or_negative_stored_value_is_not_a_ceiling(self):
        assert effective_context_window(_resolved(context_window=0)) == (0, "unknown")
        assert effective_context_window(_resolved(context_window=-1)) == (0, "unknown")

    def test_a_none_config_is_not_a_crash(self):
        bare = ResolvedModel(OllamaEngine.name, "m", "http://localhost:1")
        assert effective_context_window(bare) == (DEFAULT_CONTEXT_WINDOW, "engine-default")
