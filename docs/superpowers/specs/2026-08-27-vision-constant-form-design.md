# Vision R2 — one constant input form — design spec

**Date:** 2026-08-27
**Status:** Design, not built. Written against branch `vision-r2-constant-form` (worktree
`.claude/worktrees/vision-generation`), on top of R1 (ADR 0012 D-EDIT-12).
**Owner requirement:** `.superpowers/owner-requirements/vision-full-onboarding-and-constant-form.md` §R2.
**Lands as:** ADR 0012 decision D-EDIT-13, one PR against `tools/vision/` + two engine members.

---

## 1. Context, and the gap

`/vision/` narrows itself three ways today, and the owner read the narrowing as lost function:

| Narrowing | Where | Effect |
|---|---|---|
| Only the picked operation's params are rendered | `views.CreatePageView.get_context_data` (`views.py:491-507`) → `forms.build_form` (`forms.py:166`) | Picking the distilled build (family `flux2`) hides Width/Height/Negative prompt/Sampler/Scheduler the moment the mode is `edit` |
| Modes the model can't run vanish from the nav | `views.page_operations` (`views.py:43-61`) → `services.operations_for_model` (`services.py:257-302`), rendered by `views.page_tabs` (`views.py:174-184`) | No trace that `inpaint` exists at all, and no reason given |
| `img2img` + `edit` collapse to one tab | `_MERGED_OPERATION_KEYS`/`_IMAGE_TO_IMAGE_LABEL`/`_merge_image_to_image`/`_display_label` (`views.py:121-184`) | Two real operations wear one name; the nav hides entirely while one mode is on offer (`create.html:41`) |

R1 left the missing fact unconsumed: `flux2_txt2img.IGNORES` (`flux2_txt2img.py:70-76`) names
`negative_prompt` and `scheduler` as params the `flux2` graphs cannot honour, readable through
`comfyui_workflows.ignored_params` (`__init__.py:207-213`). ADR 0012 D-EDIT-12 says in as many
words that "the console disabling those two fields … is deferred to the constant-form work". This
is that work. `Param.description` (`operations.py:41-76`) is likewise already on every param,
rendered as an always-visible `.field-hint` (`forms.py:136-137`, `create.html:26-28, 164, 175`);
R2 moves that same text into a hover/focus tooltip — one copy, a different place.

---

## 2. Decisions

**D1 — the page renders the UNION of every registered operation's params, always.**
The header row carries the existing Model picker plus a new Operation `<select>` listing every
registered `image-generation` operation. Below it, one fixed field list: the union of
`operations_for(IMAGE_GENERATION_CAPABILITY)` params, de-duplicated by key, first declaration
wins, in registration order (`apps.py`: `txt2img, img2img, inpaint, upscale, edit`). The union is
computed by a new `services.union_params`, which is the same computation the peer's P1
`tools/vision/tools.py::build_generate_spec` performs — one function once P1 merges (§11). Here in
the console the file params are real `<input type="file">` fields, never references.
Deleted: `_MERGED_OPERATION_KEYS`, `_IMAGE_TO_IMAGE_LABEL`, `_merge_image_to_image`,
`_display_label`, `page_tabs` (`views.py:121-184`), the tab nav and its `.op-chooser` CSS
(`create.html:13-15, 41-48`). `edit` and `img2img` are two operations again, named by their own
schema labels.

**D2 — engine facts reach the page through engine optional members and `services`, never a
`comfyui_workflows` import.** `tools/vision` may import `models.contracts.*` pure leaves and
`models.registry.bindings`; it must not learn which engine is bound (`views.py:1-9`). So the
"this model ignores that param" fact travels: template `IGNORES` → `comfyui_workflows.ignored_params`
→ new optional member `ComfyUIEngine.ignored_params` → new `services.live_ignored`, the exact twin
of `live_defaults`/`param_defaults` (`services.py:223-254`, `comfyui.py:668-682`) — same shape,
same `getattr`-and-never-raise tolerance, same drop-undeclared-keys rule.

