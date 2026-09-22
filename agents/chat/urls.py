"""URL routes for the chat surface, mounted UNGATED at /chat/
(config/urls.py).

Ungated for the reason spec section 8.1 gives: `docs/DEV.md:235-246`
fixes the two supported suite states at 'vision,media' and 'vision',
and a third feature token would either break that contract or leave
this whole surface untested in one of them.
"""
from django.urls import path

from agents.chat.views import (
    AllConversationsView, ChatIndexView, ConversationView, agent_edit,
    agent_entitlements, agent_list, agent_new,
    attachment_detach, chat_settings, conversation_archive, conversation_delete,
    conversation_duplicate,
    conversation_pin, conversation_rename, conversation_share, conversation_start,
    conversation_unarchive, conversation_unpin, default_install, tool_entitlements,
    turn_create, turn_status, workstream_consolidate, workstream_edit, workstream_list,
    workstream_new, workstream_page, workstream_scope, workstream_settings, workstream_share,
)

urlpatterns = [
    path("", ChatIndexView.as_view(), name="chat-index"),
    path("start/", conversation_start, name="chat-start"),
    path("defaults/install/", default_install, name="chat-default-install"),
    # ROUND 14, Part 2: the conversations browser. `all/` BEFORE the
    # `c/<uuid:...>/` block below reads clearly even though Django's
    # `uuid` converter would never match the literal "all" anyway (the
    # same non-issue `w/new/`'s own comment already names for `w/<int:
    # pk>/`).
    path("all/", AllConversationsView.as_view(), name="chat-all"),
    path("c/<uuid:conversation_id>/", ConversationView.as_view(), name="chat-conversation"),
    path("c/<uuid:conversation_id>/turn/", turn_create, name="chat-turn"),
    # ROUND 13 (message-bound attachments), requirement E's post-send
    # remove: row-addressed, POST-only, matching the shape every other
    # per-conversation action on this list already takes.
    path("c/<uuid:conversation_id>/attachments/<int:doc_id>/detach/", attachment_detach,
         name="chat-attachment-detach"),
    path("c/<uuid:conversation_id>/delete/", conversation_delete, name="chat-conversation-delete"),
    path("c/<uuid:conversation_id>/share/", conversation_share, name="chat-conversation-share"),
    # UI-3b: the sidebar's per-conversation menu. Four POST-only,
    # row-addressed routes beside the delete that was already here, all
    # class "O" and all gated by the SAME predicate delete is
    # (`agents.visibility.may_manage_conversation`). Rename and archive
    # are two routes rather than one with a direction in the body, for
    # the reason `_set_archived` records.
    path("c/<uuid:conversation_id>/rename/", conversation_rename,
         name="chat-conversation-rename"),
    path("c/<uuid:conversation_id>/duplicate/", conversation_duplicate,
         name="chat-conversation-duplicate"),
    path("c/<uuid:conversation_id>/archive/", conversation_archive,
         name="chat-conversation-archive"),
    path("c/<uuid:conversation_id>/unarchive/", conversation_unarchive,
         name="chat-conversation-unarchive"),
    # ROUND 20: the SAME shape archive/unarchive already take, two
    # routes rather than one with a direction in the body, for the
    # identical reason `_set_archived`'s own docstring records.
    path("c/<uuid:conversation_id>/pin/", conversation_pin,
         name="chat-conversation-pin"),
    path("c/<uuid:conversation_id>/unpin/", conversation_unpin,
         name="chat-conversation-unpin"),
    path("turns/<int:turn_id>/", turn_status, name="chat-turn-status"),
    # ROUND 21: the chat column's own settings-area page (the "Chat"
    # entry in `foundation/settings_area.py`'s Setup group). Class S,
    # like the two entitlement pages below it -- `agents/chat/views/
    # settings.py` has the reasoning. GET and POST on ONE route, the
    # shape `identity-settings` already uses for its own singleton.
    path("settings/", chat_settings, name="chat-settings"),
    path("tools/", tool_entitlements, name="chat-tool-entitlements"),
    path("access/", agent_entitlements, name="chat-agent-entitlements"),
    # THE AGENT PAGES (chat cluster, feature B). `agents/new/` BEFORE
    # `agents/<int:pk>/` reads clearly even though Django's `int`
    # converter would never match the literal "new" anyway -- the same
    # non-issue `w/new/`'s own comment already names.
    path("agents/", agent_list, name="chat-agents"),
    path("agents/new/", agent_new, name="chat-agent-new"),
    path("agents/<int:pk>/", agent_edit, name="chat-agent-edit"),
    # WS-1: the stream list, the stream page, and the one edit route with
    # an `action` field (author decision 11). `chat-workstream-share`
    # (WS-2, Task 17) shares and revokes on one URL, the same shape
    # `chat-conversation-share` already uses. `chat-workstream-consolidate`
    # is Task 18; `rag-workstream-pin` is Task 15, mounted under /rag/.
    path("w/", workstream_list, name="chat-workstreams"),
    # Owner feedback round 7: "should we have it on its own screen rather
    # than chilling at the top" -- creation moved off the list page's own
    # inline name+Create row entirely, onto its own setup screen. `w/new/`
    # BEFORE the `<int:pk>/` pattern below reads clearly even though
    # Django's `int` converter would never match the literal "new" anyway.
    path("w/new/", workstream_new, name="chat-workstream-new"),
    path("w/<int:pk>/", workstream_page, name="chat-workstream"),
    # Owner feedback (chat layout fix): "keep the settings that are not
    # often updated on a separate settings page" -- Manage/Instructions/
    # Scope/Upload default/Tags/Shared with, split off `chat-workstream`
    # into their own page. Owner-only under the plain house 404 rule
    # (`identity/routes.py`'s own entry has the reasoning); `chat-
    # workstream` itself is untouched and stays what a recipient sees.
    path("w/<int:pk>/settings/", workstream_settings, name="chat-workstream-settings"),
    path("w/<int:pk>/edit/", workstream_edit, name="chat-workstream-edit"),
    path("w/<int:pk>/scope/", workstream_scope, name="chat-workstream-scope"),
    path("w/<int:pk>/share/", workstream_share, name="chat-workstream-share"),
    path("w/<int:pk>/consolidate/", workstream_consolidate, name="chat-workstream-consolidate"),
]
