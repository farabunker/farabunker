"""Unit tests for the /vision/ create page and the shared nav entry (spec §4.7)."""
from __future__ import annotations

import importlib
import json
import re
from datetime import timedelta
from unittest.mock import patch

import pytest
from django import forms as django_forms
from django.test import Client, override_settings
from django.urls import clear_url_caches, reverse
from django.utils import timezone
from django.utils.html import escape

from models.registry.models import ModelConnection, RoleBinding
from models.contracts import operations
from models.contracts.engines import ENGINES
from models.contracts.engines.base import Asset
from identity.contracts.postures import POSTURE_ENTERPRISE
from models.contracts.operations import TXT2IMG, UPSCALE, Operation, Param
from models.contracts.roles import VISION_GENERATE_ROLE
from tools.vision.models import GenerationJob, JobInput
from tools.vision.tests._helpers import (
    StubEngine, clear_bindings, grant, make_entitlement, make_user, posture, sign_in,
    stored_output,
)


@pytest.fixture
def client():
    return Client()


@pytest.fixture(autouse=True)
def _clear_bindings(db):
    clear_bindings()


def _bind():
    connection = ModelConnection.objects.create(
        name="stub image model", engine="stubengine", endpoint="http://stub:9999",
        model_id="stub.safetensors", capabilities=["image-generation"],
    )
    RoleBinding.objects.create(role_key=VISION_GENERATE_ROLE, connection=connection)


def _done_job(prompt="a lighthouse", seed=42):
    """A finished job, for the surfaces that render a card. No files on
    disk: the card links outputs by URL and never reads one."""
    return GenerationJob.objects.create(
        operation="txt2img",
        params={
            "prompt": prompt, "negative_prompt": "", "width": 512, "height": 512,
            "steps": 20, "cfg_scale": 7.0, "seed": seed, "sampler": "euler",
            "scheduler": "normal", "batch_size": 1,
        },
        seed=seed, engine="stubengine", model_id="stub.safetensors",
        endpoint="http://stub:9999",
        model_fingerprint="stubengine:stub.safetensors:None",
        status=GenerationJob.Status.DONE,
    )


def _engine(healthy=True, choices=None):
    engine = StubEngine(healthy=healthy)
    engine.list_choices = lambda endpoint, key: (choices or {}).get(key, ())
    return patch.dict(ENGINES, {"stubengine": engine})


def _field_block(body, key):
    """The single `.field` wrapper that renders `key`, isolated from the
    rest of the page. `vision/_field.html` never nests one `.field` div
    inside another, so splitting the whole page on the wrapper's own
    opening tag and keeping the chunk that names `key` in a widget
    attribute finds exactly one block."""
    blocks = re.split(r'(?=<div class="field field--)', body)
    matches = [b for b in blocks if re.search(rf'name="{re.escape(key)}"', b)]
    assert len(matches) == 1, (key, len(matches))
    return matches[0]


@pytest.mark.django_db
class TestCreatePage:
    def test_unbound_role_shows_the_banner_and_a_link_to_the_console(self, client):
        response = client.get(reverse("vision-create"))
        body = response.content.decode()

        assert response.status_code == 200
        assert response.context["preflight"].state == "unbound"
        assert "No model assigned for Image generation" in body
        assert reverse("inference-console") in body

    def test_unreachable_engine_says_so_without_hiding_the_form(self, client):
        _bind()
        with _engine(healthy=False):
            response = client.get(reverse("vision-create"))

        body = response.content.decode()
        assert response.context["preflight"].state == "unreachable"
        assert "not reachable" in body
        assert 'name="prompt"' in body

    def test_ready_state_renders_the_schema_form_with_live_sampler_options(self, client):
        _bind()
        with _engine(choices={"sampler": ("euler", "heun"), "scheduler": ("karras",)}):
            response = client.get(reverse("vision-create"))

        body = response.content.decode()
        assert response.context["preflight"].state == "ready"
        assert '<option value="euler"' in body
        assert '<option value="karras"' in body
        assert 'name="negative_prompt"' in body
        assert 'name="steps"' in body

    def test_recent_jobs_are_listed_newest_first(self, client):
        _bind()
        older = GenerationJob.objects.create(
            operation="txt2img", params={"prompt": "one"}, seed=1, engine="stubengine",
            model_id="stub.safetensors", model_fingerprint="f",
        )
        newer = GenerationJob.objects.create(
            operation="txt2img", params={"prompt": "two"}, seed=2, engine="stubengine",
            model_id="stub.safetensors", model_fingerprint="f",
        )
        with _engine():
            response = client.get(reverse("vision-create"))

        assert [job.id for job in response.context["jobs"]] == [newer.id, older.id]

    def test_ready_state_shows_a_non_warning_status_line_naming_the_bound_model(self, client):
        """Fix-wave item 2: bound + reachable used to render NO status line
        at all -- silence that left the operator guessing whether the page
        had picked anything up. The `ready` branch must name the exact
        checkpoint, engine, and endpoint, and must not carry the `warn`
        banner styling the unbound/unreachable branches use."""
        _bind()
        with _engine():
            response = client.get(reverse("vision-create"))

        body = response.content.decode()
        assert response.context["preflight"].state == "ready"
        assert "stub.safetensors" in body
        assert "stubengine" in body
        assert "http://stub:9999" in body
        assert '<div class="banner warn">' not in body

    def test_non_terminal_job_card_carries_the_no_js_refresh_note(self, client):
        """Fix-wave item 3: the server-rendered fallback for a still-running
        job must be unchanged by the JS-side `markLive` refactor -- a
        non-terminal card's card still emits `.no-js-note` / "Refresh to
        update." so the page works with JS disabled."""
        _bind()
        GenerationJob.objects.create(
            operation="txt2img", params={"prompt": "one"}, seed=1, engine="stubengine",
            model_id="stub.safetensors", model_fingerprint="f",
        )
        with _engine():
            response = client.get(reverse("vision-create"))

        body = response.content.decode()
        assert 'class="muted no-js-note"' in body
        assert "Refresh to update." in body

    def test_jobs_empty_placeholder_has_an_id_when_there_are_no_jobs(self, client):
        """Fix-wave item 4: the submit handler removes the "Nothing
        generated yet." paragraph by id right after inserting the first
        card, so it needs a stable `id="jobs-empty"` to find."""
        response = client.get(reverse("vision-create"))

        body = response.content.decode()
        assert 'id="jobs-empty"' in body
        assert "Nothing generated yet." in body

    def test_jobs_empty_placeholder_is_absent_once_a_job_exists(self, client):
        _bind()
        GenerationJob.objects.create(
            operation="txt2img", params={"prompt": "one"}, seed=1, engine="stubengine",
            model_id="stub.safetensors", model_fingerprint="f",
        )
        with _engine():
            response = client.get(reverse("vision-create"))

        assert 'id="jobs-empty"' not in response.content.decode()

    def test_the_page_never_500s_when_the_engine_blows_up(self, client):
        _bind()
        engine = StubEngine()
        engine.list_choices = lambda endpoint, key: (_ for _ in ()).throw(RuntimeError("boom"))
        with patch.dict(ENGINES, {"stubengine": engine}):
            response = client.get(reverse("vision-create"))

        assert response.status_code == 200

    def test_the_page_never_500s_when_the_bound_engine_is_not_registered(self, client):
        """`ModelConnection.engine` is a plain CharField -- nothing constrains
        it to a name `ENGINES` actually has. A stale or hand-edited binding
        must degrade the same way an unreachable engine does: free-text
        choice fields, not a 500."""
        connection = ModelConnection.objects.create(
            name="orphaned binding", engine="not-a-real-engine", endpoint="http://stub:9999",
            model_id="stub.safetensors", capabilities=["image-generation"],
        )
        RoleBinding.objects.create(role_key=VISION_GENERATE_ROLE, connection=connection)

        response = client.get(reverse("vision-create"))
        body = response.content.decode()

        assert response.status_code == 200
        form = response.context["form"]
        assert isinstance(form.fields["sampler"], django_forms.CharField)
        assert 'type="text" name="sampler"' in body


@pytest.mark.django_db
class TestSharedNavEntry:
    """The shared shell (foundation/templates/_shell.html) links to /vision/ as
    "Images", gated on the vision feature so it never points at a route
    that isn't mounted -- AND, since UI-1, on `vision.generate` actually
    having a model bound, so it never points at a surface that could only
    apologise. Both halves are one key (`surface_available.images`); the
    four tests below drive them separately."""

    def test_the_link_appears_on_every_page_once_the_role_is_bound(self, client):
        _bind()
        body = client.get(reverse("rag-ask-page")).content.decode()
        assert f'href="{reverse("vision-create")}"' in body
        assert ">Images</a>" in body

    def test_the_link_is_absent_while_nothing_is_bound(self, client):
        """UI-1 decision 2: availability is model-bound. Feature on, no
        `vision.generate` binding, no entry -- and the path to fixing
        that is the settings area — Settings is always shown, and its
        sidebar leads to Models."""
        body = client.get(reverse("rag-ask-page")).content.decode()
        assert ">Images</a>" not in body

    def test_the_vision_page_marks_that_entry_current(self, client):
        _bind()
        body = client.get(reverse("vision-create")).content.decode()
        assert f'<a href="{reverse("vision-create")}" class="current">Images</a>' in body

    def test_the_link_is_gone_when_the_feature_is_off(self, client):
        """With the feature off there is no /vision/ route at all, so the
        nav must not render the entry -- and the url tag inside the
        `if` must never be evaluated (an unmounted route would raise
        NoReverseMatch). Bound first, so the ONLY thing keeping the entry
        off the page is the flag."""
        _bind()
        from config import urls as config_urls

        try:
            with override_settings(FARABUNKER_FEATURES=frozenset()):
                importlib.reload(config_urls)
                clear_url_caches()
                body = client.get(reverse("rag-ask-page")).content.decode()
                assert ">Images</a>" not in body
        finally:
            # Restore AFTER `override_settings` has already put the real
            # feature set back -- reloading while still inside the `with`
            # would rebuild the URLconf from the OVERRIDDEN (empty) settings
            # and leave every later test without the vision/ mount.
            importlib.reload(config_urls)
            clear_url_caches()


