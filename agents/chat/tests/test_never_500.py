"""A never-500 sweep over the whole `/chat/` surface.

THE ROUTE LIST IS DERIVED, NOT TYPED. `_CHAT_URL_NAMES` reads
`agents.chat.urls.urlpatterns` at import time rather than a hand-typed
list of the seven names that exist today, and `test_no_chat_route_
ever_500s` below walks that same `urlpatterns` object directly. A
route added to `agents/chat/urls.py` without a matching entry in
`_DRIVERS` fails with a `KeyError` naming it, immediately -- that
`KeyError` (not a silently-shorter loop) is what "swept automatically"
means here: an eighth route cannot ship untested by this file without
this file telling you so.

FOUR CONDITIONS PER ROUTE, wherever the route's own shape admits them:
a normal, readable state; no `Agent` rows at all; the queue raising
`QueueUnavailable`; and a malformed or absent target (a path segment
that does not parse, or one that parses but names nothing). Every
response's status must land in `_NEVER_500_STATUSES` and its body must
never contain the word "Traceback" -- the two obligations every other
task in this phase already wrote to, and this module's only job is to
keep them true after every later change to `/chat/`.

Queue doubles are the ones `agents/chat/tests/_helpers.py` already
declares (`_patch_queue`, imported directly here rather than through
its fixture wrappers, because each driver below needs to choose WHEN
in its own body the patch takes effect) -- except `turn_status`, which
reads `get_job` directly off `models.contracts.queue` into its own
module namespace (see `agents/chat/views/turns.py`'s own docstring),
so its queue-down double patches `agents.chat.views.turns.get_job`
directly rather than going through `_patch_queue` (which only ever
touches `agents.chat.service`).
"""
from __future__ import annotations

import functools
import itertools

import pytest
from django.urls import reverse

from agents.chat import urls as chat_urls
from agents.chat.tests._helpers import (   # noqa: F401 -- the import IS the registration
    _patch_queue, bind_chat_role, make_agent, make_conversation, make_thread, make_turn,
    make_user,
)
from agents.models import Turn
from agents.tests._helpers import _workstream
from models.contracts.queue import QueueUnavailable
from models.contracts.roles import CHAT_CONVERSE_ROLE

pytestmark = pytest.mark.django_db

XHR = {"HTTP_X_REQUESTED_WITH": "XMLHttpRequest"}

# `AskJobStatusView`'s never-500 vocabulary (200/404/503) plus the
# other honest non-500s this surface actually returns: 202 (queued),
# 302 (the no-JS redirects and the delete/install redirects), 400 (a
# caller error -- blank text, an unknown agent, an unknown default),
# 405 (a GET against a `require_POST` view), and 409 (M9's one-turn-
# at-a-time refusal, exercised elsewhere but named here so this set
# stays the single definition of "not a 500").
_NEVER_500_STATUSES = frozenset({200, 202, 302, 400, 404, 405, 409, 503})

_CHAT_URL_NAMES = frozenset(pattern.name for pattern in chat_urls.urlpatterns)

_slugs = itertools.count()


def _unique_slug(prefix: str) -> str:
    """A fresh CI-unique `Agent`/`Flow` slug per call, so two drivers
    in the same test never collide on `uniq_agent_slug_ci`."""
    return f"{prefix}-{next(_slugs)}"


def _assert_never_500(response):
    assert response.status_code in _NEVER_500_STATUSES, (
        response.status_code, response.content[:2000],
    )
    body = response.content.decode(errors="replace")
    assert "Traceback" not in body
    return response


# --- chat-index -------------------------------------------------------

def _index_normal(client, monkeypatch):
    make_thread(conversation=make_conversation(agent=make_agent(slug=_unique_slug("idx"))))
    return client.get(reverse("chat-index"))


def _index_no_agents(client, monkeypatch):
    return client.get(reverse("chat-index"))


def _index_queue_down(client, monkeypatch):
    _patch_queue(monkeypatch, raises=QueueUnavailable("down"))
    make_thread(conversation=make_conversation(agent=make_agent(slug=_unique_slug("idx-down"))))
    return client.get(reverse("chat-index"))


def _index_malformed_target(client, monkeypatch):
    # No path segment to malform on this route; its nearest equivalent
    # is a query string naming a connection pk that resolves to
    # nothing real.
    return client.get(reverse("chat-index") + "?connection=99999999")


# --- chat-all (round 14, Part 2: the conversations browser) ---------------

def _all_normal(client, monkeypatch):
    make_thread(conversation=make_conversation(agent=make_agent(slug=_unique_slug("all"))))
    return client.get(reverse("chat-all"))


def _all_no_agents(client, monkeypatch):
    return client.get(reverse("chat-all"))


def _all_queue_down(client, monkeypatch):
    # This route never touches the queue; proves that independence
    # rather than skipping the condition, the same shape `_workstreams_
    # queue_down` already takes for the identical reason.
    _patch_queue(monkeypatch, raises=QueueUnavailable("down"))
    make_thread(conversation=make_conversation(
        agent=make_agent(slug=_unique_slug("all-down"))))
    return client.get(reverse("chat-all"))


def _all_malformed_target(client, monkeypatch):
    # No path segment to malform (the route takes none); its own
    # malformed-input surface is `?selected=`, a bare id `visible_
    # conversations` is asked to resolve a second time -- a garbage
    # value must degrade to "no preview pane", never a 500 or a
    # database error from a non-UUID string reaching the query.
    #
    # ROUND 16 folds the tag filter's OWN malformed-input surface into
    # this SAME driver rather than adding a fifth one -- `_DRIVERS`'
    # own house rule (`test_the_driver_table_is_not_silently_empty`)
    # holds every route to a flat, equal driver count, and one request
    # naming garbage on BOTH surfaces at once (`selected=`, now
    # MULTI-VALUE `tag=` via `getlist`: a non-numeric value, an
    # out-of-range int, and a plain duplicate, together) proves the
    # two malformed-input guards degrade independently and neither
    # trips the other up, which two separate single-purpose requests
    # would not have shown any more of.
    return client.get(
        reverse("chat-all")
        + "?selected=not-a-real-uuid"
        + "&tag=not-a-number&tag=99999999999999999999&tag=1&tag=1")


# --- chat-start ---------------------------------------------------------

