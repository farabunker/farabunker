"""URL routes for the execution queue: the Queue page and the "Job
execution" settings page, both mounted at /queue/ (see config/urls.py).

F1 (Coherence Wave C): `/queue/settings/` is now the SETTINGS PAGE (a
registered `SETTINGS_GROUPS` entry), and the write endpoint its two forms
post to sits one segment below it at `/queue/settings/update/`.

THE ENDPOINT'S URL NAME MOVED WITH ITS PATH -- `jobs-queue-settings`
became `jobs-settings-update` (I1, Wave C review). The usual reason not
to rename a working route is that nothing reads the name, so the churn
buys nothing; that reason lapsed here twice over. Its path had already
changed in the same wave, so no bookmark it could break was still
working, and `docs/EXTENDING.md`'s backend recipe now names this endpoint
and `tools.rag.views.library_settings_update` SIDE BY SIDE as the pair a
new settings page copies -- which makes a naming asymmetry between the
two the first thing a future author reads, rather than a local oddity.
`<page-name>-update` is the shape both now carry.
"""
from django.urls import path

from models.queue.views import (
    JobSettingsView, QueueView, queue_job_cancel, queue_settings_update,
)

urlpatterns = [
    path("", QueueView.as_view(), name="jobs-queue"),
    path("settings/", JobSettingsView.as_view(), name="jobs-settings"),
    path("settings/update/", queue_settings_update, name="jobs-settings-update"),
    path("<int:job_id>/cancel/", queue_job_cancel, name="jobs-queue-cancel"),
]
