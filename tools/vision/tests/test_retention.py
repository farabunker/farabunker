"""The registered artifact purge: from a conversation's references to
the generation jobs behind them."""
from __future__ import annotations

import uuid

import pytest

from tools.vision import services
from tools.vision.models import GeneratedOutput, GenerationJob, JobInput
from tools.vision.retention import purge_artifacts
from tools.vision.tests._helpers import seed_sweep_posture

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _sweep():
    seed_sweep_posture()


# MODULE-LOCAL ROW BUILDERS, mirroring
# `tools/vision/tests/test_visibility.py`'s own `_generation`/`_output`/
# `_job_input` exactly. `tools/vision/tests/_helpers.py` defines NO
# generation builder -- it re-exports only the identity helpers
# (`grant`, `make_admin`, `make_entitlement`, `make_user`, `posture`,
# `seed_sweep_posture`, `sign_in`, `user_principal`) and this column's
# engine fakes -- and `identity/tests/_helpers.py::make_generation` is
# route-matrix scaffolding that resolves through `apps.get_model`. Two
# builders for two contexts, per that module's own recorded rule; this
# one is inside the column, so it imports the models.
def _generation(**overrides) -> GenerationJob:
    fields = dict(
        operation="txt2img",
        params={"prompt": "a lighthouse"},
        engine="stubengine",
        model_id="stub.safetensors",
        model_fingerprint="stubengine:stub.safetensors:None",
        status=GenerationJob.Status.DONE,
    )
    fields.update(overrides)
    return GenerationJob.objects.create(**fields)


def _output(**overrides) -> GeneratedOutput:
    job = overrides.pop("job", None) or _generation()
    fields = dict(job=job, index=0, path="/tmp/does-not-matter.png",
                  media_type="image/png")
    fields.update(overrides)
    return GeneratedOutput.objects.create(**fields)


class TestMappingReferencesToJobs:
    def test_an_output_reference_deletes_its_whole_job(self):
        job = _generation()
        output = _output(job=job)
        assert purge_artifacts([f"output:{output.pk}"], []) == 1
        assert not GenerationJob.objects.filter(pk=job.pk).exists()

    def test_two_outputs_of_one_job_are_one_delete(self):
        """Each reference is one FK hop from its job and several outputs
        share one job, so the mapping dedupes BY JOB."""
        job = _generation()
        first, second = _output(job=job, index=0), _output(job=job, index=1)
        assert purge_artifacts(
            [f"output:{first.pk}", f"output:{second.pk}"], []) == 1

    def test_an_input_reference_resolves_through_its_own_table(self):
        job = _generation()
        job_input = JobInput.objects.create(job=job, param_key="image",
                                            path="/dev/null",
                                            media_type="image/png")
        assert purge_artifacts([f"input:{job_input.pk}"], []) == 1
        assert not GenerationJob.objects.filter(pk=job.pk).exists()

    def test_a_bare_generation_id_is_accepted(self):
        job = _generation()
        assert purge_artifacts([], [str(job.pk)]) == 1
        assert not GenerationJob.objects.filter(pk=job.pk).exists()

    def test_a_reference_and_an_id_naming_one_job_are_one_delete(self):
        job = _generation()
        output = _output(job=job)
        assert purge_artifacts([f"output:{output.pk}"], [str(job.pk)]) == 1

    def test_a_uuid_that_matches_no_job_is_ignored(self):
        assert purge_artifacts([], [str(uuid.uuid4())]) == 0

    def test_a_reference_that_matches_no_row_is_ignored(self):
        assert purge_artifacts(["output:999999"], []) == 0

    def test_an_unparseable_reference_is_dropped_not_raised(self):
        assert purge_artifacts(["not-a-reference", ""], []) == 0

    def test_a_document_reference_is_skipped_silently(self):
        """`document:<id>` names a `Document` row, which the attachment
        seam already reaches -- `agents.retention._collect` never hands
        one to this column in the first place, and this pins that a
        caller who did anyway would not raise or count it."""
        assert purge_artifacts(["document:451"], []) == 0

    def test_it_is_idempotent(self):
        job = _generation()
        output = _output(job=job)
        purge_artifacts([f"output:{output.pk}"], [])
        assert purge_artifacts([f"output:{output.pk}"], []) == 0

    def test_it_goes_through_delete_job_so_the_files_and_the_sweep_run(self, monkeypatch):
        calls = []
        from tools.vision import retention as module
        monkeypatch.setattr(module.services, "delete_job",
                            lambda job: calls.append(job.pk))
        job = _generation()
        purge_artifacts([], [str(job.pk)])
        assert calls == [job.pk]


class TestEmptyInputCostsNothing:
    """CONTROLLER ADDITION: the artifact-purge slot is called on EVERY
    conversation purge on a vision box, including the common case -- a
    conversation with no images -- which hands it two empty lists. That
    must not touch a job, run no engine-side sweep, and cost no more
    than the mapping itself needs, which is zero queries for empty
    input."""

    def test_two_empty_lists_return_zero_and_touch_nothing(
        self, monkeypatch, django_assert_num_queries,
    ):
        from tools.vision import retention as module

        called = []
        monkeypatch.setattr(module.services, "delete_jobs",
                            lambda job_ids: called.append(list(job_ids)) or 0)
        with django_assert_num_queries(0):
            assert purge_artifacts([], []) == 0
        assert called == []


class TestDeleteJobs:
    """`tools.vision.services.delete_jobs` -- the one unscoped read of
    `GenerationJob.objects` `purge_artifacts` above hands its mapped job
    ids to, rather than querying the table itself (IA-1's closed set of
    two, `foundation/ops/tests/test_column_boundaries.py`). Lives beside
    `purge_artifacts`'s own tests, not `test_services.py`, which this
    module's own size keeps under the split threshold."""

    def test_it_deletes_exactly_the_named_jobs_and_leaves_another_alone(self):
        gone = _generation()
        stays = _generation()

        assert services.delete_jobs([gone.pk]) == 1

        assert not GenerationJob.objects.filter(pk=gone.pk).exists()
        assert GenerationJob.objects.filter(pk=stays.pk).exists()

    def test_an_empty_list_deletes_nothing(self):
        _generation()

        assert services.delete_jobs([]) == 0

        assert GenerationJob.objects.count() == 1

    def test_ids_of_jobs_already_gone_answer_zero_and_do_not_raise(self):
        job = _generation()
        job_id = job.pk
        job.delete()

        assert services.delete_jobs([job_id]) == 0
