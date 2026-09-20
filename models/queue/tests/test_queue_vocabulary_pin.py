"""Pins `models.contracts.queue`'s mirrored state vocabulary equal to
`models.queue.models`'s own -- the seam's copy is declared fresh (a pure
leaf must not import `models.queue.models`; see `models/contracts/
queue.py`'s docstring), so nothing else stops the two from drifting apart
except this test.

Exists because `agents/management/commands/agent_turn.py` once polled
`get_job` against a state literal ("done") the real vocabulary has never
had, and its own test fixtures agreed with the typo instead of with the
queue -- every test passed against the wrong literal. A caller outside
the `models/` column reaches the vocabulary through `models.contracts.
queue` (import-law rule 2: `agents/` may not import `models.queue.*`
directly); this test is what keeps that mirror honest.
"""
from __future__ import annotations

from models.contracts import queue as contracts_queue
from models.queue import models as queue_models


def test_the_five_state_constants_match_the_backend_exactly():
    assert contracts_queue.QUEUED == queue_models.QUEUED
    assert contracts_queue.RUNNING == queue_models.RUNNING
    assert contracts_queue.SUCCEEDED == queue_models.SUCCEEDED
    assert contracts_queue.FAILED == queue_models.FAILED
    assert contracts_queue.CANCELLED == queue_models.CANCELLED


def test_terminal_states_match_the_backend_exactly():
    assert contracts_queue.TERMINAL_STATES == frozenset(queue_models.TERMINAL_STATES)