def _start_normal(client, monkeypatch):
    bind_chat_role(CHAT_CONVERSE_ROLE, name=_unique_slug("chat-role"))
    _patch_queue(monkeypatch)
    agent = make_agent(slug=_unique_slug("start-ok"))
    return client.post(reverse("chat-start"), {"agent": agent.slug, "text": "hi"})


def _start_no_agents(client, monkeypatch):
    # No `Agent` rows exist at all -- the slug names nothing.
    return client.post(reverse("chat-start"), {"agent": "nope", "text": "hi"})


def _start_queue_down(client, monkeypatch):
    bind_chat_role(CHAT_CONVERSE_ROLE, name=_unique_slug("chat-role"))
    _patch_queue(monkeypatch, raises=QueueUnavailable("down"))
    agent = make_agent(slug=_unique_slug("start-down"))
    return client.post(reverse("chat-start"), {"agent": agent.slug, "text": "hi"})


def _start_malformed_target(client, monkeypatch):
    # No `agent` field in the POST body at all.
    return client.post(reverse("chat-start"), {"text": "hi"})


# --- chat-default-install -------------------------------------------------

def _default_install_normal(client, monkeypatch):
    return client.post(
        reverse("chat-default-install"), {"kind": "agent", "slug": "general"},
    )


def _default_install_no_agents(client, monkeypatch):
    # This route's OWN precondition IS an empty box -- adopting a
    # shipped default is what the button does when nothing is
    # installed yet. Drives the flow catalogue instead of the agent
    # one so this scenario differs from `_default_install_normal`.
    return client.post(
        reverse("chat-default-install"), {"kind": "flow", "slug": "library-brief"},
    )


def _default_install_queue_down(client, monkeypatch):
    # `default_install` never touches the queue; this proves that
    # independence rather than skipping the condition.
    _patch_queue(monkeypatch, raises=QueueUnavailable("down"))
    return client.post(
        reverse("chat-default-install"), {"kind": "agent", "slug": "general"},
    )


def _default_install_malformed_target(client, monkeypatch):
    # UPDATED, ITEM 6 (Coherence Wave D): both refusals used to be a raw
    # 400; they are flash-and-redirect now (the caller is a plain
    # zero-JS `<form>`, `chat/_offers.html:21-24`/`chat/
    # _assistant_panel.html:158-161`), so both land on 302 -- still
    # never a 500, which is the property this sweep exists to pin.
    bad_kind = client.post(
        reverse("chat-default-install"), {"kind": "bogus", "slug": "general"},
    )
    assert bad_kind.status_code == 302
    unknown_slug = client.post(
        reverse("chat-default-install"), {"kind": "agent", "slug": "not-a-real-default"},
    )
    assert unknown_slug.status_code == 302
    # A GET is the checklist's own item 17: 405, never a page that
    # would let a prefetcher or a crawler install something.
    get_response = client.get(reverse("chat-default-install"))
    assert get_response.status_code == 405
    return get_response


# --- chat-conversation ------------------------------------------------

def _conversation_normal(client, monkeypatch):
    conversation = make_thread(
        conversation=make_conversation(agent=make_agent(slug=_unique_slug("conv-ok")))
    )
    return client.get(reverse("chat-conversation", args=[conversation.id]))


def _conversation_no_agents(client, monkeypatch):
    import uuid

    return client.get(reverse("chat-conversation", args=[uuid.uuid4()]))


def _conversation_queue_down(client, monkeypatch):
    # A GET never 503s (thread.py's own rule) -- this proves the queue
    # outage really doesn't reach it.
    _patch_queue(monkeypatch, raises=QueueUnavailable("down"))
    conversation = make_thread(
        conversation=make_conversation(agent=make_agent(slug=_unique_slug("conv-down")))
    )
    response = client.get(reverse("chat-conversation", args=[conversation.id]))
    assert response.status_code == 200
    return response


def _conversation_malformed_target(client, monkeypatch):
    import uuid

    not_a_uuid = client.get("/chat/c/not-a-uuid/")
    assert not_a_uuid.status_code == 404          # the route itself does not match
    missing = client.get(reverse("chat-conversation", args=[uuid.uuid4()]))
    assert missing.status_code == 404             # a real uuid naming nothing
    # `?pending=` is `thread.py::thread_context`'s own query-string read,
    # not a path segment -- a non-digit or negative value must render the
    # thread as a normal 200 with no poller target, never a NoReverseMatch
    # 500 (review fix wave, item 1).
    conversation = make_thread(
        conversation=make_conversation(agent=make_agent(slug=_unique_slug("conv-pending")))
    )
    bad_pending = client.get(
        reverse("chat-conversation", args=[conversation.id]), {"pending": "abc"},
    )
    assert bad_pending.status_code == 200
    negative_pending = client.get(
        reverse("chat-conversation", args=[conversation.id]), {"pending": "-1"},
    )
    assert negative_pending.status_code == 200
    return missing


# --- chat-turn ------------------------------------------------------------

def _turn_normal(client, monkeypatch):
    bind_chat_role(CHAT_CONVERSE_ROLE, name=_unique_slug("chat-role"))
    _patch_queue(monkeypatch)
    conversation = make_conversation(agent=make_agent(slug=_unique_slug("turn-ok")))
    return client.post(
        reverse("chat-turn", args=[conversation.id]), {"text": "hi"}, **XHR,
    )


def _turn_no_agents(client, monkeypatch):
    import uuid

    return client.post(
        reverse("chat-turn", args=[uuid.uuid4()]), {"text": "hi"}, **XHR,
    )


def _turn_queue_down(client, monkeypatch):
    bind_chat_role(CHAT_CONVERSE_ROLE, name=_unique_slug("chat-role"))
    _patch_queue(monkeypatch, raises=QueueUnavailable("down"))
    conversation = make_conversation(agent=make_agent(slug=_unique_slug("turn-down")))
    response = client.post(
        reverse("chat-turn", args=[conversation.id]), {"text": "hi"}, **XHR,
    )
    assert response.status_code == 503
    return response


def _turn_malformed_target(client, monkeypatch):
    conversation = make_conversation(agent=make_agent(slug=_unique_slug("turn-blank")))
    response = client.post(
        reverse("chat-turn", args=[conversation.id]), {"text": "   "}, **XHR,
    )
    assert response.status_code == 400            # blank text, nothing to queue
    return response


# --- chat-attachment-detach (round 13, message-bound attachments) --------

