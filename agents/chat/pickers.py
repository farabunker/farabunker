"""The per-turn model picker's options.

The ONE module in `agents/chat` that imports `models.registry` at all,
and it imports exactly `bindings` -- import-law rule 2's single
sanctioned exception, pinned repo-wide by `foundation/ops/tests/
test_import_law.py::test_agents_reaches_models_registry_through_
bindings_and_nothing_else`, which walks every tracked file under
`agents/`.

`picker_options` is the SAME function `/rag/` and `/vision/` already
build their choosers from (`models/registry/bindings.py:233`), lifted
there precisely so a third page would not grow a third near-identical
copy of the loop. This module adds the chat capability/role pair and
the never-raises wrapper, and nothing else.
"""
from __future__ import annotations

import logging

from models.contracts.roles import CHAT_CONVERSE_ROLE
from models.registry.bindings import model_access_for, picker_options

logger = logging.getLogger(__name__)


def chat_picker_options(principal, selected: str = "", *,
                        wall: frozenset[int] = frozenset()) -> list[dict] | None:
    """The chooser's options, or `None` when there is nothing to pick
    from (no chat-capable connection and no environment override) --
    `picker_options`' own contract, passed through unchanged.

    `principal` is REQUIRED: a connection `principal` may not use
    (`models.registry.bindings.model_access_for`) is never in the
    offered options, exactly as `tools/rag/views.py` and
    `tools/vision/views.py`'s own pickers now filter.

    `wall` is the stream scope of the conversation whose page this picker
    is on, and empty everywhere else. THE RENDER HALF OF THE WALL: this
    function's own contract is that a connection the principal may not
    use "is never in the offered options", and unnarrowed it would offer
    models the walled turn then refuses -- breaking the render-vs-gate
    pair on the surface the wall is most visible on.

    NEVER RAISES. This is chrome on a page whose real subject is the
    conversation; a registry read that failed must cost the operator a
    picker, never the thread they came to read.
    """
    try:
        return picker_options("chat", CHAT_CONVERSE_ROLE, selected,
                              access=model_access_for(principal, wall=wall))
    except Exception:  # noqa: BLE001 -- log detail, then degrade to no picker
        logger.exception("chat: could not build the model picker's options")
        return None
