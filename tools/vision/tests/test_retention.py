"""The two ways `tools/vision` answers a deletion: the registered
artifact-children resolver (a conversation's references and generation
ids to the jobs still behind them, fed to `agents.retention.
conversation_children`), and the `vision_job` kind's own registered
handler for one generation deleted on its own ticket."""
from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from django.utils.module_loading import import_string

from identity.contracts.cascades import retention_handlers
from identity.contracts.retention import KIND_VISION_JOB
from tools.vision import services, store
from tools.vision.models import GeneratedOutput, GenerationJob, JobInput
from tools.vision.retention import purge_job, resolve_artifact_jobs
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
    """`resolve_artifact_jobs` -- RESOLVES and deletes nothing. Its
    answer is what `agents.retention.conversation_children` turns into
    tickets of their own, one per generation, rather than a destroy
    (see `retention.py`'s `resolve_artifact_jobs` docstring for why)."""

    def test_an_output_reference_resolves_its_whole_job(self):
        job = _generation()
        output = _output(job=job)
        assert resolve_artifact_jobs([f"output:{output.pk}"], []) == [
            (str(job.pk), "", "")]

    def test_two_outputs_of_one_job_resolve_to_one_key(self):
        """Each reference is one FK hop from its job and several outputs
        share one job, so the mapping dedupes BY JOB."""
        job = _generation()
        first, second = _output(job=job, index=0), _output(job=job, index=1)
        assert resolve_artifact_jobs(
            [f"output:{first.pk}", f"output:{second.pk}"], []) == [
            (str(job.pk), "", "")]

    def test_an_input_reference_resolves_through_its_own_table(self):
        job = _generation()
        job_input = JobInput.objects.create(job=job, param_key="image",
                                            path="/dev/null",
                                            media_type="image/png")
        assert resolve_artifact_jobs([f"input:{job_input.pk}"], []) == [
            (str(job.pk), "", "")]

    def test_a_bare_generation_id_is_accepted(self):
        job = _generation()
        assert resolve_artifact_jobs([], [str(job.pk)]) == [(str(job.pk), "", "")]

    def test_a_failed_job_with_no_output_is_reached_only_through_its_generation_id(self):
        """The docstring's own claim: a job that reached the engine and
        FAILED mints no output at all, so the reference channel above
        finds nothing for it -- the generation-id channel is the only
        way this column's purge reaches it."""
        job = _generation(status=GenerationJob.Status.FAILED,
                          error="the engine could not be reached")
        assert not GeneratedOutput.objects.filter(job=job).exists()
        assert resolve_artifact_jobs([], [str(job.pk)]) == [(str(job.pk), "", "")]

    def test_a_reference_and_an_id_naming_one_job_are_one_key(self):
        job = _generation()
        output = _output(job=job)
        assert resolve_artifact_jobs(
            [f"output:{output.pk}"], [str(job.pk)]) == [(str(job.pk), "", "")]

    def test_the_jobs_own_owner_rides_along(self):
        """THE ASSERTION THAT WOULD FLIP if `existing_job_ids` stopped
        reading the owner columns: a job owned by a REAL principal, not
        the default blank ("written before accounts existed") every
        other fixture in this class leaves alone. `agents.retention.
        conversation_children` passes this straight through so `identity.
        retention.delete_content` can stamp the child ticket from THIS,
        not from the conversation's own owner."""
        job = _generation(owner_kind="user", owner_key="42")
        output = _output(job=job)
        assert resolve_artifact_jobs([f"output:{output.pk}"], []) == [
            (str(job.pk), "user", "42")]

    def test_a_uuid_that_matches_no_job_is_ignored(self):
        """The whole point of the existence check `resolve_artifact_jobs`
        runs both channels through: a stored generation id can outlive
        the job it names."""
        assert resolve_artifact_jobs([], [str(uuid.uuid4())]) == []

    def test_a_reference_that_matches_no_row_is_ignored(self):
        assert resolve_artifact_jobs(["output:999999"], []) == []

    def test_an_unparseable_reference_is_dropped_not_raised(self):
        assert resolve_artifact_jobs(["not-a-reference", ""], []) == []

    def test_a_document_reference_is_skipped_silently(self):
        """`document:<id>` names a `Document` row, which the attachment
        seam already reaches -- `agents.retention._collect` never hands
        one to this column in the first place, and this pins that a
        caller who did anyway would not raise or count it."""
        assert resolve_artifact_jobs(["document:451"], []) == []

    def test_it_is_idempotent(self):
        """RESOLVING twice is not DESTROYING twice: a second call answers
        the SAME list, not a second, smaller one."""
        job = _generation()
        output = _output(job=job)
        first = resolve_artifact_jobs([f"output:{output.pk}"], [])
        assert first == resolve_artifact_jobs([f"output:{output.pk}"], []) == [
            (str(job.pk), "", "")]

    def test_it_goes_through_delete_job_so_the_files_and_the_sweep_run(self, monkeypatch):
        """Moved onto `purge_job`: the resolver above destroys nothing,
        so the delete-job-and-sweep contract now belongs to the handler
        that actually removes a job."""
        calls = []
        from tools.vision import retention as module
        monkeypatch.setattr(module.services, "delete_job",
                            lambda job: calls.append(job.pk))
        job = _generation()
        purge_job(str(job.pk))
        assert calls == [job.pk]