def _detach_document(conversation):
    """A chat-scoped `Document`, attached to `conversation`, owned by
    `OPEN_PRINCIPAL` -- the SAME principal every other driver in this
    file implicitly acts as (none of them signs in), which is what
    lets `may_detach_attachment`'s own uploader-only check succeed
    here without a real sign-in step."""
    from identity.access import owner_fields
    from identity.contracts.principals import OPEN_PRINCIPAL
    from agents.tests._helpers import make_document
    from tools.rag.models import Document, DocumentAttachment

    doc = make_document(scope=Document.Scope.CONVERSATION, **owner_fields(OPEN_PRINCIPAL))
    DocumentAttachment.objects.create(document=doc, conversation_id=conversation.id)
    return doc


def _detach_normal(client, monkeypatch):
    conversation = make_thread(
        conversation=make_conversation(agent=make_agent(slug=_unique_slug("detach-ok")))
    )
    doc = _detach_document(conversation)
    response = client.post(reverse("chat-attachment-detach", args=[conversation.id, doc.id]))
    assert response.status_code == 302
    return response


def _detach_no_agents(client, monkeypatch):
    import uuid

    return client.post(reverse("chat-attachment-detach", args=[uuid.uuid4(), 1]))


def _detach_queue_down(client, monkeypatch):
    # Detaching never touches the queue; proves it rather than skipping
    # the condition, the same reason `_delete_queue_down` does.
    _patch_queue(monkeypatch, raises=QueueUnavailable("down"))
    conversation = make_thread(
        conversation=make_conversation(agent=make_agent(slug=_unique_slug("detach-down")))
    )
    doc = _detach_document(conversation)
    response = client.post(reverse("chat-attachment-detach", args=[conversation.id, doc.id]))
    assert response.status_code == 302
    return response


def _detach_malformed_target(client, monkeypatch):
    conversation = make_thread(
        conversation=make_conversation(agent=make_agent(slug=_unique_slug("detach-404")))
    )
    get_response = client.get(
        reverse("chat-attachment-detach", args=[conversation.id, 999999]))
    assert get_response.status_code == 405         # require_POST
    missing = client.post(
        reverse("chat-attachment-detach", args=[conversation.id, 999999]))
    assert missing.status_code == 404
    return missing


# --- chat-conversation-delete ---------------------------------------------

def _delete_normal(client, monkeypatch):
    conversation = make_thread(
        conversation=make_conversation(agent=make_agent(slug=_unique_slug("del-ok")))
    )
    response = client.post(reverse("chat-conversation-delete", args=[conversation.id]))
    assert response.status_code == 302
    return response


def _delete_no_agents(client, monkeypatch):
    import uuid

    return client.post(reverse("chat-conversation-delete", args=[uuid.uuid4()]))


def _delete_queue_down(client, monkeypatch):
    # Deleting a thread never touches the queue; proves it rather than
    # skipping the condition.
    _patch_queue(monkeypatch, raises=QueueUnavailable("down"))
    conversation = make_thread(
        conversation=make_conversation(agent=make_agent(slug=_unique_slug("del-down")))
    )
    response = client.post(reverse("chat-conversation-delete", args=[conversation.id]))
    assert response.status_code == 302
    return response


def _delete_malformed_target(client, monkeypatch):
    import uuid

    get_response = client.get(reverse("chat-conversation-delete", args=[uuid.uuid4()]))
    assert get_response.status_code == 405        # require_POST
    missing = client.post(reverse("chat-conversation-delete", args=[uuid.uuid4()]))
    assert missing.status_code == 404
    return missing


# --- the sidebar menu's four actions (UI-3b) ------------------------------
#
# ONE SET OF BUILDERS, FOUR ROUTES. The four are the same SHAPE -- a
# POST at a conversation uuid, answering 302 when the row is there and
# 404 when it is not -- and they differ only in the body one of them
# reads. Sixteen near-identical driver functions would be sixteen places
# to forget the `_unique_slug`; these four are parametrised by route
# name, and `_menu_drivers` binds them per route with `functools.partial`
# so the table below still names four drivers for each of the four.

_MENU_BODIES = {"chat-conversation-rename": {"title": "Renamed"}}


def _menu_normal(name, client, monkeypatch):
    conversation = make_thread(
        conversation=make_conversation(agent=make_agent(slug=_unique_slug("menu")))
    )
    response = client.post(reverse(name, args=[conversation.id]),
                           _MENU_BODIES.get(name, {}))
    assert response.status_code == 302
    return response


def _menu_no_agents(name, client, monkeypatch):
    import uuid

    return client.post(reverse(name, args=[uuid.uuid4()]), _MENU_BODIES.get(name, {}))


def _menu_queue_down(name, client, monkeypatch):
    # None of the four touches the queue; proves it rather than skipping
    # the condition, exactly as the delete drivers above do.
    _patch_queue(monkeypatch, raises=QueueUnavailable("down"))
    return _menu_normal(name, client, monkeypatch)


def _menu_malformed_target(name, client, monkeypatch):
    import uuid

    get_response = client.get(reverse(name, args=[uuid.uuid4()]))
    assert get_response.status_code == 405        # require_POST
    missing = client.post(reverse(name, args=[uuid.uuid4()]),
                          _MENU_BODIES.get(name, {}))
    assert missing.status_code == 404
    return missing


def _menu_drivers(name):
    """The four conditions, bound to one route name."""
    return tuple(functools.partial(driver, name) for driver in (
        _menu_normal, _menu_no_agents, _menu_queue_down, _menu_malformed_target))


def _rename_refuses_a_blank_title(client, monkeypatch):
    """The one BODY-shaped malformed target this group has: a rename
    whose title is whitespace. Never a 500 and never a silent clear --
    the caller can already see the row, so there is nothing left for a
    404 to conceal.

    UPDATED, ITEM 6 (Coherence Wave D): the refusal used to be a raw
    400 (a bare plain-text page); it is flash-and-redirect now, the
    same house shape the SUCCESS path on this same view already
    redirects with -- so this lands on 302, still never a 500."""
    conversation = make_thread(
        conversation=make_conversation(agent=make_agent(slug=_unique_slug("blank")))
    )
    response = client.post(
        reverse("chat-conversation-rename", args=[conversation.id]),
        {"title": "   "})
    assert response.status_code == 302
    return response


# --- chat-turn-status -------------------------------------------------