@pytest.mark.django_db
class TestBannerLinksToSetup:
    """The banner points at the engine's own section of /setup/, so an
    operator who has nothing bound is one click from the install steps."""

    def test_unbound_points_at_the_image_engine_section(self, client):
        body = client.get(reverse("vision-create")).content.decode()
        assert f'{reverse("setup-index")}#engine-comfyui' in body

    def test_bound_but_unreachable_points_at_the_bound_engine_section(self, client):
        _bind()
        with _engine(healthy=False):
            body = client.get(reverse("vision-create")).content.decode()

        assert f'{reverse("setup-index")}#engine-stubengine' in body


IMG2IMG = Operation(
    key="img2img", label="Image to image", capability="image-generation",
    output_media="image/png",
    params=(
        Param("prompt", "text", "Prompt", default="", required=True),
        Param("denoise", "float", "Denoise", default=0.6, min=0, max=1),
        Param("seed", "seed", "Seed", default=None),
    ),
)


@pytest.mark.django_db
class TestOperationSurface:
    """The page serves the operation REGISTRY through one `<select>`:
    every registered mode is an option, always, and the ones this model
    cannot run are disabled with the reason (ADR 0012 D-EDIT-13)."""

    def test_every_registered_operation_is_an_option(self, client):
        body = client.get(reverse("vision-create")).content.decode()

        assert '<select id="operation" name="operation"' in body
        for key in ("txt2img", "img2img", "inpaint", "upscale", "edit"):
            assert f'<option value="{key}"' in body
        assert "Edit an image" in body  # its own schema label, no longer merged away

    def test_an_operation_the_picked_model_cannot_run_is_disabled_with_a_reason(self, client):
        connection = ModelConnection.objects.create(
            name="picked", engine="stubengine", endpoint="http://stub:9999",
            model_id="w.gguf", capabilities=["image-generation"],
            config={"family": "flux2"},
        )

        class _EditOnly(StubEngine):
            def supported_operations(self, model_id, endpoint, family=""):
                return ("edit",)

        with patch.dict(ENGINES, {"stubengine": _EditOnly()}, clear=True):
            body = client.get(
                reverse("vision-create"), {"connection": str(connection.pk)}
            ).content.decode()

        assert "No inpaint graph for the flux2 family." in body
        assert 'value="inpaint" disabled' in body
        assert 'value="edit" selected' in body

    def test_the_select_lives_in_the_pickers_own_get_form(self, client):
        """No JS required: changing the mode is a GET of the same little
        form the model picker already is, so it can only ever produce a
        query parameter -- which is why `?operation=` is the canonical
        URL (spec section 4)."""
        body = client.get(reverse("vision-create")).content.decode()

        assert '<form class="model-picker" method="get"' in body
        assert 'class="op-chooser"' not in body

    def test_the_query_string_names_the_operation(self, client):
        body = client.get(reverse("vision-create"), {"operation": "upscale"}).content.decode()

        assert '<input type="hidden" name="operation" value="upscale">' in body
        assert 'value="upscale" selected' in body

    def test_the_path_alias_renders_the_identical_page(self, client):
        """`op/<key>/` stays a rendering alias -- no redirect, no extra
        round trip -- so a bookmark or an external link keeps working."""
        aliased = client.get(reverse("vision-create-operation", args=["upscale"]))
        queried = client.get(reverse("vision-create"), {"operation": "upscale"})

        assert aliased.status_code == queried.status_code == 200
        assert aliased.context["operation"].key == "upscale"
        assert queried.context["operation"].key == "upscale"

    def test_the_path_wins_over_the_query_string(self, client):
        response = client.get(
            reverse("vision-create-operation", args=["upscale"]), {"operation": "inpaint"}
        )
        assert response.context["operation"].key == "upscale"

    def test_an_unknown_operation_is_a_plain_404_either_way(self, client):
        assert client.get("/vision/op/controlnet/").status_code == 404
        assert client.get(reverse("vision-create"), {"operation": "controlnet"}).status_code == 404

    def test_the_default_url_still_serves_the_first_registered_operation(self, client):
        response = client.get(reverse("vision-create"))

        assert response.context["operation"].key == "txt2img"
        assert "<h1>Image generation</h1>" in response.content.decode()

    def test_the_headline_is_constant_the_select_names_the_mode(self, client):
        """The `<h1>` no longer changes with the mode -- the select is
        what names it, and a heading that renamed the page each time
        made two operations look like two pages."""
        for key in ("txt2img", "upscale", "edit"):
            body = client.get(reverse("vision-create"), {"operation": key}).content.decode()
            assert "<h1>Image generation</h1>" in body

    def test_the_card_facts_come_from_the_schema(self, client):
        _done_job()

        body = client.get(reverse("vision-create")).content.decode()

        assert "<dt>Seed</dt><dd>42</dd>" in body
        assert "<dt>Sampler</dt><dd>euler</dd>" in body
        assert "<dt>Batch size</dt><dd>1</dd>" in body
        assert "<dt>Model</dt><dd>stub.safetensors</dd>" in body


@pytest.mark.django_db
class TestFactsLayouts:
    """One facts SOURCE (`GenerationJob.facts`), one PARTIAL, two
    presentations: labelled rows on the card, the compact prose line in a
    240px gallery caption where a two-column list would wrap badly."""

    def test_the_card_renders_facts_as_a_definition_list(self, client):
        _done_job(seed=42)

        body = client.get(reverse("vision-create")).content.decode()

        assert '<dl class="job-facts">' in body
        assert "<dt>Seed</dt>" in body
        assert "<dd>42</dd>" in body

    def test_the_card_no_longer_renders_the_dot_separated_line(self, client):
        _done_job(seed=42)

        body = client.get(reverse("vision-create")).content.decode()

        assert "Seed 42 ·" not in body


_PROMPTLESS_UPSCALE_OP = Operation(
    key="upscale", label="Upscale", capability="image-generation",
    output_media="image/png",
    params=(Param("scale", "float", "Scale", default=2.0, min=1, max=4),),
)


@pytest.mark.django_db
class TestCardHeadline:
    """Fix-wave item 6: a card's headline falls back to the operation's
    schema label when the job carries no `prompt` param; txt2img is
    unchanged."""

    def test_a_prompt_less_operations_card_headlines_the_schema_label(self, client):
        GenerationJob.objects.create(
            operation="upscale", params={"scale": 2.0}, seed=1, engine="stubengine",
            model_id="stub.safetensors", model_fingerprint="f",
            status=GenerationJob.Status.DONE,
        )
        with patch.dict(operations._OPERATIONS, {"upscale": _PROMPTLESS_UPSCALE_OP}):
            body = client.get(reverse("vision-create")).content.decode()

        assert '<h3 class="job-title">Upscale</h3>' in body

    def test_txt2img_cards_still_headline_the_prompt(self, client):
        _done_job()

        body = client.get(reverse("vision-create")).content.decode()

        assert '<h3 class="job-title">a lighthouse</h3>' in body


@pytest.mark.django_db
class TestReuseResolvesOperation:
    """Fix-wave item 2: the gallery's "Reuse settings" link
    (`?reuse=<job-id>`) used to land on the DEFAULT operation even when the
    reused job ran a different, still-registered one."""

    def _job(self, operation="img2img", params=None):
        return GenerationJob.objects.create(
            operation=operation,
            params=params if params is not None else {"prompt": "a lighthouse", "denoise": 0.6, "seed": 42},
            seed=42, engine="stubengine", model_id="stub.safetensors", model_fingerprint="f",
        )

    def test_reusing_a_registered_non_default_operations_job_resolves_the_page_to_it(self, client):
        job = self._job()

        with patch.dict(operations._OPERATIONS, {"img2img": IMG2IMG}):
            response = client.get(reverse("vision-create"), {"reuse": str(job.id)})

        assert response.context["operation"].key == "img2img"
        body = response.content.decode()
        assert 'name="denoise"' in body
        assert response.context["form"].fields["denoise"].initial == 0.6

    def test_a_url_named_operation_wins_over_the_reused_jobs(self, client):
        job = self._job()

        with patch.dict(operations._OPERATIONS, {"img2img": IMG2IMG}):
            response = client.get(
                reverse("vision-create-operation", args=["txt2img"]), {"reuse": str(job.id)}
            )

        assert response.context["operation"].key == "txt2img"
        assert 'name="width"' in response.content.decode()

    def test_reusing_an_unregistered_operations_job_falls_back_to_the_default(self, client):
        job = self._job(operation="retired", params={"prompt": "a lighthouse"})

        response = client.get(reverse("vision-create"), {"reuse": str(job.id)})

        assert response.context["operation"].key == "txt2img"


@pytest.mark.django_db
class TestSharedCardChrome:
    """The card fragment is rendered by three views; the rules and markup it
    needs are owned once, not copied per page."""

    def test_the_create_page_renders_the_shared_delete_control(self, client):
        _done_job()
        response = client.get(reverse("vision-create"))

        assert "vision/_delete_control.html" in [t.name for t in response.templates]

    def test_the_card_rules_come_from_the_module_base_not_the_page(self, client):
        """`vision/base.html` declares them, so the standalone gallery gets
        the same card chrome without a second copy.

        The `.delete-disclosure` rule is searched for with its leading
        newline + two-space indent so the base rule is counted once even
        though the 2026-08-25 card redesign also scopes it narrower with
        `.job-foot .delete-disclosure { margin-top: 0; }` -- a second,
        deliberately different selector that happens to contain the same
        substring, not a duplicate declaration of the base rule."""
        create = client.get(reverse("vision-create")).content.decode()
        gallery = client.get(reverse("vision-gallery")).content.decode()

        for rule in ("\n  .delete-disclosure {", ".delete-confirm {", "button.danger {"):
            assert create.count(rule) == 1
            assert gallery.count(rule) == 1

    def test_the_danger_hex_appears_only_as_the_token_declaration(self, client):
        """`foundation/templates/_shell.html` is an inline `<style>` in every page's
        `<head>` (the project ships no external stylesheet), so the token's
        VALUE necessarily reaches the body. The rule this pins is therefore
        "declared once, never used as a property value" -- which is exactly
        what a token is for."""
        for name in ("vision-create", "vision-gallery"):
            body = client.get(reverse(name)).content.decode()
            assert body.count("#b3261e") == 1  # the light-scheme token declaration
            assert "--danger: #b3261e" in body
            assert "color: #b3261e" not in body
            assert "border-color: #b3261e" not in body
            assert "var(--danger)" in body