class TestAFailedParseIsLoggedWithoutWhatItFailedToParse:
    """A deletion path must not write what it is destroying into a log:
    the log records that one reference, or one generation id, failed to
    parse and was ignored -- never the raw value itself."""

    def test_an_unparseable_reference_names_no_raw_value(self, caplog):
        raw = "not-a-reference-xyz789"
        with caplog.at_level("WARNING"):
            resolve_artifact_jobs([raw], [])
        assert not any(raw in record.getMessage() for record in caplog.records)

    def test_an_unusable_generation_id_names_no_raw_value(self, caplog):
        raw = "not-a-uuid-xyz789"
        with caplog.at_level("WARNING"):
            resolve_artifact_jobs([], [raw])
        assert not any(raw in record.getMessage() for record in caplog.records)


class TestEmptyInputCostsNothing:
    """The resolver is called on EVERY conversation delete on a vision
    box, including the common case -- a chat with no images -- which
    hands it two empty lists. `existing_job_ids` runs no query at all
    for an empty candidate set (its own docstring's claim), so this
    must cost zero queries, not one wasted existence check per delete
    with nothing to check."""

    def test_two_empty_lists_cost_zero_queries(self, django_assert_num_queries):
        with django_assert_num_queries(0):
            assert resolve_artifact_jobs([], []) == []


class TestDeleteJobs:
    """`tools.vision.services.delete_jobs` -- the second of the two
    unscoped reads of `GenerationJob.objects` a deletion needs (the
    first is `existing_job_ids`, which `resolve_artifact_jobs` calls),
    and the one that actually destroys, rather than querying the table
    itself (IA-1's closed set of two, `foundation/ops/tests/
    test_column_boundaries.py`). Lives beside `resolve_artifact_jobs`'s
    and `purge_job`'s own tests, not `test_services.py`, which this
    module's own size keeps under the split threshold."""

    def test_it_deletes_exactly_the_named_jobs_and_leaves_another_alone(self):
        gone = _generation()
        stays = _generation()

        assert services.delete_jobs([gone.pk]) == 1

        assert not GenerationJob.objects.filter(pk=gone.pk).exists()
        assert GenerationJob.objects.filter(pk=stays.pk).exists()

    def test_an_empty_list_deletes_nothing(self, django_assert_num_queries):
        """`pk__in=[]` short-circuits Django's own queryset before it
        ever reaches Postgres (the same zero-query property
        `TestEmptyInputCostsNothing` above pins for `resolve_artifact_
        jobs`), so this measures it rather than assuming it."""
        _generation()

        with django_assert_num_queries(0):
            assert services.delete_jobs([]) == 0

        assert GenerationJob.objects.count() == 1

    def test_ids_of_jobs_already_gone_answer_zero_and_do_not_raise(self):
        job = _generation()
        job_id = job.pk
        job.delete()

        assert services.delete_jobs([job_id]) == 0