def _turn_status_normal(client, monkeypatch):
    conversation = make_conversation(agent=make_agent(slug=_unique_slug("status-ok")))
    turn = make_turn(conversation=conversation, role=Turn.Role.ASSISTANT,
                      state=Turn.State.DONE, queue_job_id=None)
    response = client.get(reverse("chat-turn-status", args=[turn.pk]))
    assert response.status_code == 200
    return response


def _turn_status_no_agents(client, monkeypatch):
    return client.get(reverse("chat-turn-status", args=[999999999]))


def _turn_status_queue_down(client, monkeypatch):
    def _raise(job_id):
        raise QueueUnavailable("down")

    monkeypatch.setattr("agents.chat.views.turns.get_job", _raise)
    conversation = make_conversation(agent=make_agent(slug=_unique_slug("status-down")))
    turn = make_turn(conversation=conversation, role=Turn.Role.ASSISTANT,
                      state=Turn.State.QUEUED, queue_job_id=1)
    response = client.get(reverse("chat-turn-status", args=[turn.pk]))
    assert response.status_code == 503
    return response


def _turn_status_malformed_target(client, monkeypatch):
    not_an_int = client.get("/chat/turns/not-an-int/")
    assert not_an_int.status_code == 404          # the route itself does not match
    missing = client.get(reverse("chat-turn-status", args=[424242424]))
    assert missing.status_code == 404
    return missing


# --- chat-settings -------------------------------------------------------

def _chat_settings_normal(client, monkeypatch):
    response = client.get(reverse("chat-settings"))
    assert response.status_code == 200
    return response


def _chat_settings_no_agents(client, monkeypatch):
    # No `Agent` rows at all -- this page reads one settings row and
    # nothing else, so it has no agent-shaped precondition to lose.
    return client.get(reverse("chat-settings"))


def _chat_settings_queue_down(client, monkeypatch):
    # This view never touches the queue; proves that independence rather
    # than skipping the condition.
    _patch_queue(monkeypatch, raises=QueueUnavailable("down"))
    return client.get(reverse("chat-settings"))


def _chat_settings_malformed_target(client, monkeypatch):
    """THERE IS NO MALFORMED TARGET HERE, and this driver says so
    honestly rather than inventing one. The route addresses no row, and
    its one field is a checkbox: any value at all is a legal "on", and
    an absent field is a legal "off" -- so a junk body SAVES rather than
    refusing, which is the correct behaviour for a two-state control,
    not a gap. Both shapes are still driven, because a 500 is what this
    sweep is about and a garbage body is a plausible way to cause one.
    """
    junk_body = client.post(reverse("chat-settings"), {"time_aware": "\x00banana"})
    assert junk_body.status_code == 302
    junk_query = client.get(reverse("chat-settings") + "?nonsense=1")
    assert junk_query.status_code == 200
    return junk_query


# --- chat-tool-entitlements --------------------------------------------

def _tool_entitlements_normal(client, monkeypatch):
    response = client.get(reverse("chat-tool-entitlements"))
    assert response.status_code == 200
    return response


def _tool_entitlements_no_agents(client, monkeypatch):
    # No `Agent` rows at all -- the shell-path warning column is simply
    # empty; the page itself has no agent-shaped precondition.
    return client.get(reverse("chat-tool-entitlements"))


def _tool_entitlements_queue_down(client, monkeypatch):
    # This view never touches the queue; proves that independence rather
    # than skipping the condition.
    _patch_queue(monkeypatch, raises=QueueUnavailable("down"))
    return client.get(reverse("chat-tool-entitlements"))


def _tool_entitlements_malformed_target(client, monkeypatch):
    # F7 (Coherence Wave B): an unknown tool_key now flashes and
    # redirects (302), not a raw 400 -- the house convention every
    # other mutation on this surface already uses.
    #
    # PHASE 2 WIDENED THE SHAPE: the row's whole-set `entitlements`
    # field became the transfer panel's `op` plus `add`/`remove`, so
    # there are three more ways to hand this view nonsense -- a missing
    # `op`, a tampered one, and a non-numeric or unoffered id on either
    # side. Every one of them is a flash-and-redirect, and the last two
    # land back on the ROW that was being edited rather than the page
    # top, which is why their `Location` carries a fragment.
    bad_key = client.post(reverse("chat-tool-entitlements"),
                           {"tool_key": "not.registered", "op": "add", "add": []})
    assert bad_key.status_code == 302
    missing_fields = client.post(reverse("chat-tool-entitlements"), {})
    assert missing_fields.status_code == 302
    no_op = client.post(reverse("chat-tool-entitlements"),
                        {"tool_key": "rag.search", "add": ["1"]})
    assert no_op.status_code == 302
    bad_op = client.post(reverse("chat-tool-entitlements"),
                         {"tool_key": "rag.search", "op": "explode", "add": ["1"]})
    assert bad_op.status_code == 302
    bad_entitlement = client.post(reverse("chat-tool-entitlements"),
                                  {"tool_key": "rag.search", "op": "add", "add": ["abc"]})
    assert bad_entitlement.status_code == 302
    unoffered = client.post(reverse("chat-tool-entitlements"),
                            {"tool_key": "rag.search", "op": "remove",
                             "remove": ["424242"]})
    assert unoffered.status_code == 302
    return unoffered


# --- chat-agent-entitlements ---------------------------------------------

def _agent_entitlements_normal(client, monkeypatch):
    response = client.get(reverse("chat-agent-entitlements"))
    assert response.status_code == 200
    return response


def _agent_entitlements_no_agents(client, monkeypatch):
    # No `Agent`/`Flow` rows at all -- both sections simply render empty.
    return client.get(reverse("chat-agent-entitlements"))


def _agent_entitlements_queue_down(client, monkeypatch):
    # This view never touches the queue; proves that independence rather
    # than skipping the condition.
    _patch_queue(monkeypatch, raises=QueueUnavailable("down"))
    return client.get(reverse("chat-agent-entitlements"))