**D3 — three field states, every one of them rendered.** A field is `enabled`,
`unused` (the picked operation does not declare it), or `ignored` (declared, but this model's graph
cannot honour it). `unused` and `ignored` fields carry HTML `disabled`, so the browser does not
submit them, and `required=False`, so nothing blocks the form.

**D4 — the POST path is unchanged.** `generate` (`views.py:663-789`) keeps building a bound form
over the PICKED operation only (`build_form_for` → `forms.build_form`, `views.py:1039-1050`) and
keeps `validate_params` as the schema floor (`views.py:737`). One new call sits between them:
`services.fill_engine_blanks`, which back-fills ignored `choice` params the disabled widget never
submitted. The union form is a RENDERING object; it never validates a submission.

**D5 — the catalog is the one source.** `GET /vision/operations/?connection=` gains per-param
`"ignored"` and per-operation `"supported"`/`"unsupported_reason"`, and now lists every registered
operation rather than only the supported ones — the same honesty the page shows.

Untouched: `models/contracts/operations.py`, `agents/**`, `tools/rag/**`, `models/registry/**`,
`models/queue/**`, `config/**`, `pytest.ini`. Out of scope: JS live toggling without reload,
rebinding the baseline-checkpoint default, any `Param` field addition.

---

## 3. The field-state algorithm

Inputs: `operations` (registered, in order), `operation` (picked), `resolved` (picked model or the
role binding, or `None`), `ignored = services.live_ignored(operation, resolved)`.

The union defines the SET and the ORDER; the picked operation's own `Param` defines each ENABLED
field's rendering rule. This matters: `steps` is `default=25` in `SAMPLING_PARAMS`
(`operations.py:396`) and `default=20` in `EDIT` (`operations.py:630`); `denoise` is `0.6` for
`img2img` and `1.0` for `inpaint`. A field the picked operation declares is built from THAT
`Param`; a `unused` field is built from the union's canonical (first-declaring) `Param`.

| Key order (20 params) | First declared by |
|---|---|
| `prompt`, `negative_prompt`, `width`, `height`, `steps`, `cfg_scale`, `seed`, `sampler`, `scheduler`, `batch_size`, `loras`, `lora_strength` | `TXT2IMG` |
| `init_image`, `denoise` | `IMG2IMG` |
| `mask_image`, `mask_grow` | `INPAINT` |
| `upscale_model` | `UPSCALE` |
| `instruction`, `reference_image`, `guidance` | `EDIT` |

Per key:

