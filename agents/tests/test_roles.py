"""`chat.converse` is a registered role, and registering it cost nothing.

ROADMAP:236-241 predicted this exact outcome -- "the console, the
Getting-models checklist, and per-role binding then serve it with zero
framework changes". These tests are what turns that prediction into a
pinned fact: the role is in `all_roles()`, it declares the `chat`
capability every chat-capable connection is filtered by, and it has no
`rematerialize` (a conversation role does not embed, so there is
nothing to re-encode and inventing a callback would be a lie).
"""
from __future__ import annotations

from models.contracts.roles import CHAT_CONVERSE_ROLE, all_roles, get_role


def test_chat_converse_is_registered_with_the_chat_capability():
    spec = get_role(CHAT_CONVERSE_ROLE)
    assert spec is not None, [r.key for r in all_roles()]
    assert spec.capability == "chat"


def test_chat_converse_declares_no_rematerialize():
    """A chat role does not embed. `models.registry.drift` resolves
    `rematerialize` for every role that declares one; a role that
    declared a callback it does not need would make the drift guard
    offer an action that does nothing."""
    assert get_role(CHAT_CONVERSE_ROLE).rematerialize is None


def test_the_role_key_constant_is_the_shared_one():
    """Not a bare literal in `agents/`. `models/contracts/roles.py` is
    the one place both the `models/` column and every feature column can
    reach a single definition -- that file's own comment at :22-29 says
    so for the two RAG roles, and this is the same argument."""
    assert CHAT_CONVERSE_ROLE == "chat.converse"