def _agent_entitlements_malformed_target(client, monkeypatch):
    # F7 (Coherence Wave B): all three malformed-target cases now flash
    # and redirect (302), not a raw 400 -- the house convention every
    # other mutation on this surface already uses.
    bad_kind = client.post(reverse("chat-agent-entitlements"),
                           {"kind": "not-a-kind", "pk": "1", "op": "add", "add": []})
    assert bad_kind.status_code == 302
    bad_pk = client.post(reverse("chat-agent-entitlements"),
                         {"kind": "agent", "pk": "not-an-int", "op": "add", "add": []})
    assert bad_pk.status_code == 302
    missing_pk = client.post(reverse("chat-agent-entitlements"),
                             {"kind": "agent", "pk": "424242424", "op": "add", "add": []})
    assert missing_pk.status_code == 302
    missing_fields = client.post(reverse("chat-agent-entitlements"), {})
    assert missing_fields.status_code == 302
    agent = make_agent(slug=_unique_slug("access-bad-entitlement"))
    # PHASE 2's three extra shapes, for the reason the tool page's own
    # driver states: a missing `op`, a tampered one, and an id outside
    # what this principal may label with.
    no_op = client.post(reverse("chat-agent-entitlements"),
                        {"kind": "agent", "pk": str(agent.pk), "add": ["1"]})
    assert no_op.status_code == 302
    bad_op = client.post(reverse("chat-agent-entitlements"),
                         {"kind": "agent", "pk": str(agent.pk), "op": "explode",
                          "add": ["1"]})
    assert bad_op.status_code == 302
    bad_entitlement = client.post(
        reverse("chat-agent-entitlements"),
        {"kind": "agent", "pk": str(agent.pk), "op": "add", "add": ["abc"]},
    )
    assert bad_entitlement.status_code == 302
    unoffered = client.post(
        reverse("chat-agent-entitlements"),
        {"kind": "flow", "pk": str(agent.pk), "op": "remove", "remove": ["424242"]},
    )
    assert unoffered.status_code == 302
    # THE LAST PROBE, like the tool driver beside it (fix round 2,
    # P2-N2): each response is asserted inline, so which one comes back
    # changed nothing -- but two drivers of one shape returning
    # different ones was accidental rather than meant.
    return unoffered


# --- chat-conversation-share --------------------------------------------

def _share_normal(client, monkeypatch):
    conversation = make_conversation(agent=make_agent(slug=_unique_slug("share-ok")))
    guest = make_user(username=_unique_slug("share-guest"))
    response = client.post(
        reverse("chat-conversation-share", args=[conversation.id]),
        {"action": "share", "subject": f"user:{guest.pk}", "level": "view"},
    )
    assert response.status_code == 302
    return response


def _share_no_agents(client, monkeypatch):
    import uuid

    # No `Conversation` rows at all -- the uuid names nothing.
    return client.post(
        reverse("chat-conversation-share", args=[uuid.uuid4()]),
        {"action": "share", "subject": "user:1", "level": "view"},
    )


def _share_queue_down(client, monkeypatch):
    # Sharing never touches the queue; proves that independence rather
    # than skipping the condition.
    _patch_queue(monkeypatch, raises=QueueUnavailable("down"))
    conversation = make_conversation(agent=make_agent(slug=_unique_slug("share-down")))
    guest = make_user(username=_unique_slug("share-down-guest"))
    response = client.post(
        reverse("chat-conversation-share", args=[conversation.id]),
        {"action": "share", "subject": f"user:{guest.pk}", "level": "view"},
    )
    assert response.status_code == 302
    return response


def _share_malformed_target(client, monkeypatch):
    import uuid

    get_response = client.get(reverse("chat-conversation-share", args=[uuid.uuid4()]))
    assert get_response.status_code == 405        # require_POST
    conversation = make_conversation(agent=make_agent(slug=_unique_slug("share-bad")))
    unknown_action = client.post(
        reverse("chat-conversation-share", args=[conversation.id]), {"action": "bogus"},
    )
    # M4 (Wave C review): flash-and-redirect now, not a raw 400 -- the
    # never-500 claim this module exists for is unchanged either way.
    assert unknown_action.status_code == 302
    bad_share_id = client.post(
        reverse("chat-conversation-share", args=[conversation.id]),
        {"action": "revoke", "share": "not-an-int"},
    )
    assert bad_share_id.status_code == 404
    # "²".isdigit() is True but int("²") raises ValueError -- `isdigit()`
    # admits a whole class of characters `int()` itself rejects (review
    # finding, 2026-08-31). Both parses in `shares.py`/`visibility.py`
    # used to reach `int(...)` on exactly this kind of string, a 500 on
    # a never-500 surface; both are `isdecimal()` now.
    non_decimal_share_id = client.post(
        reverse("chat-conversation-share", args=[conversation.id]),
        {"action": "revoke", "share": "²"},
    )
    assert non_decimal_share_id.status_code == 404
    non_decimal_subject = client.post(
        reverse("chat-conversation-share", args=[conversation.id]),
        {"action": "share", "subject": "user:²", "level": "view"},
    )
    assert non_decimal_subject.status_code == 404
    return non_decimal_subject


# --- chat-workstreams (WS-1, T13) -----------------------------------------

def _workstreams_normal(client, monkeypatch):
    _workstream(name=_unique_slug("ws-list"))
    return client.get(reverse("chat-workstreams"))


def _workstreams_no_agents(client, monkeypatch):
    # No `Agent` rows at all -- the list itself carries no agent-shaped
    # precondition (setup-screen round: the create form this comment used
    # to reference moved to `chat-workstream-new`, below, and no longer
    # lives here at all).
    return client.get(reverse("chat-workstreams"))


def _workstreams_queue_down(client, monkeypatch):
    # This route never touches the queue; proves that independence
    # rather than skipping the condition. A GET, not the POST this driver
    # used before the setup-screen round retired create from this route
    # entirely -- there is no create left here to exercise.
    _patch_queue(monkeypatch, raises=QueueUnavailable("down"))
    return client.get(reverse("chat-workstreams"))


def _workstreams_malformed_target(client, monkeypatch):
    # Setup-screen round: POST-create retired from this route
    # (`@require_GET` now, moved wholesale to `chat-workstream-new`) --
    # the nearest equivalent malformed input is a POST this route no
    # longer accepts at all, refused cleanly (405) rather than 500ing.
    response = client.post(reverse("chat-workstreams"), {"name": "irrelevant"})
    assert response.status_code == 405
    return response


# --- chat-workstream-new (owner feedback round 7: the setup screen) -------
# EVERY DRIVER IN THIS MODULE RUNS OPEN-POSTURE (no `sign_in` call
# anywhere in this file), the identical unauthenticated-is-`sees_all_
# content` shape `_workstreams_normal` above already relies on -- these
# four are no exception.