@pytest.mark.django_db
class TestCardActionParity:
    """A Recent card must offer exactly what a gallery card offers, from the
    SAME partial -- the owner's request was "I should have all the options
    available in gallery here as well", and a second copy of the links
    markup is how the two silently drift apart."""

    def test_a_recent_card_offers_every_gallery_action(self, client, tmp_path):
        output = stored_output(tmp_path)

        # The registry is pinned exactly as `test_views_gallery.py`'s
        # `TestUseInLinks` pins it: `input_targets()` reads the live
        # registry, so a test that asserts a specific mode's link must own
        # what is registered rather than inherit whatever ran first.
        # `operations.IMG2IMG` -- qualified, like `TestStoredInputPrefill`
        # above -- because this module's own bare `IMG2IMG` (defined further
        # down for `TestOperationSurface`) declares no file param and would
        # silently produce no "Use in ..." link.
        with patch.dict(operations._OPERATIONS, {"txt2img": TXT2IMG, "img2img": operations.IMG2IMG}):
            body = client.get(reverse("vision-create")).content.decode()

        assert "Use in Image to image" in body
        assert (
            f"{reverse('vision-create')}?operation=img2img"
            f"&input_init_image=output:{output.id}"
        ) in body
        assert f"{reverse('vision-create')}?reuse={output.job.id}" in body
        assert f"{reverse('vision-output-file', args=[output.id])}?download=1" in body

    def test_use_in_links_are_not_merged_across_the_live_registry(self, client, tmp_path):
        """Review fix 2: `input_targets` no longer merges `img2img` and
        `edit` into one "Use in Image to image" link (ADR 0012 D-EDIT-13).
        Unbound, with the REAL, unpinned registry (all five registered
        operations) -- the only case that ever had both at once -- both
        of their own "Use in ..." links must appear, each pointing at its
        own schema's first file param."""
        output = stored_output(tmp_path)

        body = client.get(reverse("vision-create")).content.decode()

        assert "Use in Image to image" in body
        assert "Use in Edit an image" in body
        assert (
            f"{reverse('vision-create')}?operation=img2img"
            f"&input_init_image=output:{output.id}"
        ) in body
        assert (
            f"{reverse('vision-create')}?operation=edit"
            f"&input_init_image=output:{output.id}"
        ) in body

    def test_the_poll_fragment_offers_them_too(self, client, tmp_path):
        """`job_status` returns the card on its own; a card that loses its
        actions the moment the page polls is not parity."""
        output = stored_output(tmp_path)

        with patch.dict(operations._OPERATIONS, {"txt2img": TXT2IMG, "img2img": operations.IMG2IMG}):
            body = client.get(
                reverse("vision-job-status", args=[output.job.id])
            ).content.decode()

        assert "Use in Image to image" in body
        assert f"{reverse('vision-output-file', args=[output.id])}?download=1" in body

    def test_both_pages_render_the_one_partial(self, client, tmp_path):
        stored_output(tmp_path)

        create = client.get(reverse("vision-create"))
        gallery = client.get(reverse("vision-gallery"))

        for response in (create, gallery):
            assert "vision/_output_actions.html" in [t.name for t in response.templates]


@pytest.mark.django_db
class TestSecondModeArrivesByRegistrationAlone:
    def test_the_select_lists_a_newly_registered_mode_with_no_template_edit(self, client):
        body = client.get(reverse("vision-create")).content.decode()
        assert '<option value="img2img"' in body
        assert "Image to image" in body

    def test_picking_img2img_makes_its_own_fields_the_enabled_ones(self, client):
        _bind()
        with _engine(choices={"sampler": ("euler",), "scheduler": ("normal",)}):
            body = client.get(
                reverse("vision-create"), {"operation": "img2img"}
            ).content.decode()

        assert 'name="init_image"' in body
        assert 'accept="image/*"' in body
        assert 'name="denoise"' in body
        # Still RENDERED, because the form is constant -- but not for
        # this mode, so disabled and explained.
        assert 'name="mask_image"' in body
        assert "Not used by Image to image." in body


@pytest.mark.django_db
class TestJobInputsAreVisible:
    def _job_with_input(self):
        job = _done_job()
        return JobInput.objects.create(
            job=job, param_key="init_image", path="/tmp/init_image-beach.png",
            media_type="image/png",
        )

    def test_the_card_links_the_input_it_ran_on(self, client):
        job_input = self._job_with_input()
        body = client.get(reverse("vision-create")).content.decode()
        assert reverse("vision-input-file", args=[job_input.id]) in body

    def test_the_json_form_reports_inputs_by_url_never_by_path(self, client):
        job_input = self._job_with_input()
        payload = client.get(
            reverse("vision-job-status", args=[job_input.job_id]) + "?format=json"
        ).json()
        assert payload["inputs"] == [
            {
                "id": job_input.id,
                "param_key": "init_image",
                "url": reverse("vision-input-file", args=[job_input.id]),
                "media_type": "image/png",
            }
        ]
        assert "/tmp/" not in json.dumps(payload)


@pytest.mark.django_db
class TestCardStructure:
    """The redesign, pinned where it is behaviour rather than taste: the
    regions exist, the status is a chip, the poll seam is untouched."""

    def test_the_card_has_a_head_body_and_foot(self, client, tmp_path):
        stored_output(tmp_path)

        body = client.get(reverse("vision-create")).content.decode()

        assert 'class="job-head"' in body
        assert 'class="job-body"' in body
        assert 'class="job-foot"' in body

    def test_the_status_is_a_chip_coloured_by_status(self, client):
        _done_job()

        body = client.get(reverse("vision-create")).content.decode()

        assert '<span class="chip chip-done">Done</span>' in body

    def test_a_failed_job_gets_the_failed_chip_and_keeps_its_error(self, client):
        job = _done_job()
        job.status = GenerationJob.Status.FAILED
        job.error = "Allocation on device"
        job.save(update_fields=["status", "error"])

        body = client.get(reverse("vision-create")).content.decode()

        assert '<span class="chip chip-failed">Failed</span>' in body
        assert "Allocation on device" in body

    def test_outputs_are_tiles_and_inputs_are_a_labelled_strip(self, client, tmp_path):
        output = stored_output(tmp_path)
        JobInput.objects.create(
            job=output.job, param_key="init_image", path="/tmp/init_image-beach.png",
            media_type="image/png",
        )

        body = client.get(reverse("vision-create")).content.decode()

        assert 'class="output-tile"' in body
        assert 'class="job-inputs"' in body
        assert '<span class="strip-label">Inputs</span>' in body

    def test_a_running_card_still_carries_the_poll_attribute(self, client):
        job = _done_job()
        job.status = GenerationJob.Status.RUNNING
        job.save(update_fields=["status"])

        body = client.get(reverse("vision-create")).content.decode()

        assert f'data-job-poll="{reverse("vision-job-status", args=[job.id])}"' in body
        assert 'class="muted no-js-note"' in body

    def test_a_picked_connections_running_card_polls_with_that_connection(self, client):
        """B2, closing the loop `_render_card` alone leaves open: the
        `data-job-poll` URL itself must carry the picked model, so the
        page's own poll script (create.html) re-requests the SAME model on
        every refresh instead of silently reverting to the role binding
        the moment the browser polls."""
        connection = ModelConnection.objects.create(
            name="picked", engine="stubengine", endpoint="http://stub:9999",
            model_id="w.safetensors", capabilities=["image-generation"],
        )
        job = _done_job()
        job.status = GenerationJob.Status.RUNNING
        job.save(update_fields=["status"])

        with _engine():
            body = client.get(
                reverse("vision-create"), {"connection": str(connection.pk)}
            ).content.decode()

        expected = (
            f'data-job-poll="{reverse("vision-job-status", args=[job.id])}'
            f'?connection={connection.pk}"'
        )
        assert expected in body


@pytest.mark.django_db
class TestStoredInputPrefill:
    """`operations.IMG2IMG` and `operations.TXT2IMG` (the real, registered
    schemas -- qualified to avoid the module's own simplified `IMG2IMG`
    double above, which declares no `init_image` file param and would
    silently pass this class for the wrong reason)."""

    def test_the_page_carries_the_reference_as_a_hidden_field(self, client, tmp_path):
        _bind()
        output = stored_output(tmp_path)
        url = (
            reverse("vision-create-operation", args=["img2img"])
            + f"?input_init_image=output:{output.id}"
        )
        with patch.dict(
            operations._OPERATIONS, {"txt2img": operations.TXT2IMG, "img2img": operations.IMG2IMG}
        ), _engine():
            body = client.get(url).content.decode()

        assert f'<input type="hidden" name="input_init_image" value="output:{output.id}">' in body
        assert reverse("vision-output-file", args=[output.id]) in body
        assert "Init image" in body

    def test_an_unresolvable_reference_is_ignored_rather_than_breaking_the_page(self, client):
        _bind()
        url = reverse("vision-create-operation", args=["img2img"]) + "?input_init_image=output:4242"
        with patch.dict(
            operations._OPERATIONS, {"txt2img": operations.TXT2IMG, "img2img": operations.IMG2IMG}
        ), _engine():
            response = client.get(url)
        assert response.status_code == 200
        assert 'name="input_init_image"' not in response.content.decode()

    def test_the_query_form_of_the_link_carries_the_reference_too(self, client, tmp_path):
        """The canonical link the gallery now emits, followed for real."""
        _bind()
        output = stored_output(tmp_path)
        url = (
            f"{reverse('vision-create')}?operation=img2img"
            f"&input_init_image=output:{output.id}"
        )
        with patch.dict(
            operations._OPERATIONS,
            {"txt2img": operations.TXT2IMG, "img2img": operations.IMG2IMG},
        ), _engine():
            body = client.get(url).content.decode()

        assert f'<input type="hidden" name="input_init_image" value="output:{output.id}">' in body
        assert '<input type="hidden" name="operation" value="img2img">' in body

    def test_the_stored_block_renders_under_its_own_field_not_at_the_top(self, client, tmp_path):
        """Owner screenshot: this block used to render as one loop at the
        TOP of the whole form, before `<fieldset>` -- "Using this image as
        Init image" sat above the fold while the Init image file input,
        further down, still read "No file chosen". It now renders inside
        `init_image`'s own `.field` wrapper (`_field.html`), directly
        under that field's widget, matched by `stored.param_key == f.name`."""
        _bind()
        output = stored_output(tmp_path)
        url = (
            reverse("vision-create-operation", args=["img2img"])
            + f"?input_init_image=output:{output.id}"
        )
        with patch.dict(
            operations._OPERATIONS, {"txt2img": operations.TXT2IMG, "img2img": operations.IMG2IMG}
        ), _engine():
            body = client.get(url).content.decode()

        # Nothing at the old top-of-form position, before any field
        # wrapper. `class="stored-input"` (the rendered div), not the
        # bare word, so the page's own `<style>` block (which still
        # defines the `.stored-input` selector) does not false-positive.
        top = body.split('<div class="field field--')[0]
        assert 'class="stored-input"' not in top

        # `_field_block` isolates exactly the `init_image` field's own
        # wrapper, bounded by the NEXT field's opening `<div class="field
        # field--`, which is exactly "before the next field's label"
        # without having to know which param is next.
        block = _field_block(body, "init_image")
        widget_index = block.index('name="init_image"')
        stored_index = block.index('class="stored-input"')
        assert stored_index > widget_index

        # The hidden field posted back is unchanged, just relocated.
        assert (
            f'<input type="hidden" name="input_init_image" value="output:{output.id}">'
            in block
        )
        assert (
            "Using this image as Init image — choose a file above to replace it."
            in block
        )


