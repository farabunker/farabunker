"""URL routes for the vision module, mounted at /vision/ (config/urls.py)."""
from django.urls import path

from tools.vision.maintenance import engine_file_thumbnail, engine_files, engine_files_delete
from tools.vision.views import (
    CreatePageView, gallery, generate, input_file, job_delete, job_status,
    jobs_delete_selected, jobs_strip, output_file, queue_job_status, vision_operations,
)

urlpatterns = [
    path("", CreatePageView.as_view(), name="vision-create"),
    # A RENDERING ALIAS, kept for links issued before `?operation=` became
    # canonical (ADR 0012 D-EDIT-13). Same view, same page, no redirect and
    # no extra round trip -- `CreatePageView` reads this path segment first
    # and `?operation=` second. Nothing the page generates points here any
    # more; `generate` still reads the same key out of its POST, so an
    # operation is identified the same way whether the operator navigated
    # or submitted.
    path("op/<slug:operation_key>/", CreatePageView.as_view(), name="vision-create-operation"),
    path("gallery/", gallery, name="vision-gallery"),
    # Schema discovery (ADR 0012): the catalog `CreatePageView` builds its
    # own form from, as JSON for a caller that isn't a browser.
    path("operations/", vision_operations, name="vision-operations"),
    path("generate/", generate, name="vision-generate"),
    path("jobs/<uuid:job_id>/", job_status, name="vision-job-status"),
    path("jobs/<uuid:job_id>/delete/", job_delete, name="vision-job-delete"),
    # The create page's Recent region, as a standalone fragment (T1,
    # 2026-09-16): a literal segment, never `<uuid:...>`, so it can never
    # collide with the row-addressed route above -- the same reasoning
    # `jobs/delete-selected/` already documents for its own literal
    # segment.
    path("jobs/strip/", jobs_strip, name="vision-jobs-strip"),
    # The gallery's select-mode bulk delete -- a literal segment, not a
    # `<uuid:job_id>` capture, so it never collides with the row-addressed
    # route above (no UUID reads as "delete-selected").
    path("jobs/delete-selected/", jobs_delete_selected, name="vision-jobs-delete-selected"),
    path("outputs/<int:output_id>/file/", output_file, name="vision-output-file"),
    path("inputs/<int:input_id>/file/", input_file, name="vision-input-file"),
    # The queued placeholder's poll target: a queue job id, not a
    # generation UUID -- the generation does not exist yet when the page
    # starts polling. It answers with the real job card the moment it does.
    path("queue/<int:queue_job_id>/", queue_job_status, name="vision-queue-status"),
    # Engine files (2026-09-02): admin-only visibility into the engine's
    # OWN output/input folders, which farabunker never manages
    # (`tools/vision/maintenance.py`'s own module docstring). Literal
    # segments throughout, never `<uuid:...>`, so none of the three
    # collides with any route above.
    path("engine-files/", engine_files, name="vision-engine-files"),
    path(
        "engine-files/thumb/<str:kind>/<str:name>/",
        engine_file_thumbnail, name="vision-engine-file-thumbnail",
    ),
    path("engine-files/delete/", engine_files_delete, name="vision-engine-files-delete"),
]
