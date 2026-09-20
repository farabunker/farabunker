"""Unit tests for the Engine files admin page
(`tools/vision/maintenance.py`) -- listing, accounted/orphan tagging,
thumbnails, and bulk delete over the image engine's OWN output/input
folders (Engine files feature, 2026-09-02).
"""
from __future__ import annotations

import pytest
from django.http import Http404
from django.test import Client
from django.urls import reverse

from identity.contracts.actions import ENGINE_FILE_DELETED
from identity.contracts.postures import POSTURE_PERSONAL
from identity.models import AuditEvent
from tools.vision import store
from tools.vision.models import GenerationJob, JobInput
from tools.vision.tests._helpers import clear_bindings, make_admin, make_user, posture, sign_in


@pytest.fixture
def client():
    return Client()


@pytest.fixture(autouse=True)
def _clear_bindings(db):
    clear_bindings()


def _dirs(tmp_path, monkeypatch):
    """Point `store.ENGINE_OUTPUT_DIR`/`ENGINE_INPUT_DIR` at two real,
    empty tmp directories for the duration of one test -- the "settings/
    env monkeypatch" the brief calls for, since these two are plain
    module attributes (`config/` is a no-touch zone for this change),
    not Django settings."""
    output = tmp_path / "output"
    input_dir = tmp_path / "input"
    output.mkdir()
    input_dir.mkdir()
    monkeypatch.setattr(store, "ENGINE_OUTPUT_DIR", output)
    monkeypatch.setattr(store, "ENGINE_INPUT_DIR", input_dir)
    return output, input_dir


def _job(**overrides):
    fields = dict(
        operation="txt2img",
        params={"prompt": "a lighthouse"},
        engine="stubengine", model_id="stub.safetensors",
        model_fingerprint="stubengine:stub.safetensors:None",
        status=GenerationJob.Status.DONE,
    )
    fields.update(overrides)
    return GenerationJob.objects.create(**fields)


@pytest.mark.django_db
class TestStanding:
    """Route class S (`identity/routes.py`): admin only, enforced at the
    middleware."""

    def test_an_admin_sees_the_page(self, client, tmp_path, monkeypatch):
        _dirs(tmp_path, monkeypatch)
        with posture(POSTURE_PERSONAL):
            sign_in(client, make_admin())
            response = client.get(reverse("vision-engine-files"))
        assert response.status_code == 200

    def test_a_member_is_refused(self, client, tmp_path, monkeypatch):
        _dirs(tmp_path, monkeypatch)
        with posture(POSTURE_PERSONAL):
            sign_in(client, make_user())
            response = client.get(reverse("vision-engine-files"))
        assert response.status_code == 403


@pytest.mark.django_db
class TestMissingDirectory:
    def test_an_unmounted_directory_is_an_honest_empty_state_not_a_500(self, client):
        """Neither `_dirs` nor any other override runs here -- the module
        defaults (`/engine/output`, `/engine/input`) name paths that do
        not exist on a test box, exactly like a preview stack with no
        bind mount configured."""
        with posture(POSTURE_PERSONAL):
            sign_in(client, make_admin())
            response = client.get(reverse("vision-engine-files"))

        assert response.status_code == 200
        assert response.context["outputs"] == []
        assert response.context["inputs"] == []
        assert response.context["outputs_reachable"] is False
        assert response.context["inputs_reachable"] is False
        assert "not reachable" in response.content.decode()