@pytest.mark.django_db
class TestCancelAStoredHandOff:
    """brief-cancel-carry.md Part A -- owner: "when I select use image,
    there is no way to undo it unless I change the url." Uses the real
    registered `INPAINT` operation (two file params: `init_image` and
    `mask_image`) so `remove_url` has an "other `input_*`" to prove it
    keeps."""

    def test_remove_url_drops_only_its_own_param_and_keeps_the_rest(self, client, tmp_path):
        _bind()
        connection = ModelConnection.objects.create(
            name="stub image model 2", engine="stubengine", endpoint="http://stub:9999",
            model_id="stub2.safetensors", capabilities=["image-generation"],
        )
        init_output = stored_output(tmp_path)
        mask_output = stored_output(tmp_path)
        url = (
            f"{reverse('vision-create')}?operation=inpaint&connection={connection.pk}"
            f"&input_init_image=output:{init_output.id}&input_mask_image=output:{mask_output.id}"
        )
        with _engine():
            response = client.get(url)

        stored = {item["param_key"]: item for item in response.context["stored_inputs"]}
        remove_url = stored["init_image"]["remove_url"]
        assert "input_init_image=" not in remove_url
        assert f"input_mask_image=output%3A{mask_output.id}" in remove_url
        assert "operation=inpaint" in remove_url
        assert f"connection={connection.pk}" in remove_url

    def test_the_remove_link_is_rendered_in_the_field_block(self, client, tmp_path):
        _bind()
        output = stored_output(tmp_path)
        url = (
            reverse("vision-create-operation", args=["img2img"])
            + f"?input_init_image=output:{output.id}"
        )
        with patch.dict(
            operations._OPERATIONS, {"txt2img": operations.TXT2IMG, "img2img": operations.IMG2IMG}
        ), _engine():
            body = client.get(url).content.decode()

        block = _field_block(body, "init_image")
        assert '<a class="stored-remove" href="' in block
        assert ">Remove</a>" in block

    def test_the_remove_link_sits_inside_the_thumbnail_column(self, client, tmp_path):
        """Owner feedback: Remove used to render at the far right, after
        the caption text, and was hard to find. `_field.html` now wraps
        the thumbnail and Remove together in `.stored-thumb` so Remove
        sits directly under the thumbnail, left-aligned with it. Checked
        by containment: the remove anchor must fall between
        `.stored-thumb`'s opening tag and its own first closing `</div>`
        (nothing else inside that column is a `<div>`)."""
        _bind()
        output = stored_output(tmp_path)
        url = (
            reverse("vision-create-operation", args=["img2img"])
            + f"?input_init_image=output:{output.id}"
        )
        with patch.dict(
            operations._OPERATIONS, {"txt2img": operations.TXT2IMG, "img2img": operations.IMG2IMG}
        ), _engine():
            body = client.get(url).content.decode()

        block = _field_block(body, "init_image")
        thumb_start = block.index('class="stored-thumb"')
        thumb_end = block.index("</div>", thumb_start)
        remove_index = block.index('<a class="stored-remove" href="')
        assert thumb_start < remove_index < thumb_end

    def test_the_script_wires_up_the_stored_remove_control(self, client):
        """String-level only (no JS execution), same convention as
        `TestChosenFilePreviewScript`: the click handler lives in the SAME
        inline script as the file preview, inside the generate form."""
        _bind()
        with _engine():
            body = client.get(reverse("vision-create")).content.decode()

        form_start = body.index('id="generate-form"')
        form_end = body.index("</form>", form_start)
        script_slice = body[form_start:form_end]
        assert "stored-remove" in script_slice
        assert script_slice.count("<script>") == 1

    def test_the_click_handler_also_drops_the_pickers_own_mirrored_hidden_input(self, client):
        """Live-verified regression (:8002): Remove appeared to work, but
        the NEXT Model/Mode switch brought the image back -- the picker
        form's own server-rendered `input_<key>` mirror (the comment above
        the picker form, create.html) was untouched by the click handler,
        so the picker's GET resubmitted it. String-level only, per the
        brief; the handler must reference `.model-picker` and remove
        EVERY hidden input found there by that name (`querySelectorAll`,
        not `querySelector` -- a second live-verified round found the
        first fix still didn't stick in the browser, so this is
        deliberately defensive rather than "one match is enough")."""
        _bind()
        with _engine():
            body = client.get(reverse("vision-create")).content.decode()

        form_start = body.index('id="generate-form"')
        form_end = body.index("</form>", form_start)
        script_slice = body[form_start:form_end]
        assert ".model-picker" in script_slice
        assert "input[type=hidden]" in script_slice
        assert "querySelectorAll(" in script_slice
        assert "mirror.remove()" in script_slice

    def test_the_remove_link_carries_its_input_name_as_a_data_attribute(self, client, tmp_path):
        """A second live-verified round of the SAME regression: the
        earlier fix read the hidden field's `name` off a SIBLING inside
        `.stored-input`, reached for AFTER the click handler already had
        `link` in hand -- fragile, and apparently not reliable in the
        browser tested. `_field.html` now stamps the exact mirrored name
        directly on the `.stored-remove` link itself, so the script never
        has to reach anywhere else for it."""
        _bind()
        output = stored_output(tmp_path)
        url = (
            reverse("vision-create-operation", args=["img2img"])
            + f"?input_init_image=output:{output.id}"
        )
        with patch.dict(
            operations._OPERATIONS, {"txt2img": operations.TXT2IMG, "img2img": operations.IMG2IMG}
        ), _engine():
            body = client.get(url).content.decode()

        block = _field_block(body, "init_image")
        assert 'data-input-name="input_init_image"' in block

    def test_the_handler_reads_the_data_attribute_before_removing_anything(self, client):
        """String-order assertion, since pure token presence didn't catch
        the earlier live-verified regression: the script must read
        `link.dataset.inputName` (or an equivalent property access on
        `link`) strictly BEFORE the first `.remove()` call in the
        click-handler function -- reading second, off a node the handler
        already deleted, is exactly the class of bug this order rules
        out."""
        _bind()
        with _engine():
            body = client.get(reverse("vision-create")).content.decode()

        form_start = body.index('id="generate-form"')
        form_end = body.index("</form>", form_start)
        script_slice = body[form_start:form_end]
        handler_start = script_slice.index("addEventListener('click'")
        name_read_index = script_slice.index("link.dataset.inputName", handler_start)
        first_remove_index = script_slice.index(".remove()", handler_start)
        assert name_read_index < first_remove_index

    def test_the_carry_handler_only_ever_reads_the_live_generate_form(self, client):
        """The carry-forward listener (`{% block scripts %}`, bottom of
        the page) must read CURRENT field values off the live DOM
        (`new FormData(form)`) and skip every `input_*` name outright --
        never re-add one from a snapshot taken at load, a dataset, or
        anywhere else that would resurrect what the Remove handler above
        just deleted."""
        _bind()
        with _engine():
            body = client.get(reverse("vision-create")).content.decode()

        carry_start = body.index("pickerForm.addEventListener('submit'")
        carry_end = body.index("form.addEventListener('submit'", carry_start)
        carry_slice = body[carry_start:carry_end]
        assert "new FormData(form)" in carry_slice
        assert "indexOf('input_') === 0" in carry_slice
        # No OTHER value source for a carried field -- no snapshot object,
        # no `localStorage`, no re-derivation off `link`/`dataset` inside
        # this handler.
        assert "localStorage" not in carry_slice
        assert "dataset" not in carry_slice