def _workstream_new_normal(client, monkeypatch):
    response = client.post(reverse("chat-workstream-new"), {"name": _unique_slug("ws-new")})
    assert response.status_code == 302
    return response


def _workstream_new_no_agents(client, monkeypatch):
    # No `Agent` rows at all -- this page carries no agent-shaped
    # precondition either; nothing here reads one.
    return client.get(reverse("chat-workstream-new"))


def _workstream_new_queue_down(client, monkeypatch):
    # Creating a stream never touches the queue; proves that independence
    # rather than skipping the condition.
    _patch_queue(monkeypatch, raises=QueueUnavailable("down"))
    response = client.post(reverse("chat-workstream-new"), {"name": _unique_slug("ws-new-down")})
    assert response.status_code == 302
    return response


def _workstream_new_malformed_target(client, monkeypatch):
    # No path segment on this route either; its nearest equivalent is the
    # SAME blank-name refusal the retired list-page create form used to
    # exercise -- `create_workstream`'s own refusal, a 400 re-render
    # rather than a 500, now on this page instead.
    response = client.post(reverse("chat-workstream-new"), {"name": "   "})
    assert response.status_code == 400
    return response


# --- chat-workstream (WS-1, T13) ------------------------------------------

def _workstream_normal(client, monkeypatch):
    stream = _workstream(name=_unique_slug("ws-page"))
    response = client.get(reverse("chat-workstream", args=[stream.pk]))
    assert response.status_code == 200
    return response


def _workstream_no_agents(client, monkeypatch):
    # No `Agent` rows at all -- the page's "New chat" `<select>` simply
    # renders empty.
    stream = _workstream(name=_unique_slug("ws-page-no-agents"))
    return client.get(reverse("chat-workstream", args=[stream.pk]))


def _workstream_queue_down(client, monkeypatch):
    # A GET never touches the queue; proves that independence rather
    # than skipping the condition.
    _patch_queue(monkeypatch, raises=QueueUnavailable("down"))
    stream = _workstream(name=_unique_slug("ws-page-down"))
    response = client.get(reverse("chat-workstream", args=[stream.pk]))
    assert response.status_code == 200
    return response


def _workstream_malformed_target(client, monkeypatch):
    not_an_int = client.get("/chat/w/not-an-int/")
    assert not_an_int.status_code == 404          # the route itself does not match
    missing = client.get(reverse("chat-workstream", args=[424242424]))
    assert missing.status_code == 404             # a real int naming nothing
    return missing


# --- chat-workstream-settings (settings-page split) -------------------------

def _workstream_settings_normal(client, monkeypatch):
    stream = _workstream(name=_unique_slug("ws-settings"))
    response = client.get(reverse("chat-workstream-settings", args=[stream.pk]))
    assert response.status_code == 200
    return response


def _workstream_settings_no_agents(client, monkeypatch):
    # No `Agent` rows at all -- irrelevant to this page (the "New chat"
    # `<select>` this condition targets lives on `chat-workstream`, not
    # here), kept anyway so this mount's own quartet stays the same
    # uniform four conditions every other mount's own quartet in this
    # sweep is built from.
    stream = _workstream(name=_unique_slug("ws-settings-no-agents"))
    return client.get(reverse("chat-workstream-settings", args=[stream.pk]))


def _workstream_settings_queue_down(client, monkeypatch):
    # A GET never touches the queue; proves that independence rather
    # than skipping the condition.
    _patch_queue(monkeypatch, raises=QueueUnavailable("down"))
    stream = _workstream(name=_unique_slug("ws-settings-down"))
    response = client.get(reverse("chat-workstream-settings", args=[stream.pk]))
    assert response.status_code == 200
    return response


def _workstream_settings_malformed_target(client, monkeypatch):
    not_an_int = client.get("/chat/w/not-an-int/settings/")
    assert not_an_int.status_code == 404          # the route itself does not match
    missing = client.get(reverse("chat-workstream-settings", args=[424242424]))
    assert missing.status_code == 404             # a real int naming nothing
    return missing


# --- chat-workstream-edit (WS-1, T13) -------------------------------------

def _workstream_edit_normal(client, monkeypatch):
    stream = _workstream(name=_unique_slug("ws-edit-ok"))
    response = client.post(reverse("chat-workstream-edit", args=[stream.pk]),
                           {"action": "rename", "name": "Renamed"})
    assert response.status_code == 302
    return response


def _workstream_edit_no_agents(client, monkeypatch):
    # No `Workstream` rows at all -- a real-shaped id names nothing.
    return client.post(reverse("chat-workstream-edit", args=[424242424]),
                       {"action": "rename", "name": "x"})


def _workstream_edit_queue_down(client, monkeypatch):
    # Editing a stream never touches the queue; proves that independence
    # rather than skipping the condition.
    _patch_queue(monkeypatch, raises=QueueUnavailable("down"))
    stream = _workstream(name=_unique_slug("ws-edit-down"))
    response = client.post(reverse("chat-workstream-edit", args=[stream.pk]),
                           {"action": "archive"})
    assert response.status_code == 302
    return response


def _workstream_edit_malformed_target(client, monkeypatch):
    get_response = client.get(reverse("chat-workstream-edit", args=[424242424]))
    assert get_response.status_code == 405        # require_POST
    stream = _workstream(name=_unique_slug("ws-edit-bad-action"))
    unknown_action = client.post(reverse("chat-workstream-edit", args=[stream.pk]),
                                 {"action": "explode"})
    assert unknown_action.status_code == 400
    missing = client.post(reverse("chat-workstream-edit", args=[424242424]),
                          {"action": "rename", "name": "x"})
    assert missing.status_code == 404
    return missing


# --- chat-workstream-scope (WS-1, T13) ------------------------------------

def _workstream_scope_normal(client, monkeypatch):
    stream = _workstream(name=_unique_slug("ws-scope-ok"))
    response = client.post(reverse("chat-workstream-scope", args=[stream.pk]),
                           {"entitlements": []})
    assert response.status_code == 302
    return response


def _workstream_scope_no_agents(client, monkeypatch):
    # No `Workstream` rows at all -- a real-shaped id names nothing.
    return client.post(reverse("chat-workstream-scope", args=[424242424]),
                       {"entitlements": []})


