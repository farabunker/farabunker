from __future__ import annotations

from django.apps import AppConfig


class JobsConfig(AppConfig):
    """The execution queue's storage (ADR 0013): the `InferenceJob`
    table and `JobSettings` singleton, plus the `backend.py` module
    `models/contracts/queue.py` dispatches to via
    `settings.INFERENCE_QUEUE_BACKEND`.

    A `models/` column app, not a tool -- the queue-side twin of
    `models.registry` one rung up the app list. Owns no roles/job kinds
    of its own (feature apps register those against `models.contracts.
    jobkinds`), so `ready()` stays empty, same as `models.registry`.
    """

    default_auto_field = "django.db.models.BigAutoField"
    name = "models.queue"
    label = "jobs"

    def ready(self) -> None:
        """No job kinds to register, no DB/heavy imports at startup -- see class docstring."""