@pytest.mark.django_db
class TestLiveAssetOptions:
    OPERATION = Operation(
        key="assets", label="Assets", capability="image-generation", output_media="image/png",
        params=(
            Param("prompt", "text", "Prompt", default="", required=True),
            Param("loras", "asset", "LoRAs", asset_kind="lora", multiple=True, default=()),
        ),
    )

    def _asset_engine(self, assets=(), blow_up=False):
        engine = StubEngine()
        engine.list_choices = lambda endpoint, key: ()

        def list_assets(endpoint, kind):
            if blow_up:
                raise RuntimeError("boom")
            return [Asset(kind=kind, asset_id=asset_id) for asset_id in assets]

        engine.list_assets = list_assets
        return patch.dict(ENGINES, {"stubengine": engine})

    def test_the_page_offers_the_engines_own_assets(self, client):
        """A `multiple=True` asset field (LoRAs) is a chip group now (T2):
        the platform's `<label class="chip-check">` pattern, never a raw
        `<select multiple>`."""
        _bind()
        with patch.dict(operations._OPERATIONS, {"assets": self.OPERATION}), \
             self._asset_engine(assets=("style.safetensors",)):
            body = client.get(reverse("vision-create-operation", args=["assets"])).content.decode()

        assert 'name="loras" value="style.safetensors"' in body
        assert '<label class="chip-check">' in body
        loras_block = _field_block(body, "loras")
        assert "<select" not in loras_block
        assert loras_block.count('type="checkbox"') == 1

    def test_an_engine_that_cannot_list_assets_never_500s_the_page(self, client):
        _bind()
        with patch.dict(operations._OPERATIONS, {"assets": self.OPERATION}), \
             self._asset_engine(blow_up=True):
            response = client.get(reverse("vision-create-operation", args=["assets"]))

        assert response.status_code == 200
        # NOTE (deviation from the brief's literal string): the brief's Step
        # 9 text asserted `"reports no loras"`, but S4/R2-3 (see the plan's
        # review history) settled on a message that deliberately never
        # claims the engine "reports" anything -- `test_forms.py::
        # TestAssetFields::test_the_message_names_both_causes_and_never_
        # asserts_an_engine_said_so` pins `"reports no" not in help_text`
        # for the exact same field. The brief's literal string for this one
        # assertion is a leftover from before that ruling landed; asserting
        # it verbatim would contradict the form test in the same task. This
        # checks for the actual, ruling-compliant copy instead.
        assert "no loras is available" in response.content.decode().lower()

    def test_an_engine_with_no_list_assets_member_is_not_an_error(self, client):
        """Every optional protocol member is read with `getattr` -- an
        adapter that predates assets must still serve the page."""
        _bind()
        engine = StubEngine()
        engine.list_choices = lambda endpoint, key: ()
        with patch.dict(operations._OPERATIONS, {"assets": self.OPERATION}), \
             patch.dict(ENGINES, {"stubengine": engine}):
            assert client.get(reverse("vision-create-operation", args=["assets"])).status_code == 200


@pytest.mark.django_db
class TestLoraChipGroupStates:
    """T2: the LoRAs field is the platform chip-picker
    (`<label class="chip-check">`), and it carries the SAME three field
    states (enabled/unused/ignored) every other field does -- a chip
    group's checkboxes carry `disabled` exactly the same way a select or
    a text input's would."""

    def test_two_lora_options_render_two_chips_and_no_select(self, client):
        _bind()
        engine = StubEngine()
        engine.list_choices = lambda endpoint, key: ("euler",) if key == "sampler" else ("normal",)
        engine.list_assets = lambda endpoint, kind: (
            [Asset(kind=kind, asset_id=name) for name in ("style.safetensors", "anime.safetensors")]
            if kind == "lora" else []
        )
        with patch.dict(ENGINES, {"stubengine": engine}):
            body = client.get(reverse("vision-create"), {"operation": "txt2img"}).content.decode()

        loras_block = _field_block(body, "loras")
        assert loras_block.count('type="checkbox"') == 2
        assert 'name="loras" value="style.safetensors"' in loras_block
        assert 'name="loras" value="anime.safetensors"' in loras_block
        assert "<select" not in loras_block
        assert "<select multiple" not in body

    def test_the_loras_chip_group_is_unused_and_hidden_where_the_mode_does_not_declare_it(self, client):
        """Upscale declares no `loras` param -- the union still renders
        the wrapper, `unused`, hidden. `services.live_options` (untouched
        by this task) only asks the engine for the PICKED operation's own
        params, so an unused ASSET field's live options are always empty
        and its chip group is an empty group -- there is no checkbox for
        the browser to disable, and nothing to see even with `hidden`
        stripped. `_field_block` keys off `name="..."`, which an EMPTY
        chip group never emits, so this test locates the wrapper by its
        label text instead."""
        _bind()
        engine = StubEngine()
        engine.list_choices = lambda endpoint, key: ()
        engine.list_assets = lambda endpoint, kind: [Asset(kind=kind, asset_id="4x.pth")]
        with patch.dict(ENGINES, {"stubengine": engine}):
            response = client.get(reverse("vision-create"), {"operation": "upscale"})

        form = response.context["form"]
        body = response.content.decode()
        assert form.fields["loras"].state == "unused"
        blocks = re.split(r'(?=<div class="field field--)', body)
        matches = [b for b in blocks if ">LoRAs" in b]
        assert len(matches) == 1
        block = matches[0]
        assert block.startswith(
            '<div class="field field--unused field--kind-asset field--multi" hidden>'
        )
        assert '<div class="chip-picker">' in block
        assert 'type="checkbox"' not in block

    def test_the_loras_chip_group_is_ignored_visible_disabled_with_its_reason(self, client):
        """A model whose graph cannot honour LoRAs: the mode DOES declare
        the field, so it stays visible, dimmed, with its reason -- the
        same rule any other ignored field follows, now proven for the
        chip group."""
        _bind()
        engine = StubEngine()
        engine.list_choices = lambda endpoint, key: ("euler",) if key == "sampler" else ("normal",)
        engine.list_assets = lambda endpoint, kind: (
            [Asset(kind=kind, asset_id="style.safetensors")] if kind == "lora" else []
        )
        engine.ignored_params = lambda operation_key, config=None: (
            {"loras": "This model has no LoRA input."} if operation_key == "txt2img" else {}
        )
        with patch.dict(ENGINES, {"stubengine": engine}):
            response = client.get(reverse("vision-create"), {"operation": "txt2img"})

        form = response.context["form"]
        body = response.content.decode()
        assert form.fields["loras"].state == "ignored"
        block = _field_block(body, "loras")
        assert block.startswith(
            '<div class="field field--ignored field--kind-asset field--multi">'
        )
        assert "hidden" not in block.split(">", 1)[0]
        assert "This model has no LoRA input." in block
        assert re.search(r'type="checkbox"[^>]*\bdisabled\b', block)


@pytest.mark.django_db
class TestUpscalePage:
    def test_upscale_enables_its_own_two_fields_and_disables_the_rest(self, client):
        _bind()
        engine = StubEngine()
        engine.list_choices = lambda endpoint, key: ()
        engine.list_assets = lambda endpoint, kind: [Asset(kind=kind, asset_id="4x.pth")]
        with patch.dict(ENGINES, {"stubengine": engine}):
            response = client.get(reverse("vision-create"), {"operation": "upscale"})

        body = response.content.decode()
        form = response.context["form"]
        assert '<option value="4x.pth"' in body
        assert form.fields["init_image"].state == "enabled"
        assert form.fields["upscale_model"].state == "enabled"
        # Present, disabled, explained -- never absent. This is the whole
        # of R2: the operator can see what upscaling does not ask for.
        assert 'name="prompt"' in body
        assert form.fields["prompt"].state == "unused"
        assert form.fields["sampler"].state == "unused"
        assert "Not used by Upscale." in body


@pytest.mark.django_db
class TestEnginePositionOnTheCard:
    def test_no_line_when_the_refresh_reports_no_position(self, client):
        """A job read straight from the database has no `engine_position` at
        all -- the card must render nothing rather than raise, which is the
        never-500 half of using a transient attribute."""
        job = _done_job()
        job.status = GenerationJob.Status.QUEUED
        job.save(update_fields=["status"])

        def _refreshed(target):
            target.unreachable = False
            target.engine_position = None
            return target

        with patch("tools.vision.views.services.refresh_job", side_effect=_refreshed):
            body = client.get(reverse("vision-job-status", args=[job.id])).content.decode()

        assert "Waiting on the image engine" not in body

    def test_the_position_renders_when_the_refresh_reports_one(self, client):
        job = _done_job()
        job.status = GenerationJob.Status.QUEUED
        job.save(update_fields=["status"])

        def _refreshed(target):
            target.unreachable = False
            target.engine_position = 3
            return target

        with patch("tools.vision.views.services.refresh_job", side_effect=_refreshed):
            body = client.get(reverse("vision-job-status", args=[job.id])).content.decode()

        assert "Waiting on the image engine — position 3." in body

    @override_settings(VISION_STALE_AFTER=timedelta(minutes=10))
    def test_a_known_position_wins_over_the_stale_hint(self, client):
        """A job can be both stale (old, non-terminal) and have a known
        engine position at the same time -- the position, not the "check
        that the engine is running" hint, should render: a live position
        means the engine IS responding."""
        job = _done_job()
        job.status = GenerationJob.Status.QUEUED
        job.created_at = timezone.now() - timedelta(minutes=11)
        job.save(update_fields=["status", "created_at"])
        assert job.is_stale is True

        def _refreshed(target):
            target.unreachable = False
            target.engine_position = 3
            return target

        with patch("tools.vision.views.services.refresh_job", side_effect=_refreshed):
            body = client.get(reverse("vision-job-status", args=[job.id])).content.decode()

        assert "Waiting on the image engine — position 3." in body
        assert "Still queued — check that the image engine is running." not in body