def _workstream_scope_queue_down(client, monkeypatch):
    # Setting the wall never touches the queue; proves that independence
    # rather than skipping the condition.
    _patch_queue(monkeypatch, raises=QueueUnavailable("down"))
    stream = _workstream(name=_unique_slug("ws-scope-down"))
    response = client.post(reverse("chat-workstream-scope", args=[stream.pk]),
                           {"entitlements": []})
    assert response.status_code == 302
    return response


def _workstream_scope_malformed_target(client, monkeypatch):
    get_response = client.get(reverse("chat-workstream-scope", args=[424242424]))
    assert get_response.status_code == 405        # require_POST
    missing = client.post(reverse("chat-workstream-scope", args=[424242424]),
                          {"entitlements": []})
    assert missing.status_code == 404
    return missing


# --- chat-workstream-share (WS-2, T17) -------------------------------------

def _workstream_share_normal(client, monkeypatch):
    # OPEN POSTURE (this module's default): `accounts_on()` is False, so
    # `identity.access.share_subjects` -- which this view's own
    # `_share_subject` resolves the posted id against -- offers nobody
    # (spec section 13: sharing is "inert but consistent" with no
    # accounts). A real, decimal user id is therefore refused as "pick an
    # account or a group", not honoured -- the honest 400 this route
    # answers on a box with nobody to share to, never a 500.
    stream = _workstream(name=_unique_slug("ws-share-ok"))
    guest = make_user(username=_unique_slug("ws-share-guest"))
    response = client.post(reverse("chat-workstream-share", args=[stream.pk]),
                           {"user": str(guest.pk)})
    assert response.status_code == 400
    return response


def _workstream_share_no_agents(client, monkeypatch):
    # No `Workstream` rows at all -- a real-shaped id names nothing.
    return client.post(reverse("chat-workstream-share", args=[424242424]),
                       {"user": "1"})


def _workstream_share_queue_down(client, monkeypatch):
    # Sharing never touches the queue; proves that independence rather
    # than skipping the condition -- same 400 as the normal driver above,
    # for the identical open-posture reason.
    _patch_queue(monkeypatch, raises=QueueUnavailable("down"))
    stream = _workstream(name=_unique_slug("ws-share-down"))
    guest = make_user(username=_unique_slug("ws-share-down-guest"))
    response = client.post(reverse("chat-workstream-share", args=[stream.pk]),
                           {"user": str(guest.pk)})
    assert response.status_code == 400
    return response


def _workstream_share_malformed_target(client, monkeypatch):
    get_response = client.get(reverse("chat-workstream-share", args=[424242424]))
    assert get_response.status_code == 405        # require_POST
    stream = _workstream(name=_unique_slug("ws-share-bad"))
    neither = client.post(reverse("chat-workstream-share", args=[stream.pk]), {})
    assert neither.status_code == 400
    # A non-numeric `share` on `action=revoke` is silently ignored (the
    # view's own revoke branch never checks `revoke_workstream_share`'s
    # `None`, unlike `chat-conversation-share`'s), so it redirects --
    # never a 500, which is the property this sweep exists to pin.
    bad_share_id = client.post(reverse("chat-workstream-share", args=[stream.pk]),
                               {"action": "revoke", "share": "not-an-int"})
    assert bad_share_id.status_code == 302
    # "²".isdigit() is True but int("²") raises ValueError -- the exact
    # landmine `chat-conversation-share`'s own driver above pins.
    non_decimal_share_id = client.post(reverse("chat-workstream-share", args=[stream.pk]),
                                       {"action": "revoke", "share": "²"})
    assert non_decimal_share_id.status_code == 302
    missing = client.post(reverse("chat-workstream-share", args=[424242424]),
                          {"user": "1"})
    assert missing.status_code == 404
    return missing


# --- chat-workstream-consolidate (WS-1, Task 18) --------------------------
#
# `_patch_queue` (this file's own, from `agents.chat.tests._helpers`)
# patches `agents.chat.service.enqueue` -- the WRONG module for this
# route, the same reason the module docstring names for `turn_status`:
# `workstream_consolidate` reads `enqueue` directly off `models.contracts.
# queue` into its OWN module namespace
# (`agents.chat.views.workstreams.enqueue`), so its own queue doubles
# patch that name directly.

def _consolidate_thread(stream):
    agent = make_agent(slug=_unique_slug("consolidate"))
    return make_thread(conversation=make_conversation(agent=agent, workstream=stream))


def _bind_consolidate_roles():
    """Both roles `workstream_consolidate`'s own enqueue-time preflight
    resolves for REAL (`models.contracts.bindings.resolve`, not this
    file's `_patch_queue`/monkeypatched `enqueue`) -- an unbound role
    there answers 503 before this driver's own double is ever reached."""
    from models.contracts.roles import RAG_EMBED_ROLE

    bind_chat_role(CHAT_CONVERSE_ROLE, name=_unique_slug("consolidate-chat"))
    bind_chat_role(RAG_EMBED_ROLE, name=_unique_slug("consolidate-embed"),
                   capability="embeddings", embed_dim=768)


def _workstream_consolidate_normal(client, monkeypatch):
    _bind_consolidate_roles()
    monkeypatch.setattr("agents.chat.views.workstreams.enqueue", lambda kind, payload: 1)
    stream = _workstream(name=_unique_slug("ws-consolidate-ok"))
    conversation = _consolidate_thread(stream)
    response = client.post(reverse("chat-workstream-consolidate", args=[stream.pk]),
                           {"conversation": str(conversation.pk)})
    assert response.status_code == 302
    return response


def _workstream_consolidate_no_agents(client, monkeypatch):
    # No `Workstream` rows at all -- a real-shaped id names nothing.
    return client.post(
        reverse("chat-workstream-consolidate", args=[424242424]),
        {"conversation": "00000000-0000-0000-0000-000000000000"})


def _workstream_consolidate_queue_down(client, monkeypatch):
    _bind_consolidate_roles()

    def _raise(kind, payload):
        raise QueueUnavailable("down")

    monkeypatch.setattr("agents.chat.views.workstreams.enqueue", _raise)
    stream = _workstream(name=_unique_slug("ws-consolidate-down"))
    conversation = _consolidate_thread(stream)
    response = client.post(reverse("chat-workstream-consolidate", args=[stream.pk]),
                           {"conversation": str(conversation.pk)})
    assert response.status_code == 503
    return response


