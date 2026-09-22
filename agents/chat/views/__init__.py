"""The chat surface's views, split by what they act on.

A package rather than one module because the views divide cleanly by
SUBJECT -- the conversation list, one thread, one turn, workstreams,
sharing, entitlement labelling, and defaults, each its own module
(CONSOLIDATION WAVE, audit B S3: this docstring used to name "the
seven views... three subjects," a count that was already stale by the
time it was written and has drifted further with every module and
export this package has gained since -- a STRUCTURAL claim replaces it
rather than a number this comment would have to keep re-deriving) --
and each carries a paragraph of contract (the 202 shape, the never-500
rule, the no-JS path) that reads better beside its own view than in
one large file. `urls.py` imports every name from HERE, so the split
is an implementation detail of this package and moving a view between
modules never touches a URL.

THE IMPORT DIRECTION INSIDE THIS PACKAGE IS ONE-WAY: `turns.py` ->
`thread.py` -> (nothing). `turn_create`'s 503 path re-renders the whole
thread, so it imports `thread_context`; `thread.py` imports NOTHING
from `turns.py`, which is why the poller's three constants live in
`agents/chat/service.py` (a module both import) rather than in
`turns.py` where the view that uses them is. A constant reached by an
import back up the chain is a cycle waiting for its second caller.
`conversations.py` likewise imports `service.py`, never `turns.py`.

UI-3b ADDS A THIRD SHARED MODULE ON THE SAME TERMS: `agents/chat/
sidebar.py`. The conversation sidebar renders on the index AND on every
thread page, so both `conversations.py` and `thread.py` need its
context builder -- and `thread.py` importing `conversations.py` would
be exactly the import back up the chain this rule exists to forbid. It
lives beside `service.py`, imports neither view module, and both may
import it.

ROUND 14 ADDS `all_conversations.py` ON THE SAME TERMS AGAIN: it reads
MANY conversations at once (a subject neither `conversations.py` nor
`thread.py` owns), imports `agents.chat.rendering` directly (a shared
LEAF, like `service.py`/`sidebar.py`) for its preview pane, and imports
neither `turns.py` nor `thread.py` -- so it adds no new edge to the
one-way chain above.
"""
from agents.chat.views.access import agent_entitlements
from agents.chat.views.agents import agent_edit, agent_list, agent_new
from agents.chat.views.agents_admin import agents_admin_list
from agents.chat.views.all_conversations import AllConversationsView
from agents.chat.views.assistant import assistant_ask, assistant_panel, assistant_reset
from agents.chat.views.conversations import (
    ChatIndexView, conversation_archive, conversation_delete, conversation_duplicate,
    conversation_pin, conversation_rename, conversation_start, conversation_unarchive,
    conversation_unpin,
)
from agents.chat.views.defaults import default_install
from agents.chat.views.settings import chat_settings
from agents.chat.views.shares import conversation_share
from agents.chat.views.thread import ConversationView
from agents.chat.views.tools import tool_entitlements
from agents.chat.views.turns import attachment_detach, turn_create, turn_status
from agents.chat.views.workstreams import (
    workstream_consolidate, workstream_edit, workstream_list, workstream_new,
    workstream_page, workstream_scope, workstream_settings, workstream_share,
)

__all__ = [
    "AllConversationsView", "ChatIndexView", "ConversationView", "agent_edit",
    "agent_entitlements", "agent_list", "agent_new", "agents_admin_list",
    "assistant_ask", "assistant_panel", "assistant_reset",
    "attachment_detach", "chat_settings", "conversation_archive", "conversation_delete",
    "conversation_duplicate", "conversation_pin", "conversation_rename",
    "conversation_share", "conversation_start", "conversation_unarchive",
    "conversation_unpin", "default_install", "tool_entitlements",
    "turn_create", "turn_status", "workstream_consolidate", "workstream_edit",
    "workstream_list", "workstream_new", "workstream_page", "workstream_scope",
    "workstream_settings", "workstream_share",
]