@pytest.mark.django_db
class TestInfoTooltips:
    """R2: the schema's own sentence (`Param.description`) is no longer an
    always-visible line under every field -- it is an ⓘ tooltip, shown on
    hover and on keyboard focus, and always present in the DOM wired with
    `aria-describedby` so a screen reader gets it either way."""

    def test_every_field_carries_an_info_trigger_wired_to_its_tooltip(self, client):
        body = client.get(reverse("vision-create")).content.decode()

        assert '<span class="info" tabindex="0" aria-describedby="id_width-info">' in body
        assert '<span class="tip" id="id_width-info" role="tooltip">' in body

    def test_the_tooltip_is_not_inside_its_label(self, client):
        """A label's accessible name is its text content, so tooltip prose
        living inside `<label>` would be read as part of the field's name
        (peer review). The `.tip` span must be a SIBLING of `<label>`, not
        a descendant of it, even though the ⓘ trigger that describes it
        still is."""
        body = client.get(reverse("vision-create")).content.decode()

        width = next(param for param in TXT2IMG.params if param.key == "width")
        label_start = body.index('for="id_width"')
        label_close = body.index("</label>", label_start)
        label_html = body[label_start:label_close]

        assert 'class="info"' in label_html
        assert 'class="tip"' not in label_html
        assert escape(width.description) not in label_html

        after_label = body[label_close:label_close + 600]
        assert '<span class="tip" id="id_width-info" role="tooltip">' in after_label

    def test_the_schema_sentence_is_in_the_dom_not_only_on_hover(self, client):
        body = client.get(reverse("vision-create")).content.decode()

        width = next(param for param in TXT2IMG.params if param.key == "width")
        assert width.description
        assert escape(width.description) in body

    def test_the_always_visible_hint_line_is_gone(self, client):
        body = client.get(reverse("vision-create")).content.decode()

        assert 'class="muted field-hint"' not in body
        assert ".field-hint {" not in body

    def test_the_tooltip_needs_no_javascript(self, client):
        """Pure CSS: `:hover` for a mouse, `:focus` for a keyboard (the ⓘ
        is `tabindex="0"`). `.tip` sits outside `<label>` now, so `.field`
        -- not `label` -- has to be the selector that reacts to a hovered
        or focused descendant, via `:has()`. The page's script block is
        for polling and the XHR submit, and a hint must not depend on
        either."""
        body = client.get(reverse("vision-create")).content.decode()

        assert ".field:has(label:hover) .tip" in body
        assert ".field:has(.info:focus) .tip" in body

    def test_the_tooltip_stays_open_while_the_pointer_is_over_it(self, client):
        """WCAG 1.4.13 (hoverable): moving the pointer off the ⓘ and onto
        the tip itself -- to read a long sentence or select its text --
        must not dismiss it. `:has(.tip:hover)` keeps `.field` in the
        "show" condition for as long as the pointer is over `.tip`
        itself, not only over the trigger that opened it."""
        body = client.get(reverse("vision-create")).content.decode()

        assert ".field:has(.tip:hover) .tip" in body

    def test_a_disabled_fields_reason_is_in_its_tooltip_and_inline(self, client):
        """The tooltip is a convenience; a state the operator cannot
        change must be legible without hovering anything."""
        response = client.get(reverse("vision-create"), {"operation": "upscale"})
        body = response.content.decode()

        assert '<span class="muted field-reason">Not used by Upscale.</span>' in body
        assert 'role="tooltip">' in body
        assert body.count("Not used by Upscale.") >= 2  # tooltip body + inline line


@pytest.mark.django_db
class TestModelPicker:
    """The per-generation model picker (Task 11) -- the same picker grammar
    the Ask page already uses, from the same console surface
    (`connections_for_picker` / `role_primary`)."""

    def test_the_bound_model_is_the_preselected_primary(self, client):
        connection = ModelConnection.objects.create(
            name="bound", engine="comfyui", endpoint="http://c:8188",
            model_id="b.safetensors", capabilities=["image-generation"],
        )
        RoleBinding.objects.update_or_create(
            role_key="vision.generate", defaults={"connection": connection}
        )

        options = client.get(reverse("vision-create")).context["connection_options"]

        assert options == [
            {"value": str(connection.pk), "label": "bound (primary)", "selected": True}
        ]

    def test_every_registered_image_connection_is_offered_with_its_descriptor(self, client):
        ModelConnection.objects.create(
            name="second", engine="comfyui", endpoint="http://c:8188",
            model_id="s.gguf", capabilities=["image-generation"],
            descriptor="faster, good with text", rank=2,
        )
        ModelConnection.objects.create(
            name="first", engine="comfyui", endpoint="http://c:8188",
            model_id="f.gguf", capabilities=["image-generation"], rank=1,
        )

        options = client.get(reverse("vision-create")).context["connection_options"]

        assert [opt["label"] for opt in options] == [
            "first", "second — faster, good with text",
        ]

    def test_a_chat_only_connection_is_never_offered(self, client):
        ModelConnection.objects.create(
            name="a chat model", engine="ollama", endpoint="http://o:11434",
            model_id="llama", capabilities=["chat"],
        )
        assert client.get(reverse("vision-create")).context["connection_options"] is None

    def test_the_picked_model_drives_the_banner_and_the_select(self, client):
        connection = ModelConnection.objects.create(
            name="picked", engine="stubengine", endpoint="http://stub:9999",
            model_id="w.gguf", capabilities=["image-generation"],
            config={"family": "flux2"},
        )

        class _Narrow(StubEngine):
            def supported_operations(self, model_id, endpoint, family=""):
                return ("edit",)

        with patch.dict(ENGINES, {"stubengine": _Narrow()}, clear=True):
            response = client.get(
                reverse("vision-create"), {"connection": str(connection.pk)}
            )

        assert [op.key for op in response.context["operations"]] == ["edit"]
        assert response.context["operation"].key == "edit"
        assert response.context["preflight"].resolved.model_id == "w.gguf"
        assert response.context["selected_connection"] == str(connection.pk)
        assert [
            state.operation.key for state in response.context["operation_states"] if state.supported
        ] == ["edit"]

    def test_a_url_naming_a_mode_the_picked_model_cannot_run_falls_back(self, client):
        """Switching models must not 404 the page an operator is standing
        on -- the mode went away, the platform did not."""
        connection = ModelConnection.objects.create(
            name="picked", engine="stubengine", endpoint="http://stub:9999",
            model_id="w.gguf", capabilities=["image-generation"],
            config={"family": "flux2"},
        )

        class _Narrow(StubEngine):
            def supported_operations(self, model_id, endpoint, family=""):
                return ("edit",)

        with patch.dict(ENGINES, {"stubengine": _Narrow()}, clear=True):
            response = client.get(
                reverse("vision-create-operation", args=["inpaint"]),
                {"connection": str(connection.pk)},
            )

        assert response.status_code == 200
        assert response.context["operation"].key == "edit"

    def test_an_unregistered_mode_is_still_a_404(self, client):
        assert client.get("/vision/not-a-mode/").status_code == 404

    def test_switching_models_cannot_cost_the_operator_their_work(self, client):
        """The picker is its OWN GET form, not a `formmethod="get"` button
        inside the multipart generate form: submitting that form as a GET
        would drop the attached image and the typed instruction, because the
        create page's GET handler reads only `?connection=`. The generate
        form learns the choice from a hidden field instead."""
        ModelConnection.objects.create(
            name="picked", engine="comfyui", endpoint="http://c:8188",
            model_id="w.safetensors", capabilities=["image-generation"],
        )
        body = client.get(reverse("vision-create")).content.decode()

        assert 'formmethod="get"' not in body
        assert '<form class="model-picker" method="get"' in body
        assert '<input type="hidden" name="connection"' in body

    def test_the_picked_model_travels_on_the_page(self, client):
        """The pick must reach the generate POST. The chooser nav that
        used to carry it in an href is gone (ADR 0012 D-EDIT-13); the
        hidden field the generate form has always carried is what makes
        the picker's GET and the submission agree."""
        connection = ModelConnection.objects.create(
            name="picked", engine="comfyui", endpoint="http://c:8188",
            model_id="w.safetensors", capabilities=["image-generation"],
        )
        body = client.get(
            reverse("vision-create"), {"connection": str(connection.pk)}
        ).content.decode()

        assert f'<input type="hidden" name="connection" value="{connection.pk}">' in body
        assert f'<option value="{connection.pk}" selected>' in body

    def test_the_form_starts_at_the_picked_models_own_defaults(self, client):
        """Switching to a distilled model changes what the Steps field says
        before the operator types anything -- the whole point of the seam
        (ADR 0012 D-EDIT-7)."""
        connection = ModelConnection.objects.create(
            name="distilled edit model", engine="comfyui", endpoint="http://c:8188",
            model_id="weights-Q8_0.gguf", capabilities=["image-generation"],
            config={"family": "flux2", "variant": "distilled"},
        )
        response = client.get(f"{reverse('vision-create')}?connection={connection.pk}")
        assert response.context["form"].fields["steps"].initial == 4


@pytest.mark.django_db
class TestCarryTypedValuesAcrossAModeSwitch:
    """brief-cancel-carry.md Part B -- owner: "when i click on buttons it
    refreshes the page and clears current inputs." `?reuse=<job-id>`
    (`TestReuseResolvesOperation`, above) is a DIFFERENT mechanism -- it
    names a job whose stored params are read back from the database, not
    a per-field GET key. This is the new one: a direct `<key>=<value>`
    GET param, lowest precedence, only for a key the union of every
    registered operation's params actually declares."""

    def test_get_params_named_after_union_keys_prefill_the_form(self, client):
        response = client.get(
            reverse("vision-create"), {"operation": "edit", "prompt": "hello", "steps": "7"}
        )
        form = response.context["form"]
        # "prompt" is not one of `edit`'s own params (it declares
        # `instruction` instead) but IS in the union (txt2img declares
        # it) -- the union form always has a field for it, unused but
        # present, and a carried value still prefills it.
        assert form.fields["prompt"].initial == "hello"
        assert form.fields["steps"].initial == "7"

    def test_a_key_that_names_no_union_param_is_ignored(self, client):
        response = client.get(reverse("vision-create"), {"operation": "edit", "bogus": "1"})
        body = response.content.decode()
        assert response.status_code == 200
        assert "bogus" not in body

    def test_a_file_params_key_in_get_is_ignored(self, client):
        """A query string cannot carry a file's bytes -- `init_image` (a
        file param on every checkpoint-based mode) must never be read
        back as `initial`, which would try to prefill a `FileField` with
        a plain string."""
        response = client.get(
            reverse("vision-create"), {"operation": "txt2img", "init_image": "not-a-file.png"}
        )
        assert response.context["form"].fields["init_image"].initial is None

    def test_a_multi_valued_params_key_in_get_is_ignored(self, client):
        """adversarial review finding 2 (LOW): a `?loras=x` GET param must
        never become `initial` for the LoRAs chip group, matching the
        carry-forward SCRIPT's own refusal to carry a multi-valued field
        (create.html -- it explicitly skips a name `FormData` yields more
        than once). A field whose real answer is a SET cannot be
        represented by one bare `key=value` pair without misrepresenting
        what was actually selected."""
        response = client.get(
            reverse("vision-create"), {"operation": "txt2img", "loras": "style.safetensors"}
        )
        assert response.context["form"].fields["loras"].initial is None

    def test_a_reused_jobs_value_still_wins_over_a_carried_get_param(self, client):
        """Precedence: `?reuse=` (a job's own stored params) beats a bare
        carried GET key on the same field -- the carry mechanism is the
        LOWEST-precedence source, exactly as the brief specifies."""
        job = GenerationJob.objects.create(
            operation="txt2img", params={"prompt": "from the reused job", "seed": 42},
            seed=42, engine="stubengine", model_id="stub.safetensors", model_fingerprint="f",
        )
        response = client.get(
            reverse("vision-create"),
            {"operation": "txt2img", "reuse": str(job.id), "prompt": "typed before switching"},
        )
        assert response.context["form"].fields["prompt"].initial == "from the reused job"

    def test_the_picker_form_carries_existing_stored_input_params(self, client, tmp_path):
        """The live bug this task also fixes: the picker is its own GET
        form with no control for `input_*` at all, so switching Model/Mode
        used to drop a gallery hand-off silently. Server-rendered, so this
        works with no JS."""
        _bind()
        output = stored_output(tmp_path)
        url = (
            reverse("vision-create-operation", args=["img2img"])
            + f"?input_init_image=output:{output.id}"
        )
        with patch.dict(
            operations._OPERATIONS, {"txt2img": operations.TXT2IMG, "img2img": operations.IMG2IMG}
        ), _engine():
            body = client.get(url).content.decode()

        picker_start = body.index('<form class="model-picker"')
        picker_end = body.index("</form>", picker_start)
        picker_block = body[picker_start:picker_end]
        assert f'<input type="hidden" name="input_init_image" value="output:{output.id}">' in picker_block

    def test_the_script_carries_typed_values_into_hidden_picker_fields(self, client):
        """String-level only, per the brief -- no JS execution here."""
        _bind()
        with _engine():
            body = client.get(reverse("vision-create")).content.decode()

        assert "pickerForm" in body
        assert "createElement('input')" in body
        assert "hidden.type = 'hidden'" in body
        assert "requestSubmit" in body