def _workstream_consolidate_malformed_target(client, monkeypatch):
    get_response = client.get(reverse("chat-workstream-consolidate", args=[424242424]))
    assert get_response.status_code == 405        # require_POST
    stream = _workstream(name=_unique_slug("ws-consolidate-bad"))
    no_conversation = client.post(
        reverse("chat-workstream-consolidate", args=[stream.pk]), {})
    assert no_conversation.status_code == 404
    # A malformed UUID must not reach the ORM's own `ValidationError`
    # (the exact landmine `tools.vision.views`' own bulk-delete driver
    # guards against with the identical `uuid.UUID(raw)` parse) --
    # answered as the same row-addressed 404 a well-formed but unknown
    # id gets.
    not_a_uuid = client.post(
        reverse("chat-workstream-consolidate", args=[stream.pk]),
        {"conversation": "not-a-uuid"})
    assert not_a_uuid.status_code == 404
    missing = client.post(
        reverse("chat-workstream-consolidate", args=[424242424]),
        {"conversation": "00000000-0000-0000-0000-000000000000"})
    assert missing.status_code == 404
    return missing


# The one place every route name and its four drivers are named
# together -- ANY name present in `chat_urls.urlpatterns` but absent
# from this dict raises `KeyError` in the sweep below, immediately.
_DRIVERS: dict[str, tuple] = {
    "chat-index": (_index_normal, _index_no_agents, _index_queue_down, _index_malformed_target),
    "chat-all": (_all_normal, _all_no_agents, _all_queue_down, _all_malformed_target),
    "chat-start": (_start_normal, _start_no_agents, _start_queue_down, _start_malformed_target),
    "chat-default-install": (
        _default_install_normal, _default_install_no_agents,
        _default_install_queue_down, _default_install_malformed_target,
    ),
    "chat-conversation": (
        _conversation_normal, _conversation_no_agents,
        _conversation_queue_down, _conversation_malformed_target,
    ),
    "chat-turn": (_turn_normal, _turn_no_agents, _turn_queue_down, _turn_malformed_target),
    "chat-attachment-detach": (
        _detach_normal, _detach_no_agents, _detach_queue_down, _detach_malformed_target,
    ),
    "chat-conversation-delete": (
        _delete_normal, _delete_no_agents, _delete_queue_down, _delete_malformed_target,
    ),
    "chat-turn-status": (
        _turn_status_normal, _turn_status_no_agents,
        _turn_status_queue_down, _turn_status_malformed_target,
    ),
    "chat-settings": (
        _chat_settings_normal, _chat_settings_no_agents,
        _chat_settings_queue_down, _chat_settings_malformed_target,
    ),
    "chat-tool-entitlements": (
        _tool_entitlements_normal, _tool_entitlements_no_agents,
        _tool_entitlements_queue_down, _tool_entitlements_malformed_target,
    ),
    "chat-agent-entitlements": (
        _agent_entitlements_normal, _agent_entitlements_no_agents,
        _agent_entitlements_queue_down, _agent_entitlements_malformed_target,
    ),
    "chat-conversation-share": (
        _share_normal, _share_no_agents, _share_queue_down, _share_malformed_target,
    ),
    # The rename's fourth driver is the blank-title 400 rather than the
    # shared "unknown uuid" one: a body the view really reads, refused
    # for a reason no other route in this group has.
    "chat-conversation-rename": _menu_drivers("chat-conversation-rename")[:3] + (
        _rename_refuses_a_blank_title,),
    "chat-conversation-duplicate": _menu_drivers("chat-conversation-duplicate"),
    "chat-conversation-archive": _menu_drivers("chat-conversation-archive"),
    "chat-conversation-unarchive": _menu_drivers("chat-conversation-unarchive"),
    # ROUND 20: the SAME driver shape archive/unarchive already use.
    "chat-conversation-pin": _menu_drivers("chat-conversation-pin"),
    "chat-conversation-unpin": _menu_drivers("chat-conversation-unpin"),
    "chat-workstreams": (
        _workstreams_normal, _workstreams_no_agents,
        _workstreams_queue_down, _workstreams_malformed_target,
    ),
    "chat-workstream-new": (
        _workstream_new_normal, _workstream_new_no_agents,
        _workstream_new_queue_down, _workstream_new_malformed_target,
    ),
    "chat-workstream": (
        _workstream_normal, _workstream_no_agents,
        _workstream_queue_down, _workstream_malformed_target,
    ),
    "chat-workstream-settings": (
        _workstream_settings_normal, _workstream_settings_no_agents,
        _workstream_settings_queue_down, _workstream_settings_malformed_target,
    ),
    "chat-workstream-edit": (
        _workstream_edit_normal, _workstream_edit_no_agents,
        _workstream_edit_queue_down, _workstream_edit_malformed_target,
    ),
    "chat-workstream-scope": (
        _workstream_scope_normal, _workstream_scope_no_agents,
        _workstream_scope_queue_down, _workstream_scope_malformed_target,
    ),
    "chat-workstream-share": (
        _workstream_share_normal, _workstream_share_no_agents,
        _workstream_share_queue_down, _workstream_share_malformed_target,
    ),
    "chat-workstream-consolidate": (
        _workstream_consolidate_normal, _workstream_consolidate_no_agents,
        _workstream_consolidate_queue_down, _workstream_consolidate_malformed_target,
    ),
}


def test_no_chat_route_ever_500s(client, monkeypatch):
    """The whole sweep, one test, walking `urlpatterns` directly."""
    swept = set()
    for pattern in chat_urls.urlpatterns:
        name = pattern.name
        swept.add(name)
        drivers = _DRIVERS[name]           # KeyError => an unswept route
        for driver in drivers:
            _assert_never_500(driver(client, monkeypatch))
    assert swept == _CHAT_URL_NAMES        # anti-vacuous: the loop really ran


def test_the_driver_table_is_not_silently_empty():
    """Anti-vacuous pin: an emptied `_DRIVERS` (or a `urlpatterns` that
    stopped returning anything) would make the sweep above pass by
    testing nothing. `_CHAT_URL_NAMES` is read from the real module at
    import time, so this also proves it is non-empty."""
    assert _DRIVERS
    assert _CHAT_URL_NAMES
    assert set(_DRIVERS) == _CHAT_URL_NAMES
    for name, drivers in _DRIVERS.items():
        assert len(drivers) == 4, name
