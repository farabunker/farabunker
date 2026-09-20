"""The chat surface's Django app (spec section 7 preamble, deviation P3-D1).

Its OWN app with its OWN label, unlike `agents/runtime/` which P2's
deviation D1 folded into the `agents` app. The reasons differ and both
are mechanical: `agents/runtime` holds MODELS that must land in
`agents/migrations/`, while this package holds none. What it needs
instead is TEMPLATE DISCOVERY -- `TEMPLATES[0]["APP_DIRS"] = True`
(`config/settings.py`) finds `agents/chat/templates/chat/` only for an
installed app -- and its own `tests/` package with its own helpers.

NO `ready()`. There is nothing to register: the `chat.converse` role,
the `agent.turn` job kind, and every tool spec belong to
`agents/apps.py::AgentsConfig`, which already runs. An empty `ready()`
here would be a hook somebody later fills in with a registration that
belongs one level up.

NO MODELS, on purpose. `label = "chat"` is declared explicitly anyway,
per the contract spec section 3.5 pins: every AppConfig in this project
sets `label`, so a package move can never rename a table. A future chat
model gets a table named `chat_*` and a migration of its own, and
nothing about that has to be decided now.
"""
from __future__ import annotations

from django.apps import AppConfig


class ChatConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "agents.chat"
    label = "chat"