@pytest.mark.django_db
class TestQwenImageFamilyPageAndPicker:
    """Finding 11: every page/picker-level test above (`TestImageToImageMerge`
    and friends) exercises `flux2` -- proving the SAME behaviour for
    `qwen_image` (D-EDIT-5's second family) closes the gap that a
    flux2-only suite could hide a special case hardcoded to one family's
    name rather than reading the connection's own declaration."""

    def test_a_qwen_image_pick_selects_edit_and_disables_every_other_mode(self, client):
        connection = ModelConnection.objects.create(
            name="qwen pick", engine="stubengine", endpoint="http://stub:9999",
            model_id="qwen-edit.gguf", capabilities=["image-generation"],
            config={"family": "qwen_image", "text_encoder": "enc.gguf", "vae": "vae.safetensors"},
        )

        class _EditOnlyForQwen(StubEngine):
            def supported_operations(self, model_id, endpoint, family=""):
                if family == "qwen_image":
                    return ("edit",)
                return ("txt2img", "img2img", "inpaint", "upscale")

        with patch.dict(ENGINES, {"stubengine": _EditOnlyForQwen()}, clear=True):
            response = client.get(
                reverse("vision-create"), {"connection": str(connection.pk)}
            )

        body = response.content.decode()
        # Its own schema label, no longer merged into "Image to image".
        assert "Edit an image" in body
        assert 'value="edit" selected' in body
        assert "<h1>Image generation</h1>" in body
        for key in ("txt2img", "img2img", "inpaint", "upscale"):
            assert f'value="{key}" disabled' in body
        assert "No image to image graph for the qwen_image family." in body
        # `edit`'s own params are the enabled ones; the checkpoint mode's
        # are rendered and disabled.
        form = response.context["form"]
        assert form.fields["instruction"].state == "enabled"
        assert form.fields["guidance"].state == "enabled"
        assert form.fields["denoise"].state == "unused"
        assert [op.key for op in response.context["operations"]] == ["edit"]


@pytest.mark.django_db
class TestOperationsSupportsNothingBanner:
    """Owner ruling 2026-08-24(c): a model whose engine reports no operations
    for its declared family gets an honest banner and a disabled form
    (Task 10's handoff -- `views.resolve_page_operation`'s own comment)."""

    def test_a_model_that_supports_nothing_shows_the_banner_and_disables_the_form(self, client):
        connection = ModelConnection.objects.create(
            name="picked", engine="stubengine", endpoint="http://stub:9999",
            model_id="w.gguf", capabilities=["image-generation"],
            config={"family": "unknown_family"},
        )

        class _Nothing(StubEngine):
            def supported_operations(self, model_id, endpoint, family=""):
                return ()

        with patch.dict(ENGINES, {"stubengine": _Nothing()}, clear=True):
            response = client.get(
                reverse("vision-create"), {"connection": str(connection.pk)}
            )

        body = response.content.decode()
        assert response.context["operations_supported"] is False
        assert "no operations" in body
        assert "w.gguf" in body
        assert "<fieldset disabled>" in body
        # Every option disabled, each with its own reason -- the banner
        # says the model can run nothing, the select says which nothings.
        for key in ("txt2img", "img2img", "inpaint", "upscale", "edit"):
            assert f'value="{key}" disabled' in body
        assert "unknown_family" in body

    def test_a_model_that_supports_something_leaves_the_form_enabled(self, client):
        connection = ModelConnection.objects.create(
            name="picked", engine="stubengine", endpoint="http://stub:9999",
            model_id="w.gguf", capabilities=["image-generation"],
            config={"family": "flux2"},
        )

        class _EditOnly(StubEngine):
            def supported_operations(self, model_id, endpoint, family=""):
                return ("edit",)

        with patch.dict(ENGINES, {"stubengine": _EditOnly()}, clear=True):
            response = client.get(
                reverse("vision-create"), {"connection": str(connection.pk)}
            )

        body = response.content.decode()
        assert response.context["operations_supported"] is True
        assert "<fieldset disabled>" not in body

    def test_unbound_is_not_mistaken_for_supports_nothing(self, client):
        """No opinion (nothing selected, nothing bound) must not trip the
        supports-nothing banner -- that banner is a fact about a SPECIFIC
        model's engine, and an unbound role already shows its own honest
        banner."""
        body = client.get(reverse("vision-create")).content.decode()

        assert "no operations" not in body
        assert "<fieldset disabled>" not in body


@pytest.mark.django_db
class TestConstantForm:
    """ADR 0012 D-EDIT-13: the page renders the UNION of every
    registered operation's params, always, whichever mode is picked."""

    UNION = [
        "prompt", "negative_prompt", "width", "height", "steps", "cfg_scale",
        "seed", "sampler", "scheduler", "batch_size", "loras", "lora_strength",
        "init_image", "denoise", "mask_image", "mask_grow", "upscale_model",
        "instruction", "reference_image", "guidance",
    ]

    def test_every_mode_renders_all_twenty_fields_in_the_same_order(self, client):
        _bind()
        for key in ("txt2img", "img2img", "inpaint", "upscale", "edit"):
            with _engine(choices={"sampler": ("euler",), "scheduler": ("normal",)}):
                response = client.get(reverse("vision-create"), {"operation": key})
            assert list(response.context["form"].fields) == self.UNION, key

    def test_the_picked_operations_own_param_supplies_an_enabled_fields_default(self, client):
        _bind()
        with _engine():
            txt2img = client.get(reverse("vision-create"), {"operation": "txt2img"})
            edit = client.get(reverse("vision-create"), {"operation": "edit"})

        assert txt2img.context["form"].fields["steps"].initial == 25
        assert edit.context["form"].fields["steps"].initial == 20

    def test_a_reused_jobs_settings_still_win_over_the_models_defaults(self, client):
        """ADR 0012 D-EDIT-7, unchanged by R2: a reused job's own
        settings are a stronger statement than a model's defaults."""
        job = _done_job(prompt="a lighthouse", seed=7)
        _bind()
        with _engine():
            response = client.get(reverse("vision-create"), {"reuse": str(job.id)})

        assert response.context["form"].fields["prompt"].initial == "a lighthouse"