@pytest.mark.django_db
class TestAccounting:
    def test_a_file_prefixed_with_a_known_job_uuid_is_accounted_the_rest_orphaned(
        self, client, tmp_path, monkeypatch,
    ):
        output_dir, _input_dir = _dirs(tmp_path, monkeypatch)
        job = _job()
        (output_dir / f"{job.id}_00001_.png").write_bytes(b"x")
        (output_dir / "some-random-name.png").write_bytes(b"x")

        with posture(POSTURE_PERSONAL):
            sign_in(client, make_admin())
            response = client.get(reverse("vision-engine-files"))

        by_name = {f.name: f.accounted for f in response.context["outputs"]}
        assert by_name[f"{job.id}_00001_.png"] is True
        assert by_name["some-random-name.png"] is False

    def test_a_failed_jobs_uuid_still_accounts_for_its_output(self, client, tmp_path, monkeypatch):
        """Brief section 2: ANY status, not only finished jobs."""
        output_dir, _input_dir = _dirs(tmp_path, monkeypatch)
        job = _job(status=GenerationJob.Status.FAILED)
        (output_dir / f"{job.id}_partial.png").write_bytes(b"x")

        with posture(POSTURE_PERSONAL):
            sign_in(client, make_admin())
            response = client.get(reverse("vision-engine-files"))

        assert response.context["outputs"][0].accounted is True

    def test_a_staged_uploads_uuid_accounts_for_an_input_file(self, client, tmp_path, monkeypatch):
        _output_dir, input_dir = _dirs(tmp_path, monkeypatch)
        staged = JobInput.objects.create(
            job=None, param_key="init_image",
            path=f"{tmp_path}/uploads/11111111-1111-1111-1111-111111111111/beach.png",
        )
        (input_dir / "11111111-1111-1111-1111-111111111111_upload.png").write_bytes(b"x")
        (input_dir / "unrelated.png").write_bytes(b"x")

        with posture(POSTURE_PERSONAL):
            sign_in(client, make_admin())
            response = client.get(reverse("vision-engine-files"))

        by_name = {f.name: f.accounted for f in response.context["inputs"]}
        assert by_name["11111111-1111-1111-1111-111111111111_upload.png"] is True
        assert by_name["unrelated.png"] is False
        assert staged.job_id is None  # sanity: this is genuinely a staged row

    def test_a_subdirectory_is_never_listed(self, client, tmp_path, monkeypatch):
        output_dir, _input_dir = _dirs(tmp_path, monkeypatch)
        (output_dir / "a-subfolder").mkdir()
        (output_dir / "a-subfolder" / "inside.png").write_bytes(b"x")

        with posture(POSTURE_PERSONAL):
            sign_in(client, make_admin())
            response = client.get(reverse("vision-engine-files"))

        assert response.context["outputs"] == []