1. not in `{p.key for p in operation.params}` → `state="unused"`, `reason=f"Not used by {operation.label}."`
2. in `ignored` → `state="ignored"`, `reason=ignored[key]` (the template's own sentence).
3. otherwise → `state="enabled"`, `reason=""`.

`live_options`/`live_defaults` are asked for the PICKED operation only, exactly as today
(`views.py:493, 498`). An `ignored` field therefore still renders its real option list (a disabled
`<select>` of the engine's schedulers); an `unused` `choice` field has no options and degrades to
the disabled free-text input `_base_field_for` already produces (`forms.py:61-69`) — honest,
because it is not submittable either way.

**The Operation select.** Every registered operation is an `<option>`. Those absent from
`services.operations_for_model(resolved)` are `disabled` and carry their reason in the option label
suffix and in the select's tooltip: `services.operation_states(resolved)` returns
`(operation, supported, reason)` per operation from ONE `operations_for_model` call, wording an
unsupported one as `f"No {operation.label.lower()} graph for the {family} family."` when the
connection declares a family (`config_family`, `services.py:290-296`) and
`f"This model's engine has no {operation.label.lower()} graph."` when it does not.

**Edge cases.**

| Situation | Page |
|---|---|
| No connection picked, nothing bound (`resolved is None`) | `operations_for_model` returns the full registry (`services.py:279-281`): every option enabled, no `ignored` (`live_ignored` returns `{}`), form fully rendered |
| Picked pk no longer resolves, on a GET | Falls back to the role binding in silence, as today (`picked_connection`, `views.py:861-896`) |
| `unbound` / `unreachable` preflight | Existing banner unchanged (`create.html:50-65`); the whole form renders and stays enabled — nothing was learned about the operation |
| Engine reports supports-nothing (`available == []`) | Existing honest banner (`create.html:66-76`) plus the whole form disabled by the existing `<fieldset disabled>` (`create.html:158`); every option in the Operation select is disabled with its reason |
| A URL/`?operation=` key that is registered but unsupported | `resolve_page_operation` keeps today's fallback to the picked model's first supported operation (`views.py:64-105`); the select shows the named one disabled with its reason, so the substitution is visible rather than silent |
| A key that is not registered at all | `Http404`, unchanged (`views.py:102`) |

---

## 4. Rendering

**URL scheme — decision: `?operation=<key>` on `/vision/`, with `op/<key>/` kept as a rendering
alias.** The Operation select must be a control inside the picker's existing GET form
(`create.html:96-106`) so that changing it reloads with no JS — and a GET form can only produce a
query parameter, never a path segment. So the picker form gains `<select name="operation">`, its
`action` becomes `{% url 'vision-create' %}`, and every link the page generates uses
`?operation=&connection=`. The `op/<slug:operation_key>/` route (`urls.py`) stays mapped to
`CreatePageView` and renders identically (no redirect, no extra round trip), so bookmarks and any
external link keep working; `CreatePageView` reads `kwargs["operation_key"]` first, then
`?operation=`. `_create_url` (`views.py:792-810`) emits the query form. `?reuse=`, `?queued=`, and
`input_<key>=` prefill are unaffected — they are already query keys read off the same request
(`views.py:367, 399, 552`). `input_targets` (`views.py:512-538`) stops merging, keeps narrowing to
SUPPORTED operations (a gallery link that lands on a disabled operation is a dead end), and returns
`url = "/vision/?operation=<key>"`; `_output_actions.html` changes its `?input_` join to `&input_`.

**Template.** `create.html` renders one loop over the union form and defers each field to a new
`vision/_field.html` partial:

```
<div class="field field--{{ f.field.state }}">
  <label for="{{ f.id_for_label }}">{{ f.label }}
    <span class="info" tabindex="0" aria-describedby="{{ f.auto_id }}-info">ⓘ</span>
    <span class="tip" id="{{ f.auto_id }}-info" role="tooltip">{{ f.help_text }}{% if f.field.reason %} {{ f.field.reason }}{% endif %}</span>
  </label>
  {{ f }}
  {% if f.field.reason %}<span class="muted field-reason">{{ f.field.reason }}</span>{% endif %}
  {{ f.errors }}
</div>
```

`.field-hint` (`create.html:26-28`) is deleted: the always-visible sentence becomes the tooltip
body. Situational copy (the seed hint, `forms.py:58`; the empty-asset explanation,
`forms.py:94-101`) still wins over `Param.description` at `_field_for` (`forms.py:136-137`) and so
becomes that field's tooltip body — one copy, still. Tooltip CSS is pure `:hover`/`:focus-within`
on the `.info` span, positioned absolutely, using only `_shell.html`'s existing tokens (`--panel`,
`--border`, `--text`, `--muted`, `--accent`); the description stays in the DOM and is wired with
`aria-describedby`, so a screen reader gets it whether or not it is visible. No new JavaScript:
`create.html`'s existing block (`:197-280`) keeps the job polling, the XHR submit, and the
`data-reload-on-change` picker handler — which now submits the picker form for either select.
`<h1>` becomes the constant `Image generation`; `page_title` and `_display_label` go with it, the
operation being named by the select.

---

## 5. POST path

`generate` (`views.py:663-789`), in order, with one insertion:

1. `picked_connection` → `_picker_failure_response` when unusable (unchanged).
2. `preflight`, `resolve_page_operation(request.POST["operation"], …)` (unchanged — the hidden
   `operation` field, `create.html:142`, stays).
3. `build_form_for` over the PICKED operation; `form.is_valid()` → `_invalid_form_response`
   (unchanged). Disabled fields were never submitted, and the union's extra keys are simply not in
   this form.
4. `if not check.ready` → 503 (unchanged).
5. Files merged (unchanged), then
   **`params = services.fill_engine_blanks(operation, {**form.cleaned_data, **files}, check.resolved)`**
   before `validate_params(operation, params)` (`views.py:737`).
6. Everything after — staging, payload, `enqueue` — unchanged.

`fill_engine_blanks` fills a key only when it is in `live_ignored`, its kind is `"choice"`, and the
submitted value is blank: `live_defaults(operation, resolved)[key]` if present, else the first entry
of `live_options(operation, resolved)[key]`, else left as-is. `negative_prompt` (a `text` param,
not required) is deliberately NOT filled — writing a value into the job record for text the graph
never read would be a lie. A `required` param that is also ignored is a template bug, not a case to
paper over: nothing fills it and `validate_params` refuses it, loudly.

Error rendering is unchanged. The XHR 400 still renders `_form_errors.html` off the bound
operation form. The non-XHR re-render (`_create_page_response`, `views.py:1053-1061`) builds the
constant form BOUND to `request.POST` — so typed values come back — calls `is_valid()` once, then
copies the operation form's errors across with `add_error(key if key in form.fields else None, …)`,
the same trick `_picker_failure_response` already uses (`views.py:856-857`). `{{ field.errors }}`
in the template therefore needs no change.

---

## 6. Catalog JSON

`services.operation_catalog` (`services.py:304-344`) iterates `services.operation_states(resolved)`
instead of `operations_for_model`, so the array lists every registered operation. Each entry gains:

```json
{"key": "txt2img", "supported": false, "unsupported_reason": "No text to image graph for the qwen_image family.",
 "params": [{"key": "scheduler", "…": "…", "ignored": "This model's own noise schedule has no separate setting to choose."}]}
```

`"ignored"` is `null` for every honoured param and is filled from `live_ignored`;
`"unsupported_reason"` is `""` when `"supported"` is `true`. `describe`/`_describe_param`
(`operations.py:150-199`) are untouched — the catalog layers on top, exactly as it already does for
`options`/`default`. **Contract change:** a caller that previously read the array as "modes I can
run" must now filter on `"supported"`. The only in-repo consumer is the page; the peer's P1
`tools/vision/tools.py` is the second, coordinated in §11.

---

## 7. Interfaces

```python
# models/contracts/engines/base.py — optional member, docstring only (beside param_defaults, :395)
def ignored_params(self, operation_key: str, config: dict | None = None) -> dict[str, str]:
    """Params `operation_key`'s schema declares that this engine's graph for a connection
    registered with `config` cannot honour, mapped to the operator-facing reason. `{}` when
    this adapter honours everything. The negative twin of `param_defaults`."""

# models/contracts/engines/comfyui.py — implementation (beside param_defaults, :668).
# `workflow_ignores` is `comfyui_workflows.ignored_params`, aliased in the module's
# existing import block (:30-32) so the module function and this method never shadow.
def ignored_params(self, operation_key: str, config: dict | None = None) -> dict[str, str]:
    cfg = dict(config or {})
    return workflow_ignores(operation_key, str(cfg.get("family") or ""))

# tools/vision/services.py
def live_ignored(operation: Operation, resolved) -> dict[str, str]: ...
    # mirror of live_defaults (:223): {} for None/unregistered engine/absent member/raise;
    # keys the operation does not declare are dropped.
def fill_engine_blanks(operation: Operation, params: dict, resolved) -> dict: ...
def union_params(operations: Sequence[Operation]) -> tuple[Param, ...]: ...
@dataclass(frozen=True)
class OperationState:
    operation: Operation
    supported: bool
    reason: str          # "" when supported
def operation_states(resolved: ResolvedModel | None) -> tuple[OperationState, ...]: ...

# tools/vision/forms.py
def build_constant_form(
    operations: Sequence[Operation],
    operation: Operation,
    engine_options: dict[str, tuple[str, ...]],
    ignored: dict[str, str],
    initial: dict | None = None,
    stored_keys=frozenset(),
    data=None,
) -> forms.Form: ...
```

`build_constant_form` reuses `_field_for` per param unchanged, then stamps each `forms.Field`
instance with `field.state` and `field.reason` (read in the template as `f.field.state`), sets
`widget.attrs["disabled"] = True` and `required = False` for the two disabled states, and applies
`initial`/`stored_keys` exactly as `build_form` does (`forms.py:166-181`). `build_form` itself is
untouched — it is still what the POST path and the future tool build against.

---

## 8. Tests

| File | Change |
|---|---|
| `tools/vision/tests/test_views_create.py` (74 tests) | **Deleted:** `TestImageToImageMerge`, `TestFieldHintsRender`. **Rewritten:** `TestOperationSurface` (select instead of tabs; every registered operation listed; unsupported ones disabled with a reason; unknown key still 404; `op/<key>/` and `?operation=` render the same page), `TestOperationsSupportsNothingBanner` (banner + disabled fieldset + every option disabled), `TestModelPicker` (picker + operation select share one GET form; every generated link carries both), `TestQwenImageFamilyPageAndPicker` (no merge; `img2img`/`inpaint`/`txt2img`/`upscale` options disabled with the `qwen_image` reason). **Kept unchanged:** `TestCreatePage`, `TestSharedNavEntry`, `TestBannerLinksToSetup`, `TestFactsLayouts`, `TestCardHeadline`, `TestReuseResolvesOperation`, `TestSharedCardChrome`, `TestCardActionParity`, `TestSecondModeArrivesByRegistrationAlone`, `TestJobInputsAreVisible`, `TestCardStructure`, `TestStoredInputPrefill`, `TestLiveAssetOptions`, `TestUpscalePage`, `TestEnginePositionOnTheCard`. **New:** `TestConstantForm` (all 20 union keys present for every operation, in the fixed order; picked-operation `Param` supplies an enabled field's default — `steps=20` under `edit`, `25` under `txt2img`), `TestFieldStates` (the distilled build/`flux2` + `txt2img` disables `negative_prompt` + `scheduler` with the `IGNORES` sentences; `edit` marks `width`/`height` "Not used by Edit an image."; the baseline checkpoint enables everything), `TestInfoTooltips` (every field carries `ⓘ` + `aria-describedby`; the description is in the DOM; a disabled field's reason appears in the tooltip AND as an inline muted line; `.field-hint` is gone) |
| `test_views_operations.py` | **Rewritten:** `TestVisionOperations` — the catalog now lists unsupported operations too, each with `"supported": false` + a non-empty `"unsupported_reason"`; params carry `"ignored"`; the body is still JSON-safe. **Kept:** `TestPickedConnection` |
| `test_views_generate.py` | **New:** `TestIgnoredParamsSubmit` — a `flux2` `txt2img` POST with `scheduler` absent (the disabled widget sent nothing) is queued, not 400; the enqueued payload carries the back-filled value; `negative_prompt` stays blank in the payload |
| `test_forms.py` | **Rewritten:** `TestSchemaDescriptionsBecomeHints` → still asserts `help_text` (now the tooltip body), plus situational copy still wins. **Kept:** `TestBuildForm`, `TestAssetFields`, `TestFileFieldsAreNeverPrefilled`, `TestStoredKeys`. **New:** `TestConstantFormFields` (union set/order; `state`/`reason` per field; disabled fields carry `disabled` and `required=False`; a bound constant form re-populates typed values) |
| `test_services.py` | **New:** `TestLiveIgnored`, `TestFillEngineBlanks`, `TestUnionParams`, `TestOperationStates` — including the never-500 tolerances (`None`, unregistered engine name, absent member, raising member) |
| `test_comfyui_engine.py` | **New:** `TestIgnoredParams` — `("flux2","txt2img")` reports both keys; a checkpoint connection reports `{}` |
| `test_engine_base.py` | **New:** the optional-member docstring/signature assertion, beside the `param_defaults` one |

---

## 9. Docs

- `tools/vision/README.md`: rewrite "### The model picker" (`:664`) into "### The model picker and
  the operation select"; replace the "One 'Image to image' tab, not two" subsection with "One
  constant form"; extend "### Models × operations" (`:275`) so the `IGNORES` paragraph says the
  console now disables those fields rather than "deferred to the constant-form work"; note the
  `?operation=` URL scheme and the `op/<key>/` alias under "## The poll-driven page" (`:633`).
- `docs/adr/0012-…md`: new **D-EDIT-13 (R2, 2026-08-27) — one constant input form**, after
  D-EDIT-12 (`:720-790`). Outline: (a) the page renders the union of every registered operation's
  params, enabled/disabled by picked model + operation, because narrowing removed function the
  operator had — `_MERGED_OPERATION_KEYS`/`page_tabs` retired; (b) the "this model ignores that
  param" fact travels engine → `services.live_ignored` → page, never a `comfyui_workflows` import
  from `tools/vision`, so D-EDIT-12's reader finally has a consumer; (c) D-EDIT-7 still stands —
  nothing here narrows, adds, or removes a `Param`, and `validate_params` is still the floor;
  (d) the catalog now lists unsupported operations with a reason, a deliberate reversal of "never
  tell a tool about a mode it cannot perform", because saying why is more useful than silence;
  (e) `?operation=` is the canonical URL, `op/<key>/` an alias.
- `docs/ROADMAP.md` Phase 1.55 (`:76-170`): add a `[x]` **One constant input form (R2)** bullet
  after the R1 bullet, and move nothing out of Deferred.

Per `docs/adr/0008-engineering-standards.md`, all three ship in the same PR as the code.

---

## 10. Verification

1. Full suite on the branch preview DB (port 5435), both orders:
   `pytest` and `pytest -p no:randomly --reverse` — no failures, no new warnings.
2. Live on the branch preview at `http://localhost:8002/vision/` (per the R1 log's connection
   numbering: the distilled 9B build = conn 3, the ordinary build = conn 2):
   - pick the distilled build → operation `Text to image`: every one of the 20 fields is present;
     `negative_prompt` and `scheduler` are disabled, each showing its `IGNORES` sentence inline and
     in its tooltip; `Inpaint`, `Image to image`, and `Upscale` are disabled entries in the
     Operation select, each naming the `flux2` family; a Generate succeeds and produces an image.
   - switch to the baseline checkpoint connection → every field enabled, `Edit an image` disabled with
     its reason, no `IGNORES` text anywhere.
   - screenshot of a tooltip open on hover and again on keyboard focus.
3. Both artefacts pasted into the PR, per the verification doctrine — no success language before
   fresh pixels.

---

## 11. Follow-ups (not this spec's code)

1. After P1 merges to main, refactor `tools/vision/tools.py::_fill_engine_params` to call
   `services.fill_engine_blanks` and `build_generate_spec` to call `services.union_params`, and make
   its catalog read filter on `"supported"`. One function each, two callers.
2. JS live toggling of field states without a reload (progressive enhancement over §4's no-JS floor).
3. Rebind the `vision.generate` role's baseline-checkpoint default, an owner decision left open on the vision branch.