@pytest.mark.django_db
class TestFieldStates:
    """Three states, all rendered: enabled, unused (this mode does not
    declare it), ignored (this model's graph cannot honour it)."""

    IGNORES = {
        "negative_prompt": "This model has no negative-prompt input; describe what you want in the prompt instead.",
        "scheduler": "This model's own noise schedule has no separate setting to choose.",
    }

    def _flux2(self):
        connection = ModelConnection.objects.create(
            name="klein", engine="stubengine", endpoint="http://stub:9999",
            model_id="klein.gguf", capabilities=["image-generation"],
            config={"family": "flux2"},
        )

        class _Flux2(StubEngine):
            def supported_operations(self, model_id, endpoint, family=""):
                return ("txt2img", "edit")

            def ignored_params(self, operation_key, config=None):
                return dict(TestFieldStates.IGNORES) if operation_key == "txt2img" else {}

            def list_choices(self, endpoint, key):
                return {"sampler": ("euler",), "scheduler": ("normal", "karras")}.get(key, ())

        return connection, patch.dict(ENGINES, {"stubengine": _Flux2()}, clear=True)

    def test_a_model_that_cannot_honour_a_param_disables_it_and_says_why(self, client):
        connection, engine = self._flux2()
        with engine:
            response = client.get(
                reverse("vision-create"),
                {"connection": str(connection.pk), "operation": "txt2img"},
            )

        body = response.content.decode()
        form = response.context["form"]
        assert form.fields["negative_prompt"].state == "ignored"
        assert form.fields["scheduler"].state == "ignored"
        assert form.fields["prompt"].state == "enabled"
        assert form.fields["width"].state == "enabled"
        assert escape(self.IGNORES["scheduler"]) in body
        assert 'class="field field--ignored field--kind-choice"' in body

    def test_an_ignored_choice_field_still_shows_the_engines_real_options(self, client):
        connection, engine = self._flux2()
        with engine:
            body = client.get(
                reverse("vision-create"),
                {"connection": str(connection.pk), "operation": "txt2img"},
            ).content.decode()

        assert '<option value="karras"' in body

    def test_a_param_this_mode_does_not_declare_says_so_by_name(self, client):
        connection, engine = self._flux2()
        with engine:
            response = client.get(
                reverse("vision-create"),
                {"connection": str(connection.pk), "operation": "edit"},
            )

        form = response.context["form"]
        assert form.fields["width"].state == "unused"
        assert form.fields["height"].state == "unused"
        assert form.fields["width"].reason == "Not used by Edit an image."
        assert "Not used by Edit an image." in response.content.decode()

    def test_a_checkpoint_model_enables_every_txt2img_field(self, client):
        connection = ModelConnection.objects.create(
            name="sdxl", engine="stubengine", endpoint="http://stub:9999",
            model_id="sdxl.safetensors", capabilities=["image-generation"],
        )

        class _Checkpoint(StubEngine):
            def supported_operations(self, model_id, endpoint, family=""):
                return ("txt2img", "img2img", "inpaint", "upscale")

            def list_choices(self, endpoint, key):
                return ("euler",) if key == "sampler" else ("normal",)

        with patch.dict(ENGINES, {"stubengine": _Checkpoint()}, clear=True):
            response = client.get(
                reverse("vision-create"), {"connection": str(connection.pk)}
            )

        form = response.context["form"]
        for key in TestConstantForm.UNION[:12]:
            assert form.fields[key].state == "enabled", key
        assert "no separate setting to choose" not in response.content.decode()

    def test_a_disabled_field_is_not_submittable_and_blocks_nothing(self, client):
        connection, engine = self._flux2()
        with engine:
            form = client.get(
                reverse("vision-create"),
                {"connection": str(connection.pk), "operation": "txt2img"},
            ).context["form"]

        for key in ("scheduler", "init_image", "instruction"):
            assert form.fields[key].widget.attrs["disabled"] is True
            assert form.fields[key].required is False

    def test_unused_fields_render_hidden_but_stay_in_the_dom(self, client):
        """The mode does not declare these; they are not explained, they
        are hidden -- but the form stays the union (D-EDIT-13), so the
        wrapper, label, widget, and `disabled` all remain in the HTML."""
        connection, engine = self._flux2()
        with engine:
            response = client.get(
                reverse("vision-create"),
                {"connection": str(connection.pk), "operation": "txt2img"},
            )

        form = response.context["form"]
        body = response.content.decode()
        kinds = {"instruction": "text", "init_image": "file", "mask_image": "file"}
        for key in ("instruction", "init_image", "mask_image"):
            assert form.fields[key].state == "unused", key
            block = _field_block(body, key)
            assert block.startswith(
                f'<div class="field field--unused field--kind-{kinds[key]}" hidden>'
            ), key
            assert f'<label for="id_{key}"' in block, key
            assert "disabled" in block, key
            assert len(re.findall(rf'name="{key}"', block)) == 1, key

    def test_an_ignored_field_is_not_hidden_and_keeps_its_reason(self, client):
        """`ignored` differs from `unused`: the mode DOES want this field,
        the picked model just can't honour it, so it stays visible with
        its reason (ADR 0012 D-EDIT-13)."""
        connection, engine = self._flux2()
        with engine:
            response = client.get(
                reverse("vision-create"),
                {"connection": str(connection.pk), "operation": "txt2img"},
            )

        form = response.context["form"]
        body = response.content.decode()
        assert form.fields["scheduler"].state == "ignored"
        block = _field_block(body, "scheduler")
        assert block.startswith('<div class="field field--ignored field--kind-choice">')
        assert "hidden" not in block.split(">", 1)[0]
        assert escape(self.IGNORES["scheduler"]) in block

    def test_an_enabled_field_is_not_hidden(self, client):
        connection, engine = self._flux2()
        with engine:
            response = client.get(
                reverse("vision-create"),
                {"connection": str(connection.pk), "operation": "txt2img"},
            )

        form = response.context["form"]
        body = response.content.decode()
        assert form.fields["prompt"].state == "enabled"
        block = _field_block(body, "prompt")
        assert block.startswith('<div class="field field--enabled field--kind-text">')
        assert "hidden" not in block.split(">", 1)[0]

    def test_switching_the_mode_reveals_the_field_it_now_declares(self, client):
        """Proves the hiding is mode-driven, not baked at first render:
        `instruction` is unused (hidden) under txt2img and enabled
        (visible) under edit, on the same model, same GET-rendered page."""
        connection, engine = self._flux2()
        with engine:
            txt2img = client.get(
                reverse("vision-create"),
                {"connection": str(connection.pk), "operation": "txt2img"},
            ).content.decode()
            edit = client.get(
                reverse("vision-create"),
                {"connection": str(connection.pk), "operation": "edit"},
            ).content.decode()

        assert _field_block(txt2img, "instruction").startswith(
            '<div class="field field--unused field--kind-text" hidden>'
        )
        edit_block = _field_block(edit, "instruction")
        assert edit_block.startswith('<div class="field field--enabled field--kind-text">')
        assert "hidden" not in edit_block.split(">", 1)[0]


@pytest.mark.django_db
class TestKindAwareGrid:
    """T1: `field--kind-{{ f.field.kind }}` (and `field--multi`) on the
    `.field` wrapper is how create.html's CSS gives a file input or a
    chip group the full grid row instead of one auto-fit column -- both
    truncate illegibly otherwise (the owner's screenshot: a file input
    reading "Choose File I…3", a raw multi-select clipping filenames)."""

    def test_a_file_field_carries_the_kind_file_wrapper_class(self, client):
        _bind()
        with _engine():
            body = client.get(reverse("vision-create"), {"operation": "img2img"}).content.decode()

        block = _field_block(body, "init_image")
        assert 'field--kind-file' in block.split(">", 1)[0]

    def test_the_full_row_css_rule_targets_kind_file_and_multi_only(self, client):
        """Representable at the class level (per T1's judgment call: a
        single-choice asset select stays in the grid, only a file field
        or a chip group takes the whole row) -- CSS itself is out of
        scope for a Python assertion, this pins the SELECTOR."""
        _bind()
        with _engine():
            body = client.get(reverse("vision-create")).content.decode()

        assert ".gen-form .field--kind-file, .gen-form .field--multi { grid-column: 1 / -1; }" in body


@pytest.mark.django_db
class TestChosenFilePreviewScript:
    """T3: the progressive-enhancement script that previews a chosen file
    -- string assertions only, per the brief (no JS execution here)."""

    def test_the_preview_script_is_present_exactly_once_and_targets_file_inputs(self, client):
        _bind()
        with _engine():
            body = client.get(reverse("vision-create")).content.decode()

        assert body.count("createObjectURL") == 1
        assert "'.gen-form input[type=file]'" in body
        assert "file-preview" in body

    def test_the_preview_script_lives_inside_the_generate_form(self, client):
        """Placed at the end of the form block (brief T3), not in the
        page's OTHER script (`{% block scripts %}`, polling/XHR-submit) --
        both must be present, and distinct."""
        _bind()
        with _engine():
            body = client.get(reverse("vision-create")).content.decode()

        form_start = body.index('id="generate-form"')
        form_end = body.index("</form>", form_start)
        assert "createObjectURL" in body[form_start:form_end]
        # The page's other script (the job-card poller) is still present
        # and is a SEPARATE `<script>` block. THREE since C-16: the third
        # is `foundation/templates/_poller.html`, the shared
        # `pollUntilTerminal` loop this page now includes instead of
        # rolling its own.
        assert "data-job-poll" in body
        assert body.count("<script>") == 3

    def test_the_preview_script_also_builds_a_remove_control(self, client):
        """brief-remove.md: a Remove/x control beside the preview, so a
        chosen file can be unselected -- the native input can only replace,
        never clear, without this. String-level only, per the brief; the
        three tokens plus "still exactly one inline script" is the whole
        contract here."""
        _bind()
        with _engine():
            body = client.get(reverse("vision-create")).content.decode()

        form_start = body.index('id="generate-form"')
        form_end = body.index("</form>", form_start)
        script_slice = body[form_start:form_end]

        assert "file-clear" in script_slice
        assert 'type="button"' in script_slice
        assert "input.value = ''" in script_slice
        # Still exactly ONE inline script in the form block -- the Remove
        # control extends the existing preview script, it does not add a
        # second one.
        assert script_slice.count("<script>") == 1


class TestTheCreatePageRenderGatesTheForm:
    def test_a_non_holder_sees_no_generation_form_and_a_holder_does(self, client):
        """THE SAME PREDICATE the POST enforces (`_may_generate`), never a
        second truth -- a page that renders a control whose POST answers
        403 is a page that lies to the person reading it."""
        from agents.models import ToolEntitlement
        legal = make_entitlement(name="Legal")
        ToolEntitlement.objects.create(tool_key="vision.generate", entitlement=legal)
        holder = make_user()
        grant(legal, user=holder)
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, make_user())
            body = client.get(reverse("vision-create")).content
            assert b'id="generate-form"' not in body
            assert b"entitlement" in body.lower()
            other = client.__class__()
            sign_in(other, holder)
            assert b'id="generate-form"' in other.get(reverse("vision-create")).content

    def test_the_gallery_and_the_recent_list_still_render_for_a_non_holder(self, client):
        """The page is class A and it is not only a form: somebody who may
        not generate may still READ what they already generated. Hiding
        the whole page would be a refusal the route does not make.

        T13 review finding 3: asserting only 200/no-traceback proves the
        page did not 500 or 403 -- it does not prove Recent (or the form)
        is actually there or actually gone. This pins both halves of the
        class-A property directly."""
        from agents.models import ToolEntitlement
        ToolEntitlement.objects.create(tool_key="vision.generate",
                                       entitlement=make_entitlement(name="Legal"))
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, make_user())
            response = client.get(reverse("vision-create"))
        assert response.status_code == 200
        assert b"Traceback" not in response.content
        assert b"<h2>Recent</h2>" in response.content
        assert b'id="generate-form"' not in response.content

    def test_an_open_box_renders_the_form_exactly_as_today(self, client):
        from agents.models import ToolEntitlement
        ToolEntitlement.objects.create(tool_key="vision.generate",
                                       entitlement=make_entitlement(name="Legal"))
        assert b'id="generate-form"' in client.get(reverse("vision-create")).content