@pytest.mark.django_db
class TestThumbnails:
    def test_a_thumbnail_renders_only_under_sees_all_content(self, client, tmp_path, monkeypatch):
        output_dir, _input_dir = _dirs(tmp_path, monkeypatch)
        (output_dir / "some-file.png").write_bytes(b"x")
        thumb_url = reverse("vision-engine-file-thumbnail", args=["output", "some-file.png"])

        with posture(POSTURE_PERSONAL, admin_sees_content=True):
            sign_in(client, make_admin())
            shown = client.get(reverse("vision-engine-files"))
            served = client.get(thumb_url)
        assert thumb_url in shown.content.decode()
        assert served.status_code == 200

        with posture(POSTURE_PERSONAL, admin_sees_content=False):
            sign_in(client, make_admin())
            hidden = client.get(reverse("vision-engine-files"))
            refused = client.get(thumb_url)
        assert thumb_url not in hidden.content.decode()
        assert refused.status_code == 404

    def test_an_unsafe_name_404s_rather_than_traversing(self, client, tmp_path, monkeypatch):
        _dirs(tmp_path, monkeypatch)
        with posture(POSTURE_PERSONAL, admin_sees_content=True):
            sign_in(client, make_admin())
            response = client.get(
                reverse("vision-engine-file-thumbnail", args=["output", ".."])
            )
        assert response.status_code == 404

    def test_an_unknown_kind_404s(self, client, tmp_path, monkeypatch):
        output_dir, _input_dir = _dirs(tmp_path, monkeypatch)
        (output_dir / "some-file.png").write_bytes(b"x")
        with posture(POSTURE_PERSONAL, admin_sees_content=True):
            sign_in(client, make_admin())
            response = client.get(
                reverse("vision-engine-file-thumbnail", args=["bogus", "some-file.png"])
            )
        assert response.status_code == 404

    def test_a_symlink_is_refused_even_though_the_target_exists(self, client, tmp_path, monkeypatch):
        """`_list_dir` already skips a symlink when listing (`entry.
        is_file(follow_symlinks=False)`) -- this pins the SAME refusal
        for a name typed or replayed directly against the serving
        route, which does not go through `_list_dir` at all."""
        output_dir, _input_dir = _dirs(tmp_path, monkeypatch)
        real = tmp_path / "real-target.png"
        real.write_bytes(b"x")
        (output_dir / "link.png").symlink_to(real)

        with posture(POSTURE_PERSONAL, admin_sees_content=True):
            sign_in(client, make_admin())
            response = client.get(
                reverse("vision-engine-file-thumbnail", args=["output", "link.png"])
            )
        assert response.status_code == 404

    def test_the_content_off_refusal_is_byte_identical_to_a_missing_file(
        self, client, tmp_path, monkeypatch,
    ):
        """Finding 8: an administrator with `sees_all_content` off must
        not be able to tell "you may not see this" apart from "there is
        no such file" by diffing two responses for the SAME name --
        same status AND same body, not merely the same code."""
        output_dir, _input_dir = _dirs(tmp_path, monkeypatch)
        thumb_url = reverse("vision-engine-file-thumbnail", args=["output", "same-name.png"])

        with posture(POSTURE_PERSONAL, admin_sees_content=True):
            sign_in(client, make_admin())
            missing = client.get(thumb_url)  # file genuinely absent

        (output_dir / "same-name.png").write_bytes(b"x")
        with posture(POSTURE_PERSONAL, admin_sees_content=False):
            sign_in(client, make_admin())
            hidden = client.get(thumb_url)  # file present, content withheld

        assert missing.status_code == hidden.status_code == 404
        assert missing.content == hidden.content

    def test_a_nul_byte_in_the_name_is_refused_not_a_500(self, client, tmp_path, monkeypatch):
        """Finding 2: `Path.unlink`/`os.scandir` raise `ValueError` (not
        `OSError`) on a NUL-carrying path -- this must 404, not 500.
        The URL path itself cannot carry a raw NUL (Django's resolver
        already refuses it), so this drives the view function directly
        with a name that could only arrive through some other caller of
        `engine_file_thumbnail`."""
        from django.test import RequestFactory

        from tools.vision.maintenance import engine_file_thumbnail

        _dirs(tmp_path, monkeypatch)
        request = RequestFactory().get("/vision/engine-files/thumb/output/x/")
        with posture(POSTURE_PERSONAL, admin_sees_content=True):
            admin = make_admin()
            request.user = admin
            with pytest.raises(Http404):
                engine_file_thumbnail(request, "output", "evil\x00.png")