class TestOnlyJobsThatExistComeBack:
    """A STORED GENERATION ID CAN NAME A JOB THAT IS ALREADY GONE --
    the turn keeps the id after the picture was deleted from the
    gallery. That was harmless while these ids fed a delete; it is not
    harmless now that each one becomes a ticket, because a ticket for a
    job nobody has is a "Generated image" row on the Deleted page with
    a date and a Restore button, naming a picture nobody can restore
    and nothing will ever destroy."""

    def test_a_generation_id_whose_job_is_gone_is_dropped(self):
        job = _generation()
        services.delete_jobs([job.pk])
        assert resolve_artifact_jobs([], [str(job.pk)]) == []

    def test_a_live_job_and_a_gone_one_answer_only_the_live_one(self):
        live = _generation()
        gone = _generation()
        services.delete_jobs([gone.pk])
        assert resolve_artifact_jobs([], [str(live.pk), str(gone.pk)]) == [
            (str(live.pk), "", "")]

    def test_the_check_is_one_query_for_the_whole_batch(
            self, django_assert_num_queries):
        """Two ids, one existence query -- never one per id. The
        reference channel pays its own FK hop on top; this pins the
        check itself."""
        first = _generation()
        second = _generation()
        with django_assert_num_queries(1):
            assert resolve_artifact_jobs(
                [], [str(first.pk), str(second.pk)]) == sorted(
                    [(str(first.pk), "", ""), (str(second.pk), "", "")])

    def test_a_reference_costs_its_hop_and_the_check(
            self, django_assert_num_queries):
        job = _generation()
        output = _output(job=job)
        with django_assert_num_queries(2):
            assert resolve_artifact_jobs([f"output:{output.pk}"], []) == [
                (str(job.pk), "", "")]

    def test_both_channels_pay_one_check_between_them(
            self, django_assert_num_queries):
        """One ref plus one unrelated id costs the reference's own FK
        hop PLUS ONE existence query over the union -- never a second
        existence query for the id channel. A regression that checked
        each channel separately would still pass every other pin here
        (each is single-channel) but would cost 3, not 2."""
        job = _generation()
        output = _output(job=job)
        other = _generation()
        with django_assert_num_queries(2):
            assert resolve_artifact_jobs(
                [f"output:{output.pk}"], [str(other.pk)]) == sorted(
                    [(str(job.pk), "", ""), (str(other.pk), "", "")])


class TestPurgeJob:
    """The `vision_job` kind's registered handler: ONE generation,
    addressed by its own ticket."""

    def test_it_removes_the_row_its_children_and_its_file(self, tmp_path, settings):
        settings.GENERATED_DIR = tmp_path
        job = _generation()
        path = store.store_output(job.pk, 0, "lighthouse.png", b"bytes on disk")
        GeneratedOutput.objects.create(job=job, index=0, path=path,
                                       media_type="image/png")
        assert purge_job(str(job.pk)) == 1
        assert not GenerationJob.objects.filter(pk=job.pk).exists()
        assert not GeneratedOutput.objects.filter(job_id=job.pk).exists()
        assert not Path(path).exists()

    def test_it_is_idempotent(self):
        job = _generation()
        purge_job(str(job.pk))
        assert purge_job(str(job.pk)) == 0

    def test_a_job_that_does_not_exist_is_zero_not_an_error(self):
        assert purge_job(str(uuid.uuid4())) == 0

    def test_a_non_uuid_key_removes_nothing(self):
        assert purge_job("not-a-uuid") == 0


class TestTheKindIsRegistered:
    def test_the_registered_handler_resolves_and_removes_the_job(self):
        """END TO END THROUGH THE REGISTRY, not through the function
        name: this is what `identity.retention.purge_ticket` will
        actually run for a `vision_job` ticket."""
        specs = retention_handlers(KIND_VISION_JOB)
        assert [spec.label for spec in specs] == ["Generated image"]
        job = _generation()
        assert import_string(specs[0].handler)(str(job.pk)) == 1
        assert not GenerationJob.objects.filter(pk=job.pk).exists()