@pytest.mark.django_db
class TestDelete:
    def test_deletes_exactly_the_named_files(self, client, tmp_path, monkeypatch):
        output_dir, input_dir = _dirs(tmp_path, monkeypatch)
        (output_dir / "keep.png").write_bytes(b"x")
        (output_dir / "gone.png").write_bytes(b"x")
        (input_dir / "gone-input.png").write_bytes(b"x")

        with posture(POSTURE_PERSONAL):
            sign_in(client, make_admin())
            response = client.post(
                reverse("vision-engine-files-delete"),
                {"files": ["output:gone.png", "input:gone-input.png"]},
            )

        assert response.status_code == 302
        assert not (output_dir / "gone.png").exists()
        assert not (input_dir / "gone-input.png").exists()
        assert (output_dir / "keep.png").exists()

    def test_a_missing_file_is_missing_ok_not_an_error(self, client, tmp_path, monkeypatch):
        _dirs(tmp_path, monkeypatch)
        with posture(POSTURE_PERSONAL):
            sign_in(client, make_admin())
            response = client.post(
                reverse("vision-engine-files-delete"), {"files": ["output:never-existed.png"]},
            )
        assert response.status_code == 302

    def test_traversal_and_absolute_names_are_dropped_not_deleted(self, client, tmp_path, monkeypatch):
        output_dir, _input_dir = _dirs(tmp_path, monkeypatch)
        outside = tmp_path / "outside.png"
        outside.write_bytes(b"do not delete me")
        (output_dir / "real.png").write_bytes(b"x")

        with posture(POSTURE_PERSONAL):
            sign_in(client, make_admin())
            response = client.post(
                reverse("vision-engine-files-delete"),
                {"files": [f"output:../{outside.name}", f"output:{outside}", "output:real.png"]},
            )

        assert response.status_code == 302
        assert outside.exists()
        assert not (output_dir / "real.png").exists()

    def test_a_member_may_not_delete(self, client, tmp_path, monkeypatch):
        output_dir, _input_dir = _dirs(tmp_path, monkeypatch)
        (output_dir / "keep.png").write_bytes(b"x")

        with posture(POSTURE_PERSONAL):
            sign_in(client, make_user())
            response = client.post(
                reverse("vision-engine-files-delete"), {"files": ["output:keep.png"]},
            )

        assert response.status_code == 403
        assert (output_dir / "keep.png").exists()

    def test_records_one_audit_line_for_the_whole_request(self, client, tmp_path, monkeypatch):
        output_dir, _input_dir = _dirs(tmp_path, monkeypatch)
        (output_dir / "a.png").write_bytes(b"x")
        (output_dir / "b.png").write_bytes(b"x")

        with posture(POSTURE_PERSONAL):
            sign_in(client, make_admin())
            client.post(
                reverse("vision-engine-files-delete"),
                {"files": ["output:a.png", "output:b.png"]},
            )

        events = AuditEvent.objects.filter(action=ENGINE_FILE_DELETED)
        assert events.count() == 1
        assert events.first().detail["count"] == 2

    def test_an_empty_selection_records_no_audit_line(self, client, tmp_path, monkeypatch):
        _dirs(tmp_path, monkeypatch)
        with posture(POSTURE_PERSONAL):
            sign_in(client, make_admin())
            response = client.post(reverse("vision-engine-files-delete"), {"files": []})

        assert response.status_code == 302
        assert AuditEvent.objects.filter(action=ENGINE_FILE_DELETED).count() == 0


@pytest.mark.django_db
class TestSelectOrphansPreselect:
    def test_select_orphans_prechecks_only_the_orphaned_rows(self, client, tmp_path, monkeypatch):
        output_dir, _input_dir = _dirs(tmp_path, monkeypatch)
        job = _job()
        (output_dir / f"{job.id}_00001_.png").write_bytes(b"x")
        (output_dir / "orphan.png").write_bytes(b"x")

        with posture(POSTURE_PERSONAL):
            sign_in(client, make_admin())
            body = client.get(
                reverse("vision-engine-files"), {"select": "orphans"}
            ).content.decode()

        def _is_checked(value: str) -> bool:
            _before, _, after = body.partition(f'value="{value}"')
            tag, _, _rest = after.partition(">")
            return "checked" in tag

        assert _is_checked(f"output:{job.id}_00001_.png") is False
        assert _is_checked("output:orphan.png") is True


@pytest.mark.django_db
class TestSelectAllLink:
    """Finding 3: select mode must offer "Select all" (?select=all)
    alongside the other select-mode links, the same placement the
    gallery's own bulk bar uses (tools/vision/templates/vision/
    gallery.html)."""

    def test_select_all_link_is_offered_in_select_mode(self, client, tmp_path, monkeypatch):
        _dirs(tmp_path, monkeypatch)
        with posture(POSTURE_PERSONAL):
            sign_in(client, make_admin())
            body = client.get(
                reverse("vision-engine-files"), {"select": "1"}
            ).content.decode()

        assert f'{reverse("vision-engine-files")}?select=all' in body
        assert "Select all" in body

    def test_select_all_precheks_every_row(self, client, tmp_path, monkeypatch):
        output_dir, _input_dir = _dirs(tmp_path, monkeypatch)
        (output_dir / "a.png").write_bytes(b"x")
        (output_dir / "b.png").write_bytes(b"x")

        with posture(POSTURE_PERSONAL):
            sign_in(client, make_admin())
            body = client.get(
                reverse("vision-engine-files"), {"select": "all"}
            ).content.decode()

        def _is_checked(value: str) -> bool:
            _before, _, after = body.partition(f'value="{value}"')
            tag, _, _rest = after.partition(">")
            return "checked" in tag

        assert _is_checked("output:a.png") is True
        assert _is_checked("output:b.png") is True
