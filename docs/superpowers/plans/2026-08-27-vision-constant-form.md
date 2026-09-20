# Vision Constant Input Form Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `/vision/` stops narrowing itself. The page renders the UNION of every registered `image-generation` operation's params — always all 20 fields, always in one fixed order — with each field `enabled`, `unused`, or `ignored` and each disabled field carrying the operator-facing reason it is disabled. Every registered operation appears in a new Operation `<select>`, the unsupported ones disabled with a reason. `Param.description` moves from an always-visible `.field-hint` into a hover/focus ⓘ tooltip. Nothing is hidden; nothing is silently dropped.

**Architecture:** Four layers, each already in place, extended by one member apiece. (1) A template declares `IGNORES`; `comfyui_workflows.ignored_params` reads it. (2) A new OPTIONAL engine member `ComfyUIEngine.ignored_params(operation_key, config)` republishes it — the negative twin of `param_defaults`. (3) `tools/vision/services.py` gains `live_ignored` (the `getattr`-and-never-raise mirror of `live_defaults`), `union_params`, `OperationState`/`operation_states`, and `fill_engine_blanks`. (4) `tools/vision/forms.py` gains `build_constant_form`, and `create.html` defers each field to a new `vision/_field.html`. `tools/vision` never imports `comfyui_workflows` and never learns which engine is bound: the fact travels engine → services → page.

**Tech Stack:** Django 5 templates + `forms.Form` built at runtime from `Operation.params`; pytest + pytest-django on the host venv against the branch preview DB (port 5435); no new JavaScript, no new dependency, no migration.

**Spec:** docs/superpowers/specs/2026-08-27-vision-constant-form-design.md

## Global Constraints

- Worktree ONLY: `<worktree>`, branch `vision-r2-constant-form`. NEVER touch `<repo>` (the live prod checkout) or any other worktree.
- Untouched files: `models/contracts/operations.py`, `agents/**`, `tools/rag/**`, `models/registry/**`, `models/queue/**`, `config/**`, `pytest.ini`.
- Import law: `tools/vision` imports only pure leaves (`models.contracts.*`, `agents.contracts.*`, `foundation.format`/`foundation.files`) and `models.registry.bindings`; it NEVER imports `models.contracts.engines.comfyui_workflows` directly.
- `tools/vision/tools.py` module-scope imports stay stdlib + `agents.contracts.*` + `models.contracts.*` ONLY; `tools.vision.services` is imported lazily inside runners. Gate: `foundation/ops/tests/test_column_boundaries.py::test_no_tool_module_imports_its_service_layer_at_module_scope`.
- Gates every task must keep green: `foundation/ops/tests/test_column_boundaries.py`, `foundation/ops/tests/test_import_law.py`, `agents/contracts/tests/` (P1 guards), `tools/rag/tests/test_flag_hygiene.py`.
- Flag hygiene (`tools/rag/tests/test_flag_hygiene.py`) is a FILE-scoped static sweep, not a per-test one: a file that contains a literal `FARABUNKER_FEATURES=frozenset(...)` override WITHOUT `"vision"` in it must not contain `reverse(` or `Client(` ANYWHERE, unless that file reloads `config.urls` itself. `tools/vision/tests/test_tools.py` already carries `override_settings(FARABUNKER_FEATURES=frozenset())` (:97), so NOTHING added to that file may use `reverse(` or `Client(` — Task 8's additions deliberately use neither. No task below adds a new override.
- Test command (host venv, branch DB): `DATABASE_URL=postgres://farabunker:farabunker@localhost:5435/farabunker <repo>/.venv/bin/pytest -q -p no:cacheprovider <paths>`
- ADR 0012 D-EDIT-7 still stands: nothing here narrows, adds, or removes a `Param`, and `models.contracts.operations.validate_params` is still the schema floor. D-EDIT-1 and D-EDIT-12 are unchanged; this is their consumer.
- No new JavaScript beyond what `create.html` already has (card polling, XHR submit, the `data-reload-on-change` picker handler). The Operation select joins the picker's existing GET form. Tooltips are pure CSS (`:hover`/`:focus-within`).
- Tests + docs ship with the code, in the same task, per `docs/adr/0008-engineering-standards.md`.
- Every commit carries the trailers:
  `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`
  `Claude-Session: https://claude.ai/code/session_<id>`
- Do not push, do not open a PR, do not merge. Task 9 is the only task that touches the live preview, and only at `http://localhost:8002/` — NEVER `:8000`, never `main`.

---

## File structure

**Created**

| File | Single responsibility |
|---|---|
| `tools/vision/templates/vision/_field.html` | Render ONE form field: label, ⓘ tooltip (`aria-describedby`), widget, inline reason for a disabled field, errors. The only place a field's chrome is written. |
| `docs/superpowers/plans/2026-08-27-vision-constant-form.md` | This plan. |

**Modified**

| File | What changes, and why it is the only thing that changes there |
|---|---|
| `models/contracts/engines/base.py` | +1 optional-member docstring: `ignored_params`, beside `param_defaults` (:393). Contract text only — this file has no bodies. |
| `models/contracts/engines/comfyui.py` | +1 import alias (`ignored_params as workflow_ignores`) and +1 method `ignored_params`, beside `param_defaults` (:669). The adapter is the ONLY place `comfyui_workflows` is read. |
| `tools/vision/services.py` | +`live_ignored`, +`union_params`, +`OperationState`, +`operation_states`, +`fill_engine_blanks`; `operation_catalog` gains `supported`/`unsupported_reason`/per-param `ignored`. Every engine fact the page needs enters here and nowhere else. |
| `tools/vision/forms.py` | +`build_constant_form`. `build_form`, `_field_for`, `_base_field_for` untouched — the POST path and the tool still build against them. |
| `tools/vision/views.py` | Deletes the merge/tab machinery; the page builds the constant form, offers the Operation select, and emits `?operation=` URLs. |
| `tools/vision/templates/vision/create.html` | Tab nav → Operation select inside the picker GET form; the field loop defers to `_field.html`; `.op-chooser`/`.field-hint` CSS out, tooltip/field-state CSS in; constant `<h1>`. |
| `tools/vision/templates/vision/_output_actions.html` | `?input_` → `&input_` (the target URL now already carries `?operation=`). |
| `tools/vision/tools.py` | `_fill_engine_params` delegates its fill to `services.fill_engine_blanks`; a new anti-drift note pointing at `services.union_params`. |
| `tools/vision/tests/test_engine_base.py` | Pins `ignored_params` as an optional protocol member. |
| `tools/vision/tests/test_comfyui_engine.py` | Pins the adapter's `ignored_params`. |
| `tools/vision/tests/test_services.py` | Pins `live_ignored`, `union_params`, `operation_states`, `fill_engine_blanks`, and the catalog's new keys. |
| `tools/vision/tests/test_views_operations.py` | Pins the catalog contract change (unsupported operations listed, with reasons). |
| `tools/vision/tests/test_forms.py` | Pins `build_constant_form`; `TestSchemaDescriptionsBecomeHints` reworded to "the tooltip body". |
| `tools/vision/tests/test_views_create.py` | Deletes `TestImageToImageMerge`; rewrites the operation-surface/tab/hint classes; adds `TestConstantForm`, `TestFieldStates`, `TestInfoTooltips`. |
| `tools/vision/tests/test_views_generate.py` | +`TestIgnoredParamsSubmit`; redirect assertions follow the `?operation=` scheme. |
| `tools/vision/tests/test_tools.py` | +one anti-drift test tying `build_generate_spec()` to `services.union_params`. |
| `tools/vision/README.md` | The operation select, the constant form, the `?operation=` scheme, and the now-consumed `IGNORES`. |
| `docs/adr/0012-image-generation-engine-adapter.md` | +D-EDIT-13, after D-EDIT-12. |
| `docs/ROADMAP.md` | +one `[x]` bullet in Phase 1.55, after the R1 bullet. |

---

## Resolved spec ambiguities (read before Task 3 and Task 8)

1. **§5 step 3 cannot work as written.** The POST form is `build_form(PICKED operation)`, in which an `ignored` param (`scheduler` on `flux2` txt2img) is a `ChoiceField` with `required=True` (`forms._base_field_for`, :61-69) — but the union render disabled that widget, so the browser submitted nothing and `form.is_valid()` would 400 *before* `fill_engine_blanks` ever runs. **Resolution:** `views.build_form_for` relaxes `required` to `False` for exactly the `services.live_ignored` keys, on the bound form, before any `is_valid()` call. `forms.build_form` stays untouched (spec §7); the relaxation is a PAGE rule about a field the page itself disabled, so it lives in the page's own form builder. `fill_engine_blanks` then supplies the value before `validate_params`, which is still the floor.
2. **§8's "beside the `param_defaults` one" does not exist.** `tools/vision/tests/test_engine_base.py` has no `param_defaults` assertion; the closest thing is `TestProtocolsAreImportable::test_inference_engine_declares_the_optional_members`. **Resolution:** add `ignored_params` to that tuple and add one signature/docstring class beside it.
3. **§11's `build_generate_spec` → `services.union_params` adoption is refused, deliberately.** `VisionConfig.ready()` calls `build_generate_spec()`, and `tools/vision/tools.py`'s own docstring plus `test_column_boundaries` forbid pulling the service layer in at registration time — a lazy `from tools.vision import services` inside `build_generate_spec` would still import Django models during `ready()`. **Resolution:** Task 8 adopts `fill_engine_blanks` only (it is reached from a runner, lazily), and pins the two unions together with a TEST instead: `test_tools.py` asserts `build_generate_spec()`'s real-param keys match `services.union_params(all_operations())`. One assertion, zero startup cost.
   Two corollaries, both rulings:
   - **`union_params` does NOT move into `models/contracts/operations.py`.** That would let both callers share it with no import problem at all — and it is refused because that file is inside this plan's untouched set and inside the peer's P1 no-touch zone (its guards and plan pin it). A shared home for this function is a future conversation with the peer, not a change this branch makes unilaterally.
   - **`tools/vision/forms.py` does NOT import `tools/vision/services.py`.** `forms` is the pure schema→widget layer; giving it a service-layer edge to reach one pure helper is the wrong direction even though nothing cycles today. So `build_constant_form` takes the UNION already computed: `build_constant_form(union, operation, ...)`, and the VIEW calls `services.union_params(operations_for(IMAGE_GENERATION_CAPABILITY))` and passes it in. This deviates from the spec §7 signature's first argument (`operations: Sequence[Operation]` → `union: Sequence[Param]`) and is the second such deviation, after resolution 4's `keys=`.
4. **§11's `_fill_engine_params` → `fill_engine_blanks` is a superset, not an equal.** `_fill_engine_params` fills EVERY blank engine-owned `"choice"` param (a tool caller may omit `sampler` entirely); `fill_engine_blanks`'s page rule fills only the `live_ignored` ones. A straight substitution would regress the tool. **Resolution:** `fill_engine_blanks(operation, params, resolved, keys=None)` — `keys=None` means "the `live_ignored` keys" (the page's rule, exactly as §7 specifies); a caller may name its own set. `tools.py` keeps its preflight/`ToolRefused` guard and passes its wider set.
5. **Full-width fields.** `create.html` today hardcodes `prompt`/`negative_prompt` as the two full-width fields; the union adds `instruction`. Rather than grow that hardcoded list, `build_constant_form` stamps `field.wide = (param.kind == "text")` and the template branches on it — the template then names no param at all.
6. **`f.field.state`/`f.field.reason` on a plain `build_form` field.** Absent attributes resolve to `""` in Django templates (`string_if_invalid`), which is what lets Task 5 land the new partial before Task 6 switches the page to the constant form. Both tasks are independently green.

---

### Task 1: The `ignored_params` engine member and `services.live_ignored`

The fact travels: `flux2_txt2img.IGNORES` → `comfyui_workflows.ignored_params` → `ComfyUIEngine.ignored_params` → `services.live_ignored`. Nothing renders it yet.

**Files**
- Modify `models/contracts/engines/base.py` (insert after `param_defaults`, :393-398)
- Modify `models/contracts/engines/comfyui.py` (import block :30-32; method after `param_defaults`, :669-682)
- Modify `tools/vision/services.py` (after `live_defaults`, :223-254)
- Modify `tools/vision/tests/test_engine_base.py` (Test) — `TestProtocolsAreImportable` :96-101, + new class
- Modify `tools/vision/tests/test_comfyui_engine.py` (Test) — new class after `TestVariantsAndParamDefaults` :1065-1092
- Modify `tools/vision/tests/test_services.py` (Test) — new class after `TestLiveDefaults` :778-823
- Modify `tools/vision/README.md` ("### Models × operations", the whole `IGNORES` paragraph at :322-326)

**Interfaces**
- Consumes: `models.contracts.engines.comfyui_workflows.ignored_params(operation_key: str, family: str = "") -> dict[str, str]`; `models.contracts.engines.get_engine(name)`; `models.contracts.operations.Operation`
- Produces:
  - `InferenceEngine.ignored_params(self, operation_key: str, config: dict | None = None) -> dict[str, str]` (optional member, docstring only)
  - `ComfyUIEngine.ignored_params(self, operation_key: str, config: dict | None = None) -> dict[str, str]`
  - `services.live_ignored(operation: Operation, resolved) -> dict[str, str]`

**Steps**

- [ ] Write the failing protocol test. In `tools/vision/tests/test_engine_base.py`, add `"ignored_params"` to the tuple in `test_inference_engine_declares_the_optional_members` (:97-100) and append this class at the end of the file:
  ```python
  class TestIgnoredParamsIsAnOptionalMember:
      """The negative twin of `param_defaults` (ADR 0012 D-EDIT-13). Read
      downstream via `getattr`, so an adapter that has none is untouched --
      the same rule every other optional member follows."""

      def test_the_protocol_declares_it_with_the_param_defaults_signature(self):
          import inspect

          signature = inspect.signature(InferenceEngine.ignored_params)
          assert list(signature.parameters) == ["self", "operation_key", "config"]
          assert signature.parameters["config"].default is None
          assert "cannot honour" in (InferenceEngine.ignored_params.__doc__ or "")

      def test_an_adapter_without_one_reports_nothing_through_getattr(self):
          assert getattr(OllamaEngine(), "ignored_params", None) is None
  ```
- [ ] Write the failing adapter test. In `tools/vision/tests/test_comfyui_engine.py`, append after `TestVariantsAndParamDefaults`:
  ```python
  class TestIgnoredParams:
      """`ComfyUIEngine.ignored_params` republishes what the template
      package declares (`flux2_txt2img.IGNORES`, ADR 0012 D-EDIT-12) --
      pure, no HTTP, exactly like `param_defaults` beside it."""

      def test_flux2_txt2img_names_both_params_neither_graph_can_honour(self):
          ignored = ComfyUIEngine().ignored_params("txt2img", {"family": "flux2"})

          assert set(ignored) == {"negative_prompt", "scheduler"}
          assert "no negative-prompt input" in ignored["negative_prompt"]
          assert ignored["scheduler"] == (
              "This model's own noise schedule has no separate setting to choose."
          )

      def test_a_checkpoint_connection_ignores_nothing(self):
          engine = ComfyUIEngine()
          assert engine.ignored_params("txt2img", None) == {}
          assert engine.ignored_params("txt2img", {}) == {}

      def test_a_pairing_with_nothing_to_ignore_is_empty_not_an_error(self):
          assert ComfyUIEngine().ignored_params("edit", {"family": "flux2"}) == {}

      def test_a_json_null_family_reads_as_a_checkpoint(self):
          """`config["family"]` may be a JSON `null` on a stored connection;
          it must read as the empty family, never as the string 'None'."""
          assert ComfyUIEngine().ignored_params("txt2img", {"family": None}) == {}
  ```
- [ ] Write the failing service test. In `tools/vision/tests/test_services.py`, append after `TestLiveDefaults` (before `TestOperationCatalog`):
  ```python
  class TestLiveIgnored:
      """The exact twin of `TestLiveDefaults` above, for the seam that
      reports which params this model's graph cannot honour AT ALL (ADR
      0012 D-EDIT-13). Same `_resolved()` idiom, same never-500 tolerances."""

      def test_the_selected_models_own_ignored_params_are_reported(self):
          resolved = _resolved(config={"family": "flux2"})
          ignored = services.live_ignored(TXT2IMG, resolved)

          assert set(ignored) == {"negative_prompt", "scheduler"}
          assert ignored["scheduler"].endswith("no separate setting to choose.")

      def test_nothing_selected_reports_nothing(self):
          assert services.live_ignored(TXT2IMG, None) == {}

      def test_a_key_the_operation_does_not_declare_is_dropped(self):
          """An engine names a param the SCHEMA declares, or it names
          nothing -- the same rule `live_defaults` follows."""
          engine = StubEngine()
          engine.ignored_params = lambda op, config: {
              "scheduler": "no scheduler input", "not_a_param": "nope",
          }
          with patch.dict(ENGINES, {"comfyui": engine}, clear=True):
              assert services.live_ignored(TXT2IMG, _resolved()) == {
                  "scheduler": "no scheduler input"
              }

      def test_an_unregistered_engine_name_reports_nothing(self):
          with patch.dict(ENGINES, {}, clear=True):
              assert services.live_ignored(TXT2IMG, _resolved()) == {}

      def test_an_adapter_with_no_such_member_reports_nothing(self):
          with patch.dict(ENGINES, {"comfyui": StubEngine()}, clear=True):
              assert services.live_ignored(TXT2IMG, _resolved()) == {}

      def test_a_raising_adapter_costs_the_reasons_and_never_a_500(self):
          def _raise(op, config):
              raise RuntimeError("boom")

          engine = StubEngine()
          engine.ignored_params = _raise
          with patch.dict(ENGINES, {"comfyui": engine}, clear=True):
              assert services.live_ignored(TXT2IMG, _resolved()) == {}
  ```
- [ ] Run them and watch them fail:
  `DATABASE_URL=postgres://farabunker:farabunker@localhost:5435/farabunker <repo>/.venv/bin/pytest -q -p no:cacheprovider tools/vision/tests/test_engine_base.py tools/vision/tests/test_comfyui_engine.py::TestIgnoredParams tools/vision/tests/test_services.py::TestLiveIgnored`
  Expected: `AttributeError: type object 'InferenceEngine' has no attribute 'ignored_params'`, `AttributeError: 'ComfyUIEngine' object has no attribute 'ignored_params'`, and `AttributeError: module 'tools.vision.services' has no attribute 'live_ignored'`.
- [ ] Add the optional member to `models/contracts/engines/base.py`, immediately after `param_defaults` (:398) and inside the same "Optional: a variant … and its own param defaults" block:
  ```python
      def ignored_params(self, operation_key: str, config: dict | None = None) -> dict[str, str]:
          """Params `operation_key`'s schema declares that this engine's graph
          for a connection registered with `config` cannot honour, mapped to
          the operator-facing reason. `{}` when this adapter honours
          everything. The negative twin of `param_defaults`: that one reports
          where a param should START, this one reports that it will not be
          read at all -- so a console can disable the field and say why
          instead of accepting a value and silently dropping it (ADR 0012
          D-EDIT-12/13). It ANNOTATES a param the schema still declares;
          D-EDIT-7 still stands."""
          ...
  ```
- [ ] Implement it in `models/contracts/engines/comfyui.py`. Extend the existing import block (:30-32) — the alias is what keeps the module-level function and this method from shadowing each other:
  ```python
  from models.contracts.engines.comfyui_workflows import (
      families, get_template, ignored_params as workflow_ignores, template_keys,
      variant_defaults, variants,
  )
  ```
  and add the method directly after `param_defaults` (after :682):
  ```python
      def ignored_params(self, operation_key: str, config: dict | None = None) -> dict[str, str]:
          """Params `operation_key`'s schema declares that this connection's
          graph cannot honour, with the operator-facing reason each is
          skipped -- read straight from the template that declares them
          (`comfyui_workflows.ignored_params`, ADR 0012 D-EDIT-12), so
          naming one is a template edit and nothing else.

          Keyed on the FAMILY alone, not the variant: an `IGNORES` entry is
          a fact about the wiring both of a family's graphs share (neither
          flux2 graph has a negative-prompt input), never about which build
          of it an operator declared.
          """
          cfg = dict(config or {})
          return workflow_ignores(operation_key, str(cfg.get("family") or ""))
  ```
- [ ] Implement `services.live_ignored` in `tools/vision/services.py`, immediately after `live_defaults` (after :254):
  ```python
  def live_ignored(operation: Operation, resolved) -> dict[str, str]:
      """Which of `operation`'s params the SELECTED model's graph cannot
      honour at all, mapped to the operator-facing reason.

      The negative twin of `live_defaults` above, and read the same way:
      `{}` for an unbound role, an engine name that is not registered, an
      adapter with no such member, or one that raises -- a disabled field
      is never worth a 500, and rendering the field ENABLED is the honest
      degradation (nothing was learned, so nothing is claimed).

      Reported keys the operation does not declare are DROPPED, exactly as
      `live_defaults` drops them: an engine may annotate a param, never
      invent one. This is what lets the page disable a field and say why
      (ADR 0012 D-EDIT-13) without ever importing a graph template.
      """
      if resolved is None:
          return {}
      try:
          engine = get_engine(resolved.engine)
      except Exception:  # noqa: BLE001 -- an unregistered engine name is not a 500
          logger.debug("get_engine(%r) failed", resolved.engine, exc_info=True)
          return {}
      reader = getattr(engine, "ignored_params", None)
      if reader is None:
          return {}
      try:
          reported = dict(reader(operation.key, dict(resolved.config or {})))
      except Exception:  # noqa: BLE001 -- never 500 over a reason string
          logger.debug("ignored_params failed for %r", operation.key, exc_info=True)
          return {}
      declared = {param.key for param in operation.params}
      return {key: str(value) for key, value in reported.items() if key in declared}
  ```
- [ ] Update `tools/vision/README.md` — in "### Models × operations", replace the WHOLE `IGNORES` paragraph (:322-326, from "`build` never reads either key." through "is deferred to the constant-form work.") — the whole paragraph, not just its last sentence, or the two opening sentences end up written twice:
  ```
  `build` never reads either key. `ignored_params` is a pure lookup, like
  `variant_defaults`: an unregistered pairing, or one with nothing to
  ignore, answers `{}`, never raises. The engine republishes it through the
  optional `InferenceEngine.ignored_params(operation_key, config)` member,
  and `services.live_ignored` is what carries it to the page — which
  renders those two fields DISABLED, each showing this sentence, rather
  than accepting a value and silently dropping it (ADR 0012 D-EDIT-13).
  ```
- [ ] Run the same command. Expected: all green, no warnings.
- [ ] Run the gates:
  `DATABASE_URL=postgres://farabunker:farabunker@localhost:5435/farabunker <repo>/.venv/bin/pytest -q -p no:cacheprovider foundation/ops/tests/test_column_boundaries.py foundation/ops/tests/test_import_law.py tools/rag/tests/test_flag_hygiene.py agents/contracts/tests tools/vision/tests/test_comfyui_workflows.py`
  Expected: all green.
- [ ] Commit:
  ```
  git add -A && git commit -m "$(cat <<'EOF'
  feat(vision): R2 T1 — engines report the params their graph cannot honour

  `ComfyUIEngine.ignored_params` republishes `comfyui_workflows.ignored_params`
  as an optional engine member, and `services.live_ignored` is its
  `getattr`-and-never-raise reader — the negative twin of
  `param_defaults`/`live_defaults`. D-EDIT-12's reader finally has a path to
  the page that never imports a graph template.

  Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_<id>
  EOF
  )"
  ```

---

### Task 2: `union_params`, `operation_states`, and the honest catalog

The catalog stops hiding modes the model cannot run and starts saying why. `union_params` is the set-and-order the page's constant form will render.

**Files**
- Modify `tools/vision/services.py` (`union_params`/`OperationState`/`operation_states` after `operations_for_model`, :257-302; `operation_catalog` body, :304-344; imports :17-37)
- Modify `tools/vision/tests/test_services.py` (Test) — new classes after `TestOperationsForModel` (:1278-1361), AND rewrite `TestOperationCatalogFollowsTheSelectedModel::test_the_catalog_lists_only_the_selected_models_operations` (:1363-1376), which asserts `[entry["key"] for entry in catalog] == ["edit"]` and is broken by this task by construction
- Modify `tools/vision/tests/test_views_operations.py` (Test) — rewrite `TestVisionOperations`

**Interfaces**
- Consumes: `models.contracts.operations.{Operation, Param, operations_for, describe}`, `models.contracts.bindings.config_family`, `services.operations_for_model`, `services.live_options`, `services.live_defaults`, `services.live_ignored`
- Produces:
  - `services.union_params(operations: Sequence[Operation]) -> tuple[Param, ...]`
  - `services.OperationState` — frozen dataclass `(operation: Operation, supported: bool, reason: str)`
  - `services.operation_states(resolved: ResolvedModel | None) -> tuple[OperationState, ...]`
  - `services.operation_catalog(resolved=None) -> list[dict]` — each entry now carries `"supported": bool`, `"unsupported_reason": str`, and every param carries `"ignored": str | None`

**Steps**

- [ ] Write the failing service tests. Append to `tools/vision/tests/test_services.py`:
  ```python
  class TestUnionParams:
      """The SET and the ORDER the constant form renders: every registered
      operation's params, de-duplicated by key, first declaration wins, in
      registration order (ADR 0012 D-EDIT-13)."""

      def test_it_is_every_key_in_first_declaration_order(self):
          union = services.union_params(operations_for(IMAGE_GENERATION_CAPABILITY))

          assert [param.key for param in union] == [
              "prompt", "negative_prompt", "width", "height", "steps", "cfg_scale",
              "seed", "sampler", "scheduler", "batch_size", "loras", "lora_strength",
              "init_image", "denoise", "mask_image", "mask_grow", "upscale_model",
              "instruction", "reference_image", "guidance",
          ]

      def test_the_first_declaration_of_a_shared_key_wins(self):
          """`steps` is 25 in `SAMPLING_PARAMS` and 20 in `EDIT`; `denoise`
          is 0.6 in img2img and 1.0 in inpaint. The union carries the
          FIRST, and the picked operation's own `Param` is what the form
          layer swaps back in for a field that operation declares."""
          union = {param.key: param for param in
                   services.union_params(operations_for(IMAGE_GENERATION_CAPABILITY))}

          assert union["steps"].default == 25
          assert union["denoise"].default == 0.6

      def test_an_empty_sequence_is_an_empty_union(self):
          assert services.union_params([]) == ()

      def test_it_returns_a_tuple_of_the_real_param_objects(self):
          union = services.union_params([TXT2IMG])
          assert isinstance(union, tuple)
          assert union[0] is TXT2IMG.params[0]


  class TestOperationStates:
      """Every registered operation, with the reason the selected model
      cannot run the ones it cannot -- ONE `operations_for_model` call."""

      def test_nothing_selected_supports_everything_with_no_reasons(self):
          states = services.operation_states(None)

          assert [state.operation.key for state in states] == [
              op.key for op in operations_for(IMAGE_GENERATION_CAPABILITY)
          ]
          assert all(state.supported for state in states)
          assert all(state.reason == "" for state in states)

      def test_a_declared_family_names_itself_in_the_reason(self):
          class _Narrow(StubEngine):
              def supported_operations(self, model_id, endpoint, family=""):
                  return ("edit", "txt2img")

          resolved = ResolvedModel(
              engine="stubengine", model_id="w.gguf", endpoint="http://stub:9999",
              config={"family": "flux2"},
          )
          with patch.dict(ENGINES, {"stubengine": _Narrow()}, clear=True):
              states = {s.operation.key: s for s in services.operation_states(resolved)}

          assert states["txt2img"].supported is True
          assert states["txt2img"].reason == ""
          assert states["inpaint"].supported is False
          assert states["inpaint"].reason == "No inpaint graph for the flux2 family."

      def test_a_connection_with_no_family_gets_the_engine_wording(self):
          class _Checkpoint(StubEngine):
              def supported_operations(self, model_id, endpoint, family=""):
                  return ("txt2img", "img2img", "inpaint", "upscale")

          resolved = ResolvedModel(
              engine="stubengine", model_id="sdxl.safetensors",
              endpoint="http://stub:9999", config={},
          )
          with patch.dict(ENGINES, {"stubengine": _Checkpoint()}, clear=True):
              states = {s.operation.key: s for s in services.operation_states(resolved)}

          assert states["edit"].supported is False
          assert states["edit"].reason == "This model's engine has no edit an image graph."

      def test_a_model_that_supports_nothing_reports_a_reason_for_every_mode(self):
          class _Nothing(StubEngine):
              def supported_operations(self, model_id, endpoint, family=""):
                  return ()

          resolved = ResolvedModel(
              engine="stubengine", model_id="w.gguf", endpoint="http://stub:9999",
              config={"family": "unknown_family"},
          )
          with patch.dict(ENGINES, {"stubengine": _Nothing()}, clear=True):
              states = services.operation_states(resolved)

          assert states
          assert not any(state.supported for state in states)
          assert all("unknown_family" in state.reason for state in states)

      def test_it_asks_the_engine_exactly_once(self):
          """One `supported_operations` round trip for the whole select --
          the same dedup reasoning `operation_catalog`'s single preflight
          already follows."""
          calls = []

          class _Counting(StubEngine):
              def supported_operations(self, model_id, endpoint, family=""):
                  calls.append(family)
                  return ("txt2img",)

          resolved = ResolvedModel(
              engine="stubengine", model_id="w.gguf", endpoint="http://stub:9999", config={},
          )
          with patch.dict(ENGINES, {"stubengine": _Counting()}, clear=True):
              services.operation_states(resolved)

          assert len(calls) == 1
  ```
  Add `Param` to the `models.contracts.operations` import at the top of the file if it is not already there (it is, :26).
- [ ] Rewrite `TestVisionOperations` in `tools/vision/tests/test_views_operations.py`. Keep `test_it_returns_the_catalog_wrapped_under_operations`, `test_every_entry_carries_key_label_and_params`, `test_the_body_is_json_safe`, `test_unbound_role_is_still_200`, `test_the_role_binding_is_echoed_when_nothing_is_picked`, `test_an_unusable_connection_is_a_404_with_a_json_error` unchanged. REPLACE `test_the_schema_endpoint_answers_for_the_picked_model` with these three:
  ```python
      def test_the_catalog_lists_every_registered_mode_supported_or_not(self):
          """Contract change (ADR 0012 D-EDIT-13): the array is no longer
          "modes I can run" -- it is every registered mode, and a caller
          filters on `supported`. Saying WHY a mode is unavailable is more
          useful than silence."""
          connection = ModelConnection.objects.create(
              name="picked", engine="stubengine", endpoint="http://stub:9999",
              model_id="w.gguf", capabilities=["image-generation"],
              config={"family": "flux2"},
          )

          class _Narrow(StubEngine):
              def supported_operations(self, model_id, endpoint, family=""):
                  return ("edit",)

          with patch.dict(ENGINES, {"stubengine": _Narrow()}, clear=True):
              response = Client().get(
                  reverse("vision-operations"), {"connection": str(connection.pk)}
              )

          body = response.json()
          entries = {entry["key"]: entry for entry in body["operations"]}
          assert set(entries) == {"txt2img", "img2img", "inpaint", "upscale", "edit"}
          assert entries["edit"]["supported"] is True
          assert entries["edit"]["unsupported_reason"] == ""
          assert entries["inpaint"]["supported"] is False
          assert entries["inpaint"]["unsupported_reason"] == (
              "No inpaint graph for the flux2 family."
          )
          assert body["connection"] == {"id": connection.pk, "name": "picked"}

      def test_every_param_carries_an_ignored_key_null_when_honoured(self):
          connection = ModelConnection.objects.create(
              name="picked", engine="stubengine", endpoint="http://stub:9999",
              model_id="flux2.gguf", capabilities=["image-generation"],
              config={"family": "flux2"},
          )

          # The stub is MANDATORY, not a convenience: `operation_catalog`
          # calls `live_options` for EVERY operation whatever `preflight`
          # said (there is no readiness guard on that path), so a real
          # `comfyui` endpoint here would mean roughly ten httpx attempts at
          # `DISCOVERY_TIMEOUT` (5 s) each. Same `clear=True` idiom
          # `TestLiveDefaults` uses.
          class _Flux2(StubEngine):
              def supported_operations(self, model_id, endpoint, family=""):
                  return ("txt2img", "edit")

              def ignored_params(self, operation_key, config=None):
                  if operation_key != "txt2img":
                      return {}
                  return {
                      "negative_prompt": "This model has no negative-prompt input.",
                      "scheduler": (
                          "This model's own noise schedule has no separate "
                          "setting to choose."
                      ),
                  }

              def list_choices(self, endpoint, key):
                  return ()

          with patch.dict(ENGINES, {"stubengine": _Flux2()}, clear=True):
              response = Client().get(
                  reverse("vision-operations"), {"connection": str(connection.pk)}
              )

          txt2img = next(
              entry for entry in response.json()["operations"] if entry["key"] == "txt2img"
          )
          ignored = {param["key"]: param["ignored"] for param in txt2img["params"]}
          assert set(ignored) >= {"prompt", "negative_prompt", "scheduler"}
          assert ignored["prompt"] is None
          assert ignored["negative_prompt"]
          assert "no separate setting to choose" in ignored["scheduler"]

      def test_an_unbound_catalog_still_carries_both_new_keys(self):
          """Nothing bound is "no opinion", not "supports nothing": every
          entry is supported, every reason blank, every `ignored` null."""
          body = Client().get(reverse("vision-operations")).json()

          for entry in body["operations"]:
              assert entry["supported"] is True
              assert entry["unsupported_reason"] == ""
              assert all(param["ignored"] is None for param in entry["params"])
  ```
  Every test in this class that resolves a connection stubs the engine with `clear=True`: `operation_catalog` asks `live_options` per operation regardless of readiness, so an unstubbed endpoint is a network wait, not a fast degrade.
- [ ] Rewrite the one existing test this task breaks by construction — `tools/vision/tests/test_services.py::TestOperationCatalogFollowsTheSelectedModel` (:1363-1376). Rename it and replace its final assertion, keeping the `_Narrow` engine and `picked` binding above it verbatim:
  ```python
  class TestOperationCatalogFollowsTheSelectedModel:
      def test_the_catalog_lists_every_mode_and_marks_which_one_is_runnable(self):
          ...  # `_Narrow` and `picked` exactly as they are today
          with patch.dict(ENGINES, {"stubengine": _Narrow()}, clear=True):
              catalog = services.operation_catalog(picked)

          # The array is every REGISTERED mode now, in registration order
          # (ADR 0012 D-EDIT-13) -- "which can I run" is the `supported`
          # flag, not the presence of an entry.
          assert [entry["key"] for entry in catalog] == [
              operation.key for operation in operations_for(IMAGE_GENERATION_CAPABILITY)
          ]
          assert [entry["key"] for entry in catalog if entry["supported"]] == ["edit"]
          assert all(
              entry["unsupported_reason"] for entry in catalog if not entry["supported"]
          )
  ```
- [ ] Run and watch fail:
  `DATABASE_URL=postgres://farabunker:farabunker@localhost:5435/farabunker <repo>/.venv/bin/pytest -q -p no:cacheprovider tools/vision/tests/test_services.py::TestUnionParams tools/vision/tests/test_services.py::TestOperationStates tools/vision/tests/test_services.py::TestOperationCatalogFollowsTheSelectedModel tools/vision/tests/test_views_operations.py`
  Expected: `AttributeError: module 'tools.vision.services' has no attribute 'union_params'` / `operation_states`, and `KeyError: 'supported'`.
- [ ] Extend the `tools/vision/services.py` imports: add `Sequence` to the `collections.abc` import (:17) and `Param` to the `models.contracts.operations` import (:30-37).
- [ ] Implement, in `tools/vision/services.py`, immediately after `operations_for_model` (after :302):
  ```python
  def union_params(operations: Sequence[Operation]) -> tuple[Param, ...]:
      """Every param `operations` declare, de-duplicated by key, FIRST
      declaration wins, in the order the operations were registered.

      The SET and the ORDER the console's one constant form renders (ADR
      0012 D-EDIT-13): the operator sees the same 20 fields whichever mode
      is picked, so switching modes never makes a control vanish. The
      first-declaration rule is what makes the order stable -- `steps` sits
      where `txt2img` put it even on an `edit` page, though `edit`'s own
      `Param` (default 20, not 25) is what renders there.

      Pure and engine-free: this is a fact about the REGISTRY, so a caller
      with no binding gets the same answer as one with a model picked.
      """
      seen: set[str] = set()
      union: list[Param] = []
      for operation in operations:
          for param in operation.params:
              if param.key in seen:
                  continue
              seen.add(param.key)
              union.append(param)
      return tuple(union)


  @dataclass(frozen=True)
  class OperationState:
      """One registered operation, and whether the SELECTED model can run
      it -- with the sentence to show when it cannot."""

      operation: Operation
      supported: bool
      reason: str  # "" when supported


  def operation_states(resolved: ResolvedModel | None) -> tuple[OperationState, ...]:
      """Every REGISTERED image-generation operation, each marked supported
      or not for `resolved`, in registration order.

      `operations_for_model` answers "what can this model run"; this answers
      the question the operator actually has in front of a chooser -- "what
      exists, and why can't I pick that one". One `operations_for_model`
      call answers for the whole list; a health check is not repeated per
      operation, the same dedup `operation_catalog` already applies.

      The wording depends on what the CONNECTION declared, because that is
      what the answer depends on (ADR 0012 D-EDIT-2): a connection with a
      family names it, one without says only that the engine has no such
      graph. Nothing bound and nothing picked is "no opinion" --
      `operations_for_model` returns the full registry, so every operation
      is supported and no reason is invented.
      """
      supported_keys = {operation.key for operation in operations_for_model(resolved)}
      family = config_family(resolved.config) if resolved is not None else ""
      states = []
      for operation in operations_for(IMAGE_GENERATION_CAPABILITY):
          if operation.key in supported_keys:
              states.append(OperationState(operation, True, ""))
              continue
          reason = (
              f"No {operation.label.lower()} graph for the {family} family."
              if family
              else f"This model's engine has no {operation.label.lower()} graph."
          )
          states.append(OperationState(operation, False, reason))
      return tuple(states)
  ```
- [ ] Rewrite `operation_catalog`'s loop (:337-344) and extend its docstring. Replace the paragraph beginning "The catalog lists only what that model can run (`operations_for_model`) -- a tool reading this before it submits must never be told about a mode the model it is about to use cannot perform." with:
  ```
      The catalog lists EVERY registered operation, each marked
      `"supported"` and carrying an `"unsupported_reason"` when it is not
      (`operation_states`). This reverses the earlier rule ("never tell a
      tool about a mode it cannot perform", ADR 0012 D-EDIT-13): a caller
      that reads the array as "modes I can run" must now filter on
      `"supported"`, and in exchange it can say WHY a mode is unavailable
      instead of pretending it does not exist. Each param also carries
      `"ignored"` -- the reason this model's graph cannot honour it, or
      `None` -- from `live_ignored`.
  ```
  and the body:
  ```python
      check = preflight(resolved)
      catalog = []
      for state in operation_states(check.resolved):
          operation = state.operation
          entry = describe(operation)
          entry["supported"] = state.supported
          entry["unsupported_reason"] = state.reason
          options = live_options(operation, check.resolved)
          defaults = live_defaults(operation, check.resolved)
          ignored = live_ignored(operation, check.resolved)
          for param in entry["params"]:
              param["ignored"] = ignored.get(param["key"])
              if param["key"] in options:
                  param["options"] = list(options[param["key"]])
              if param["key"] in defaults:
                  param["default"] = defaults[param["key"]]
          catalog.append(entry)
      return catalog
  ```
- [ ] Run the same command. Expected: green.
- [ ] Run the neighbours the catalog contract touches:
  `DATABASE_URL=postgres://farabunker:farabunker@localhost:5435/farabunker <repo>/.venv/bin/pytest -q -p no:cacheprovider tools/vision/tests/test_services.py tools/vision/tests/test_views_operations.py tools/vision/tests/test_tools.py`
  Expected: green. `test_tools.py::run_operations` reads `entry['key']`/`entry['label']` only, so the added keys are inert there; if any assertion compares a whole entry dict, update it to the new shape in this commit.
- [ ] Commit:
  ```
  git add -A && git commit -m "$(cat <<'EOF'
  feat(vision): R2 T2 — the catalog lists every mode, and says why one is unavailable

  `services.operation_states` marks each registered operation supported or
  not for the selected model, from one `operations_for_model` call, and
  `operation_catalog` now carries `supported`/`unsupported_reason` per
  operation and `ignored` per param. `union_params` is the set and order the
  constant form will render.

  Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_<id>
  EOF
  )"
  ```

---

### Task 3: `fill_engine_blanks`, and the POST path that needs it

A field the page disabled was never submitted. The schema floor still refuses a blank `"choice"`, so the value is back-filled from the engine — and `negative_prompt` deliberately is not.

**Files**
- Modify `tools/vision/services.py` (`fill_engine_blanks`, after `live_ignored`)
- Modify `tools/vision/views.py` (`build_form_for` :1039-1050; `generate`'s form build :708-709 and params line :737)
- Modify `tools/vision/tests/test_services.py` (Test) — new class after `TestLiveIgnored`
- Modify `tools/vision/tests/test_views_generate.py` (Test) — new class after `TestGenerateEnqueues` (:456)

**Interfaces**
- Consumes: `services.live_ignored`, `services.live_defaults`, `services.live_options`, `models.contracts.operations.validate_params`
- Produces: `services.fill_engine_blanks(operation: Operation, params: dict, resolved, keys: set[str] | None = None) -> dict`
- Changed: `views.build_form_for(request, operation, check, stored_keys=frozenset(), ignored: dict[str, str] | None = None) -> forms.Form` — relaxes `required` on live-ignored fields, and accepts an already-computed `ignored` map so one POST asks the engine once, not twice (`generate` needs the same map for `fill_engine_blanks`)

**Steps**

- [ ] Write the failing service test. Append to `tools/vision/tests/test_services.py` after `TestLiveIgnored`:
  ```python
  class TestFillEngineBlanks:
      """A field the page DISABLED submitted nothing, and
      `validate_params` refuses a blank `"choice"` regardless of `required`
      (operations.py). This is what stands between those two facts."""

      def _engine(self, ignored, choices):
          engine = StubEngine()
          engine.ignored_params = lambda op, config: dict(ignored)
          engine.list_choices = lambda endpoint, key: choices.get(key, ())
          return patch.dict(ENGINES, {"comfyui": engine}, clear=True)

      def test_an_ignored_choice_param_is_filled_from_the_engines_first_option(self):
          with self._engine(
              {"scheduler": "no scheduler input"}, {"scheduler": ("normal", "karras")}
          ):
              filled = services.fill_engine_blanks(
                  TXT2IMG, {"prompt": "x", "scheduler": ""}, _resolved()
              )

          assert filled["scheduler"] == "normal"
          assert filled["prompt"] == "x"

      def test_a_reported_default_wins_over_the_first_option(self):
          engine = StubEngine()
          engine.ignored_params = lambda op, config: {"scheduler": "no scheduler input"}
          engine.list_choices = lambda endpoint, key: ("normal", "karras")
          engine.param_defaults = lambda op, config: {"scheduler": "karras"}
          with patch.dict(ENGINES, {"comfyui": engine}, clear=True):
              filled = services.fill_engine_blanks(TXT2IMG, {"scheduler": ""}, _resolved())

          assert filled["scheduler"] == "karras"

      def test_a_value_the_operator_supplied_is_never_overwritten(self):
          with self._engine(
              {"scheduler": "no scheduler input"}, {"scheduler": ("normal",)}
          ):
              filled = services.fill_engine_blanks(
                  TXT2IMG, {"scheduler": "karras"}, _resolved()
              )

          assert filled["scheduler"] == "karras"

      def test_an_ignored_text_param_is_never_filled(self):
          """`negative_prompt` is ignored by the flux2 graphs and is a
          `"text"` param. Writing a value into the job record for text the
          graph never read would be a lie -- and `validate_params` does not
          require it, so nothing is blocked."""
          with self._engine(
              {"negative_prompt": "no negative-prompt input"}, {}
          ):
              filled = services.fill_engine_blanks(
                  TXT2IMG, {"prompt": "x", "negative_prompt": ""}, _resolved()
              )

          assert filled["negative_prompt"] == ""

      def test_a_blank_choice_the_model_does_honour_is_left_alone(self):
          """Only an IGNORED key is filled by default: an ENABLED choice
          field the operator left blank is a real validation failure the
          form already reports, not something to paper over."""
          with self._engine({}, {"scheduler": ("normal",)}):
              filled = services.fill_engine_blanks(TXT2IMG, {"scheduler": ""}, _resolved())

          assert filled["scheduler"] == ""

      def test_an_explicit_key_set_overrides_the_ignored_default(self):
              """The tool path fills every blank engine-owned choice param,
              not just the ignored ones (`tools.py::_fill_engine_params`)."""
              with self._engine({}, {"scheduler": ("normal",)}):
                  filled = services.fill_engine_blanks(
                      TXT2IMG, {"scheduler": ""}, _resolved(), keys={"scheduler"}
                  )

              assert filled["scheduler"] == "normal"

      def test_nothing_to_fill_never_asks_the_engine_anything(self):
          engine = StubEngine()
          engine.ignored_params = lambda op, config: {}

          def _boom(*args, **kwargs):
              raise AssertionError("live_options must not be consulted")

          engine.list_choices = _boom
          with patch.dict(ENGINES, {"comfyui": engine}, clear=True):
              assert services.fill_engine_blanks(
                  TXT2IMG, {"prompt": "x"}, _resolved()
              ) == {"prompt": "x"}

      def test_nothing_resolved_returns_the_params_untouched(self):
          params = {"prompt": "x", "scheduler": ""}
          assert services.fill_engine_blanks(TXT2IMG, params, None) == params

      def test_the_input_dict_is_never_mutated(self):
          with self._engine(
              {"scheduler": "no scheduler input"}, {"scheduler": ("normal",)}
          ):
              params = {"scheduler": ""}
              services.fill_engine_blanks(TXT2IMG, params, _resolved())

          assert params == {"scheduler": ""}
  ```
  Fix the indentation of `test_an_explicit_key_set_overrides_the_ignored_default` to match its siblings when writing the file.
- [ ] Write the failing view test. Append to `tools/vision/tests/test_views_generate.py`, after `TestGenerateEnqueues`:
  ```python
  @pytest.mark.django_db
  class TestIgnoredParamsSubmit:
      """ADR 0012 D-EDIT-13: a field the page rendered DISABLED (this
      model's graph cannot honour it) is not submitted by the browser. The
      submission must still be QUEUED, not 400'd -- and the payload must
      carry an honest value for the engine-owned one and an honest BLANK
      for the text one."""

      IGNORES = {
          "negative_prompt": "This model has no negative-prompt input.",
          "scheduler": "This model's own noise schedule has no separate setting to choose.",
      }

      def _flux2_connection(self):
          return ModelConnection.objects.create(
              name="klein", engine="stubengine", endpoint="http://stub:9999",
              model_id="klein.gguf", capabilities=["image-generation"],
              config={"family": "flux2"},
          )

      def _engine_with_ignores(self):
          class _Flux2(StubEngine):
              def supported_operations(self, model_id, endpoint, family=""):
                  return ("txt2img", "edit")

              def ignored_params(self, operation_key, config=None):
                  return dict(TestIgnoredParamsSubmit.IGNORES) if operation_key == "txt2img" else {}

              def list_choices(self, endpoint, key):
                  return {"sampler": ("euler",), "scheduler": ("normal", "karras")}.get(key, ())

          return patch.dict(ENGINES, {"stubengine": _Flux2()}, clear=True)

      @patch("tools.vision.views.enqueue", return_value=12)
      def test_a_submission_missing_the_disabled_fields_is_queued_not_refused(
          self, mock_enqueue, client
      ):
          connection = self._flux2_connection()
          form = {key: value for key, value in FORM.items()
                  if key not in ("scheduler", "negative_prompt")}
          form["connection"] = str(connection.pk)

          with self._engine_with_ignores():
              response = client.post(reverse("vision-generate"), form, **XHR)

          assert response.status_code == 202, response.content.decode()
          payload = mock_enqueue.call_args.args[1]
          assert payload["params"]["scheduler"] == "normal"
          assert payload["params"]["negative_prompt"] == ""

      @patch("tools.vision.views.enqueue", return_value=12)
      def test_an_operator_supplied_value_still_wins(self, mock_enqueue, client):
          connection = self._flux2_connection()
          form = dict(FORM, scheduler="karras", connection=str(connection.pk))

          with self._engine_with_ignores():
              response = client.post(reverse("vision-generate"), form, **XHR)

          assert response.status_code == 202
          assert mock_enqueue.call_args.args[1]["params"]["scheduler"] == "karras"

      @patch("tools.vision.views.enqueue", return_value=12)
      def test_a_model_that_honours_everything_still_refuses_a_missing_choice(
          self, mock_enqueue, client
      ):
          """The relaxation is scoped to what the engine actually said it
          ignores -- an ordinary checkpoint still gets the honest 400."""
          _bind()
          form = {key: value for key, value in FORM.items() if key != "sampler"}

          with _engine():
              response = client.post(reverse("vision-generate"), form, **XHR)

          assert response.status_code == 400
          mock_enqueue.assert_not_called()
  ```
- [ ] Run and watch fail:
  `DATABASE_URL=postgres://farabunker:farabunker@localhost:5435/farabunker <repo>/.venv/bin/pytest -q -p no:cacheprovider tools/vision/tests/test_services.py::TestFillEngineBlanks tools/vision/tests/test_views_generate.py::TestIgnoredParamsSubmit`
  Expected: `AttributeError: module 'tools.vision.services' has no attribute 'fill_engine_blanks'`, and the first view test failing `assert 400 == 202` with `Scheduler: This field is required.`
- [ ] Implement `fill_engine_blanks` in `tools/vision/services.py`, after `live_ignored`:
  ```python
  def fill_engine_blanks(
      operation: Operation, params: dict, resolved, keys: set[str] | None = None
  ) -> dict:
      """`params`, with every blank ENGINE-OWNED `"choice"` param in `keys`
      filled from what the selected model reports.

      `keys` defaults to `live_ignored(operation, resolved)` -- the page's
      rule. A field the console DISABLED (this model's graph cannot honour
      it) submits nothing, and `validate_params` refuses a blank `"choice"`
      regardless of `required` (operations.py) -- so without this, a flux2
      txt2img submission would 400 on a control the operator was never
      allowed to touch. A caller with a different question passes its own
      set: `tools.vision.tools._fill_engine_params` fills every blank
      engine-owned choice, because a TOOL caller may simply have omitted
      one.

      The value is `live_defaults`'s, else the first entry `live_options`
      reports, else the param is left exactly as it came -- the same order
      `build_form` opens its own fields in.

      Only `"choice"` params are ever filled. `negative_prompt` is a
      `"text"` param and stays blank on purpose: writing a value into the
      job record for text the graph never read would be a lie about what
      ran, and nothing requires it. A `required` param that is also ignored
      is a TEMPLATE bug, not a case to paper over -- nothing fills it, and
      `validate_params` refuses it loudly.

      Never mutates `params`; the returned dict is always a copy, so a
      caller's `{**cleaned_data, **files}` (upload objects included) is
      carried through untouched.
      """
      if resolved is None:
          return dict(params)
      if keys is None:
          keys = set(live_ignored(operation, resolved))
      fillable = {
          param.key for param in operation.params
          if param.kind == "choice" and param.key in keys
      }
      blank = {
          key for key in fillable
          if not str(params.get(key) or "").strip()
      }
      if not blank:
          return dict(params)

      defaults = live_defaults(operation, resolved)
      options = live_options(operation, resolved)
      filled = dict(params)
      for key in blank:
          if defaults.get(key):
              filled[key] = defaults[key]
          elif options.get(key):
              filled[key] = options[key][0]
      return filled
  ```
- [ ] Relax `required` for ignored fields in `tools/vision/views.py::build_form_for` (:1039-1050):
  ```python
  def build_form_for(request, operation: Operation, check, stored_keys=frozenset(), ignored=None):
      """The bound form for a submission, with the engine's live options and
      any file param a stored image already answers.

      A param this model's graph cannot honour (`services.live_ignored`) is
      relaxed to optional here, and only here: the PAGE is what rendered
      that field disabled (ADR 0012 D-EDIT-13), so the browser sent nothing
      for it, and refusing the submission would blame the operator for a
      control they were never allowed to touch. `forms.build_form` itself
      stays untouched -- a caller with no page (the chatbot tool) never
      disabled anything and must keep the schema's own answer.
      `services.fill_engine_blanks` supplies the value before
      `validate_params`, which is still the floor.

      `ignored` is that same map, when the caller already has it: `generate`
      needs it twice in one POST (here, and for the fill), and reading it
      twice would ask the engine adapter the same question twice per
      submission. `None` means "work it out", which is what
      `_picker_failure_response` -- the one caller that has no use for it
      afterwards -- passes.
      """
      from tools.vision.forms import build_form

      form = build_form(
          operation,
          services.live_options(operation, check.resolved),
          data=request.POST,
          files=request.FILES or None,
          stored_keys=stored_keys,
      )
      if ignored is None:
          ignored = services.live_ignored(operation, check.resolved)
      for key in ignored:
          if key in form.fields:
              form.fields[key].required = False
      return form
  ```
  (`form.fields` is a per-instance deepcopy and validation runs lazily in `is_valid()`, so mutating it here — before any caller has asked — is the same thing `build_form`'s own `stored_keys` loop does one line earlier.)
- [ ] In `tools/vision/views.py::generate`, compute the map ONCE and hand it to the form builder (the `refs = stored_input_refs(...)` / `form = build_form_for(...)` pair at :708-709):
  ```python
      refs = stored_input_refs(request, operation)
      # ONE read of this model's ignored-param map per submission: the form
      # needs it to relax `required`, the fill below needs it to know what
      # to supply, and asking the engine adapter twice for the same answer
      # is waste the page can see (`live_ignored` is called per operation).
      ignored = services.live_ignored(operation, check.resolved)
      form = build_form_for(
          request, operation, check, stored_keys=frozenset(refs), ignored=ignored
      )
  ```
- [ ] Insert the fill in `tools/vision/views.py::generate`, replacing the `params = validate_params(...)` line (:737):
  ```python
          files.update(services.resolve_inputs(operation, carried))
          # Back-fill what the page's own DISABLED widgets never submitted
          # (ADR 0012 D-EDIT-13) BEFORE the schema floor sees the dict --
          # `validate_params` refuses a blank `"choice"` regardless of
          # `required`, and an ignored field is one the operator was never
          # allowed to answer.
          params = validate_params(
              operation,
              services.fill_engine_blanks(
                  operation, {**form.cleaned_data, **files}, check.resolved,
                  keys=set(ignored),
              ),
          )
  ```
- [ ] Run the same command. Expected: green.
- [ ] Run the whole generate/services surface:
  `DATABASE_URL=postgres://farabunker:farabunker@localhost:5435/farabunker <repo>/.venv/bin/pytest -q -p no:cacheprovider tools/vision/tests/test_views_generate.py tools/vision/tests/test_services.py tools/vision/tests/test_jobs.py`
  Expected: green.
- [ ] Commit:
  ```
  git add -A && git commit -m "$(cat <<'EOF'
  feat(vision): R2 T3 — back-fill the params a disabled field never submitted

  `services.fill_engine_blanks` supplies an engine-owned `"choice"` value
  for every param `live_ignored` names, between the bound form and
  `validate_params`. `build_form_for` relaxes those same keys to optional,
  because the page is what disabled them; `forms.build_form` is untouched.
  `negative_prompt` is deliberately never filled.

  Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_<id>
  EOF
  )"
  ```

---

### Task 4: `forms.build_constant_form`

The union as a rendering object: every key, one fixed order, three states, never a validator.

**Files**
- Modify `tools/vision/forms.py` (module docstring :1-9; imports :10-15; new `build_constant_form` after `build_form`, :181)
- Modify `tools/vision/tests/test_forms.py` (Test) — `TestSchemaDescriptionsBecomeHints` docstring/name; new `TestConstantFormFields`

**Interfaces**
- Consumes: `forms._field_for`, `models.contracts.operations.{Operation, Param}`. NOT `tools.vision.services` — the union arrives already computed (resolution 3), so this layer keeps no service-layer edge.
- Produces:
  ```python
  def build_constant_form(
      union: Sequence[Param],
      operation: Operation,
      engine_options: dict[str, tuple[str, ...]],
      ignored: dict[str, str],
      initial: dict | None = None,
      stored_keys=frozenset(),
      data=None,
  ) -> forms.Form: ...
  ```
  Every `forms.Field` it returns carries three stamped attributes read by the template — `field.state` (`"enabled"`/`"unused"`/`"ignored"`), `field.reason` (`""` when enabled), `field.wide` (`True` for a `"text"` param) — and one stamped WIDGET attribute, `widget.attrs["aria-describedby"] = "id_<key>-info"`, which is what actually wires the control to its tooltip for a screen reader.

**Steps**

- [ ] Write the failing form tests. In `tools/vision/tests/test_forms.py`, rename `TestSchemaDescriptionsBecomeHints` to `TestSchemaDescriptionsBecomeTooltips` and replace its docstring with:
  ```python
  class TestSchemaDescriptionsBecomeTooltips:
      """The schema already carries a sentence per param, and it is still
      the field's `help_text` -- R2 only moves where that text is SHOWN
      (`vision/_field.html`'s ⓘ tooltip instead of an always-visible
      `.field-hint`). Situational copy still WINS: a seed field's "leave
      blank for a random seed" and an asset field's "nothing installed"
      explain the situation the operator is actually in, which a generic
      description cannot."""
  ```
  (leave its three test bodies unchanged), and append:
  ```python
  class TestConstantFormFields:
      """ADR 0012 D-EDIT-13: one form, every registered operation's params,
      three states. A RENDERING object -- it never validates a submission."""

      OPERATIONS = (TXT2IMG, IMG2IMG, INPAINT, UPSCALE, EDIT)
      UNION = union_params(OPERATIONS)

      def _form(self, operation, ignored=None, **kwargs):
          return build_constant_form(
              self.UNION, operation, CHOICES, ignored or {}, **kwargs
          )

      def test_every_union_key_is_present_in_first_declaration_order(self):
          for operation in self.OPERATIONS:
              form = self._form(operation)
              assert list(form.fields) == [
                  "prompt", "negative_prompt", "width", "height", "steps", "cfg_scale",
                  "seed", "sampler", "scheduler", "batch_size", "loras", "lora_strength",
                  "init_image", "denoise", "mask_image", "mask_grow", "upscale_model",
                  "instruction", "reference_image", "guidance",
              ], operation.key

      def test_the_picked_operations_own_param_renders_an_enabled_field(self):
          """`steps` is 25 under txt2img and 20 under edit; `denoise` is 0.6
          under img2img and 1.0 under inpaint. A field the picked operation
          declares is built from THAT `Param`."""
          assert self._form(TXT2IMG).fields["steps"].initial == 25
          assert self._form(EDIT).fields["steps"].initial == 20
          assert self._form(IMG2IMG).fields["denoise"].initial == 0.6
          assert self._form(INPAINT).fields["denoise"].initial == 1.0

      def test_a_key_the_picked_operation_does_not_declare_is_unused(self):
          field = self._form(EDIT).fields["width"]
          assert field.state == "unused"
          assert field.reason == "Not used by Edit an image."
          assert field.required is False
          assert field.widget.attrs["disabled"] is True

      def test_a_key_the_model_cannot_honour_is_ignored_with_the_engines_words(self):
          form = self._form(TXT2IMG, ignored={"scheduler": "No scheduler input."})
          field = form.fields["scheduler"]
          assert field.state == "ignored"
          assert field.reason == "No scheduler input."
          assert field.required is False
          assert field.widget.attrs["disabled"] is True

      def test_an_ignored_choice_field_still_renders_the_engines_real_options(self):
          """Disabled, but honest: the operator can see WHAT this model
          would have chosen from, which is the point of showing it at all."""
          form = self._form(TXT2IMG, ignored={"scheduler": "No scheduler input."})
          assert form.fields["scheduler"].choices == [
              ("normal", "normal"), ("karras", "karras")
          ]

      def test_everything_else_is_enabled_with_no_reason(self):
          field = self._form(TXT2IMG).fields["prompt"]
          assert field.state == "enabled"
          assert field.reason == ""
          assert field.required is True
          assert "disabled" not in field.widget.attrs

      def test_unused_wins_over_ignored_for_a_param_the_operation_never_declared(self):
          """An engine may only annotate a param the OPERATION declares
          (`services.live_ignored` drops the rest), but the rule is stated
          here too so the two layers cannot disagree."""
          form = self._form(EDIT, ignored={"width": "irrelevant"})
          assert form.fields["width"].state == "unused"

      def test_a_text_param_is_marked_wide_and_nothing_else_is(self):
          form = self._form(TXT2IMG)
          assert form.fields["prompt"].wide is True
          assert form.fields["negative_prompt"].wide is True
          assert form.fields["instruction"].wide is True
          assert form.fields["seed"].wide is False
          assert form.fields["width"].wide is False

      def test_the_schema_sentence_is_still_the_help_text(self):
          width = next(param for param in TXT2IMG.params if param.key == "width")
          assert self._form(TXT2IMG).fields["width"].help_text == width.description

      def test_every_widget_is_wired_to_its_own_tooltip(self):
          """`aria-describedby` belongs on the CONTROL, not only on the ⓘ
          that opens the tooltip: a screen-reader user tabbing into the
          field must hear the description without ever landing on the ⓘ."""
          form = self._form(TXT2IMG)
          assert form.fields["width"].widget.attrs["aria-describedby"] == "id_width-info"
          assert form.fields["prompt"].widget.attrs["aria-describedby"] == "id_prompt-info"

      def test_initial_prefills_an_enabled_field_but_never_a_file_field(self):
          form = self._form(
              IMG2IMG, initial={"prompt": "reuse me", "init_image": "beach.png"}
          )
          assert form.fields["prompt"].initial == "reuse me"
          assert form.fields["init_image"].initial is None

      def test_a_stored_key_makes_its_file_field_optional(self):
          form = self._form(IMG2IMG, stored_keys=frozenset({"init_image"}))
          assert form.fields["init_image"].required is False

      def test_a_bound_constant_form_re_populates_typed_values(self):
          """The non-XHR re-render binds this form to the POST so the
          operator's typing comes back (`views._create_page_response`)."""
          form = self._form(TXT2IMG, data={"prompt": "a lighthouse", "width": "512"})
          assert form["prompt"].value() == "a lighthouse"
          assert form["width"].value() == "512"

      def test_a_bound_form_never_blocks_on_a_field_the_page_disabled(self):
          form = self._form(
              EDIT, ignored={"scheduler": "No scheduler input."},
              data={"instruction": "make it blue"},
          )
          form.is_valid()
          assert "width" not in form.errors
          assert "scheduler" not in form.errors
  ```
  Extend the module's imports to `from models.contracts.operations import EDIT, IMG2IMG, INPAINT, TXT2IMG, UPSCALE, Operation, Param`, `from tools.vision.forms import build_constant_form, build_form`, and `from tools.vision.services import union_params` (the TEST may reach for the service layer; `forms.py` itself must not — resolution 3).
- [ ] Run and watch fail:
  `DATABASE_URL=postgres://farabunker:farabunker@localhost:5435/farabunker <repo>/.venv/bin/pytest -q -p no:cacheprovider tools/vision/tests/test_forms.py`
  Expected: `ImportError: cannot import name 'build_constant_form' from 'tools.vision.forms'`.
- [ ] Implement. Extend `tools/vision/forms.py`'s module docstring with a third paragraph:
  ```
  `build_form` renders ONE operation and is what a submission validates
  against. `build_constant_form` renders the UNION of every registered
  operation (ADR 0012 D-EDIT-13) and never validates anything: it is the
  page's rendering object, so switching modes cannot make a control vanish.
  ```
  and its imports:
  ```python
  from collections.abc import Sequence

  from django import forms
  from django.urls import reverse

  from models.contracts.operations import Operation, Param
  ```
  (NO `tools.vision.services` import: this layer turns a schema into widgets and nothing more, and the union arrives already computed from the view — resolution 3.)
  Then append after `build_form`:
  ```python
  def build_constant_form(
      union: Sequence[Param],
      operation: Operation,
      engine_options: dict[str, tuple[str, ...]],
      ignored: dict[str, str],
      initial: dict | None = None,
      stored_keys=frozenset(),
      data=None,
  ) -> forms.Form:
      """The page's ONE form: every registered operation's params, always,
      with each field marked for how the picked model and the picked
      operation can treat it (ADR 0012 D-EDIT-13).

      `union` gives the SET and the ORDER, already computed by the caller
      (`services.union_params` -- first declaration wins). It is passed IN
      rather than computed here so this module keeps no service-layer
      import: it turns a schema into widgets, and that is all it knows.
      `operation` gives each ENABLED field its
      rendering RULE: `steps` sits where txt2img put it, but under `edit`
      it is `edit`'s own `Param` (default 20, not 25) that builds it. A
      field the picked operation does not declare is built from the union's
      canonical `Param` instead -- it is disabled either way, and a field
      has to be built from something.

      Three states, all of them rendered:

      - `"enabled"`  -- the picked operation declares it and this model
                        honours it;
      - `"unused"`   -- the picked operation does not declare it;
      - `"ignored"`  -- declared, but this model's graph cannot honour it
                        (`services.live_ignored`, the engine's own words).

      The two disabled states carry `widget.attrs["disabled"]`, so the
      BROWSER never submits them, and `required = False`, so nothing about
      a field the operator was not allowed to touch can block the form.
      `views.build_form_for` relaxes the same `ignored` keys on the form
      that actually validates the submission, and
      `services.fill_engine_blanks` supplies their values.

      This form never validates a submission. `data` exists for ONE caller:
      the non-XHR re-render around an invalid POST
      (`views._create_page_response`), which binds it so the operator's
      typing comes back and then copies the real form's errors across.
      """
      declared = {param.key: param for param in operation.params}
      fields: dict[str, forms.Field] = {}
      for canonical in union:
          key = canonical.key
          param = declared.get(key, canonical)
          field = _field_for(param, engine_options)
          if key not in declared:
              state, reason = "unused", f"Not used by {operation.label}."
          elif key in ignored:
              state, reason = "ignored", ignored[key]
          else:
              state, reason = "enabled", ""
          # Read in `vision/_field.html` as `f.field.state`/`f.field.reason`
          # /`f.field.wide`. Stamped on the FIELD, not carried in a parallel
          # context dict, so a field and its state cannot come apart in the
          # template loop.
          field.state = state
          field.reason = reason
          # A prose param gets the full row; everything else shares the
          # grid. Derived from the KIND so the template names no param.
          field.wide = param.kind == "text"
          # The CONTROL is what a screen reader lands on, so the control is
          # what carries the reference to its own description
          # (`vision/_field.html` renders the tooltip under this id). The ⓘ
          # trigger carries it too, for a pointer user hovering the icon.
          field.widget.attrs["aria-describedby"] = f"id_{key}-info"
          if state != "enabled":
              field.required = False
              field.widget.attrs["disabled"] = True
          fields[key] = field

      if initial:
          # Same rule `build_form` states: a `"file"` param's stored value
          # is a basename string and a browser cannot re-send a file from a
          # name, so file fields always start empty.
          file_keys = operation.file_param_keys()
          for key, value in initial.items():
              if key in fields and key not in file_keys:
                  fields[key].initial = value
      for key in stored_keys:
          if key in fields:
              fields[key].required = False

      form_class = type("ConstantGenerationForm", (forms.Form,), fields)
      return form_class(data=data)
  ```
- [ ] Run the same command. Expected: green.
- [ ] Run the whole vision suite so far:
  `DATABASE_URL=postgres://farabunker:farabunker@localhost:5435/farabunker <repo>/.venv/bin/pytest -q -p no:cacheprovider tools/vision foundation/ops/tests/test_column_boundaries.py foundation/ops/tests/test_import_law.py`
  Expected: green (the page still builds the per-operation form; nothing renders the constant one yet).
- [ ] Commit:
  ```
  git add -A && git commit -m "$(cat <<'EOF'
  feat(vision): R2 T4 — build_constant_form renders the union, in three states

  Every registered operation's params, de-duplicated in first-declaration
  order, each field stamped enabled/unused/ignored with the operator-facing
  reason and disabled where it cannot be answered. `build_form` is
  untouched: it is still what a submission validates against.

  Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_<id>
  EOF
  )"
  ```

---

### Task 5: The `_field.html` partial and the ⓘ tooltip

One place writes a field's chrome. `Param.description` stops shouting under every field and becomes hover/focus text that is still in the DOM for a screen reader. No view change yet — this task is pure presentation, and the page still renders the per-operation form.

**Files**
- Create `tools/vision/templates/vision/_field.html`
- Modify `tools/vision/templates/vision/create.html` (`{% block vision_style %}` :4-29; the two field loops :158-180; the picker hint span :105)
- Modify `tools/vision/tests/test_views_create.py` (Test) — replace `TestFieldHintsRender` (:826-843) with `TestInfoTooltips`

**Interfaces**
- Consumes: a Django `BoundField` in the context as `f`; `f.field.state`, `f.field.reason`, `f.field.wide` (all absent on a `build_form` field today, which Django resolves to `""`/falsey — that is what makes this task independently green)
- Produces: `vision/_field.html`, the ONE place a label, its ⓘ trigger, its tooltip, its widget, its inline reason, and its errors are written

**Steps**

- [ ] Write the failing test. In `tools/vision/tests/test_views_create.py`, DELETE `TestFieldHintsRender` (:825-843) and add in its place:
  ```python
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
          """Pure CSS: `:hover` for a mouse, `:focus-within` for a keyboard.
          The page's script block is for polling and the XHR submit, and a
          hint must not depend on either."""
          body = client.get(reverse("vision-create")).content.decode()

          assert ".field label:hover .tip" in body
          assert ".field label:focus-within .tip" in body
  ```
- [ ] Run and watch fail:
  `DATABASE_URL=postgres://farabunker:farabunker@localhost:5435/farabunker <repo>/.venv/bin/pytest -q -p no:cacheprovider tools/vision/tests/test_views_create.py::TestInfoTooltips`
  Expected: four failures — the markup does not exist and `class="muted field-hint"` still does.
- [ ] Create `tools/vision/templates/vision/_field.html`:
  ```html
  {% comment %}
  ONE form field, everywhere the page renders one. Takes `f`, a Django
  BoundField from the constant form (`forms.build_constant_form`).

  Four things, in this order and nowhere else:
    - the label, carrying an ⓘ trigger and the tooltip it describes. The
      description stays in the DOM and the WIDGET below points at it with
      `aria-describedby` (stamped by `forms.build_constant_form`), so a
      screen-reader user tabbing into the control hears it without ever
      landing on the ⓘ; the ⓘ points at it too, for a pointer user. The
      tooltip is shown by CSS alone (`:hover` / `:focus-within`,
      create.html), never by script;
    - the widget itself. A field the picked model or the picked operation
      cannot answer carries HTML `disabled` (stamped on the widget by
      `build_constant_form`), so the BROWSER never submits it;
    - the reason it is disabled, inline and muted -- the tooltip is a
      convenience, and a state the operator cannot change must be legible
      without hovering anything;
    - its errors.

  `f.field.state` / `f.field.reason` / `f.field.wide` are stamped on the
  FIELD by `build_constant_form`, so a field and its state cannot come
  apart in the loop that renders them.
  {% endcomment %}
  <div class="field field--{{ f.field.state }}">
    <label for="{{ f.id_for_label }}">{{ f.label }}
      {% if f.help_text or f.field.reason %}
      <span class="info" tabindex="0" aria-describedby="{{ f.auto_id }}-info">&#9432;</span>
      <span class="tip" id="{{ f.auto_id }}-info" role="tooltip">{{ f.help_text }}{% if f.field.reason %} {{ f.field.reason }}{% endif %}</span>
      {% endif %}
    </label>
    {{ f }}
    {% if f.field.reason %}<span class="muted field-reason">{{ f.field.reason }}</span>{% endif %}
    {{ f.errors }}
  </div>
  ```
- [ ] Rewrite `create.html`'s two field loops (:158-180) to defer to it, keeping today's layout exactly (the two prose fields above, the rest in the grid row) — the `wide` split arrives in Task 6:
  ```html
    <fieldset{% if not operations_supported %} disabled{% endif %}>
    {% for f in form %}
      {% if f.name == "prompt" or f.name == "negative_prompt" %}
        {% include "vision/_field.html" %}
      {% endif %}
    {% endfor %}
    <div class="row">
      {% for f in form %}
        {% if f.name != "prompt" and f.name != "negative_prompt" %}
          {% include "vision/_field.html" %}
        {% endif %}
      {% endfor %}
    </div>
    <div><button type="submit">Generate</button></div>
    </fieldset>
  ```
- [ ] Replace the `.field-hint` CSS (`create.html` :26-28) with the tooltip and field-state rules, using only `_shell.html`'s existing tokens:
  ```css
    /* One field's chrome (`vision/_field.html`). The label is the
       positioning context for its own tooltip, so `.field` is relative and
       the tip is absolute inside it -- nothing shifts the grid when one
       opens. */
    .field { position: relative; }
    .gen-form .field label { display: flex; align-items: baseline; gap: 0.3rem; }
    .info { cursor: help; color: var(--accent); font-size: 0.95em; line-height: 1; }
    .info:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; border-radius: 50%; }
    /* Shown on hover AND on keyboard focus (`:focus-within`, the ⓘ is
       `tabindex="0"`). Kept in the DOM either way and wired with
       `aria-describedby`, so a screen reader never depends on this. */
    .tip {
      position: absolute; left: 0; top: 100%; z-index: 5; width: min(26rem, 90vw);
      margin-top: 0.15rem; padding: 0.5rem 0.6rem; font-size: 0.8rem; line-height: 1.4;
      font-weight: normal; color: var(--text); background: var(--panel);
      border: 1px solid var(--border); border-radius: 6px;
      visibility: hidden; opacity: 0;
    }
    .gen-form .field label:hover .tip,
    .gen-form .field label:focus-within .tip { visibility: visible; opacity: 1; }
    /* A field this model or this mode cannot answer. The widget already
       carries HTML `disabled`; this is what makes that legible. */
    .field--unused > label, .field--ignored > label { color: var(--muted); }
    .field--unused input, .field--unused select, .field--unused textarea,
    .field--ignored input, .field--ignored select, .field--ignored textarea {
      opacity: 0.55; cursor: not-allowed;
    }
    /* The reason, inline under its own field -- a block so a sentence sits
       under the control instead of colliding with the next label. */
    .field-reason { display: block; margin-top: 0.25rem; line-height: 1.35; }
  ```
- [ ] Change the picker's own hint span (`create.html` :105) from `<span class="field-hint">` to `<span class="muted">` — `.field-hint` no longer exists, and this sentence was never a schema hint.
- [ ] Run the same command. Expected: green.
- [ ] Run the page suite:
  `DATABASE_URL=postgres://farabunker:farabunker@localhost:5435/farabunker <repo>/.venv/bin/pytest -q -p no:cacheprovider tools/vision/tests/test_views_create.py tools/vision/tests/test_views_gallery.py tools/vision/tests/test_views_queue.py models/registry/tests/test_template_comments.py`
  Expected: green. (`test_template_comments` is the repo-wide sweep that requires every template to open with a `{% comment %}` block — `_field.html` does.)
- [ ] Commit:
  ```
  git add -A && git commit -m "$(cat <<'EOF'
  feat(vision): R2 T5 — one field partial, and the schema sentence becomes a tooltip

  `vision/_field.html` is now the only place a field's label, ⓘ trigger,
  widget, inline reason, and errors are written. `Param.description` moves
  from an always-visible `.field-hint` into a pure-CSS hover/focus tooltip
  that stays in the DOM and is wired with `aria-describedby`.

  Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_<id>
  EOF
  )"
  ```

---

### Task 6: The page renders the union, and the Operation select replaces the tabs

The narrowing comes out: no merged tab, no vanishing modes, no operation-shaped `<h1>`. The page shows every field and every mode, and says why each disabled thing is disabled.

**Files**
- Modify `tools/vision/views.py` — imports (:31-33); DELETE the merge block `_MERGED_OPERATION_KEYS`/`_IMAGE_TO_IMAGE_LABEL`/`_merge_image_to_image`/`_display_label`/`page_tabs` (its comment opens at :108, the block runs to :184); `_create_page_context` (:418-463); `CreatePageView.get_context_data` (:471-509); `input_targets` (:512-538); `_create_page_response` (:1053-1061)
- Modify `tools/vision/templates/vision/create.html` — `<h1>` (:31), the tab nav (:33-48), `.op-chooser` CSS (:13-15), the picker block (:83-107 — the `{% if connection_options %}` at :83, its comment :84-95, the form :96-106, the `{% endif %}` at :107), the field loops, the script's picker handler (:245-250)
- Modify `tools/vision/tests/test_views_create.py` (Test) — delete `TestImageToImageMerge` (:976-1079); rewrite `TestOperationSurface` (:264-325), `TestSecondModeArrivesByRegistrationAlone` (:521-541), `TestQwenImageFamilyPageAndPicker` (:1081-1124), `TestUpscalePage` (:747-763); extend `TestOperationsSupportsNothingBanner` (:1126-1181) and `TestInfoTooltips`; adapt two `TestModelPicker` tests (:951-960, :889-908); add `TestConstantForm` and `TestFieldStates`
- Modify `tools/vision/README.md` — "### The model picker" (:664), its **One "Image to image" tab, not two** subsection (:691-707), the adding-an-operation checklist item 4 (:108-121, which claims the merged tab and `/vision/op/<key>/`), and the merged-tab paragraph under "The chooser is not the registry" (:270-273)

**Interfaces**
- Consumes: `services.operation_states`, `services.live_ignored`, `services.live_options`, `services.live_defaults`, `forms.build_constant_form`, `models.contracts.operations.operations_for`
- Produces (context contract for `create.html`): `operation`, `operations`, `operation_states`, `operations_supported`, `preflight`, `form`, `connection_options`, `selected_connection`, `stored_inputs`, `jobs`, `input_targets`, `queued_card` — 12 keys. `page_title` and `operation_tabs` are GONE. `operations` and `operations_supported` are DERIVED from the one `operation_states` call, not from a second `page_operations` call: `operation_states` already asks `operations_for_model` (which health-checks nothing but does reach the engine adapter), and one render must not ask the same question twice. `page_operations` itself stays — `generate`, `input_targets`, and `get_context_data` still call it.
- Removed: `views.page_tabs`, `views._display_label`, `views._merge_image_to_image`, `views._MERGED_OPERATION_KEYS`, `views._IMAGE_TO_IMAGE_LABEL`

**Steps**

- [ ] Write the failing tests. In `tools/vision/tests/test_views_create.py`:
  - DELETE the whole `TestImageToImageMerge` class (:975-1079). Owner ruling 2026-08-24(a) is superseded by D-EDIT-13: `img2img` and `edit` are two operations again, named by their own schema labels.
  - REPLACE `TestOperationSurface` (:264-325) with:
    ```python
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
    ```
  - REPLACE `TestSecondModeArrivesByRegistrationAlone` (:520-541) with:
    ```python
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
    ```
  - REPLACE `TestUpscalePage` (:746-763) with:
    ```python
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
    ```
  - REPLACE `TestQwenImageFamilyPageAndPicker::test_a_qwen_image_pick_merges_its_tab_...` (:1087-1124) with a version named `test_a_qwen_image_pick_selects_edit_and_disables_every_other_mode`, keeping the connection/engine setup verbatim and replacing the assertion block with:
    ```python
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
    ```
  - EXTEND `TestOperationsSupportsNothingBanner::test_a_model_that_supports_nothing_shows_the_banner_and_disables_the_form` with:
    ```python
            # Every option disabled, each with its own reason -- the banner
            # says the model can run nothing, the select says which nothings.
            for key in ("txt2img", "img2img", "inpaint", "upscale", "edit"):
                assert f'value="{key}" disabled' in body
            assert "unknown_family" in body
    ```
  - ADAPT `TestModelPicker::test_the_picked_model_drives_the_banner_and_the_chooser` (:889-908) — keep every assertion, add `assert [state.operation.key for state in response.context["operation_states"] if state.supported] == ["edit"]`. Rename it `test_the_picked_model_drives_the_banner_and_the_select`.
  - REWRITE `TestModelPicker::test_every_chooser_link_carries_the_picked_model` (:951-960). Its `assert f"connection={connection.pk}" in body` passes TODAY only because of the chooser nav's `?connection=` hrefs, which this task deletes — the test creates no job and no output, so no `_output_actions.html` link exists either, and the hidden field renders `name="connection" value="3"`, which does not contain `connection=3`. Rename it `test_the_picked_model_travels_on_the_page` and assert the carrier that actually survives:
    ```python
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
    ```
  - ADD, at the end of the file:
    ```python
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
            assert 'class="field field--ignored"' in body

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
    ```
  - EXTEND `TestInfoTooltips` with:
    ```python
        def test_a_disabled_fields_reason_is_in_its_tooltip_and_inline(self, client):
            """The tooltip is a convenience; a state the operator cannot
            change must be legible without hovering anything."""
            response = client.get(reverse("vision-create"), {"operation": "upscale"})
            body = response.content.decode()

            assert '<span class="muted field-reason">Not used by Upscale.</span>' in body
            assert 'role="tooltip">' in body
            assert body.count("Not used by Upscale.") >= 2  # tooltip body + inline line
    ```
- [ ] Run and watch fail:
  `DATABASE_URL=postgres://farabunker:farabunker@localhost:5435/farabunker <repo>/.venv/bin/pytest -q -p no:cacheprovider tools/vision/tests/test_views_create.py`
  Expected: the new/rewritten classes fail (no `<select id="operation">`, `context["operation_states"]` missing, `form.fields` still the per-operation set, `<h1>Text to image</h1>`).
- [ ] Delete the merge machinery in `tools/vision/views.py`: remove `_MERGED_OPERATION_KEYS`, `_IMAGE_TO_IMAGE_LABEL`, `_merge_image_to_image`, `_display_label`, `page_tabs` (the block's own explanatory comment opens at :108 and the whole run ends at :184), and drop `EDIT, IMG2IMG` from the `models.contracts.operations` import (:31-33) — nothing else references them.
- [ ] Rewrite `_create_page_context` (:418-463). Replace its `available = page_operations(check.resolved)` line with ONE `operation_states` call and derive the other two keys from it:
  ```python
      # THREE readings of one answer, from one call. `operation_states`
      # already asks `operations_for_model` for the whole registry, so the
      # narrowed list and the supports-nothing fact are readings of that
      # same answer rather than a second identical question. (`get_context_
      # data` above still asks once of its own, to resolve WHICH operation
      # the request names before this runs -- that one is a different
      # question at a different moment, and collapsing the two is not this
      # task's business.)
      states = services.operation_states(check.resolved)
      available = [state.operation for state in states if state.supported]
  ```
  then, in the returned dict: delete the `page_title` and `operation_tabs` entries, keep `"operations": available` and `"operations_supported": bool(available)` exactly as they are, and add beside them:
  ```python
          # Every REGISTERED operation, each marked supported or not for the
          # selected model, with the reason (ADR 0012 D-EDIT-13). The
          # Operation select renders this; `operations` above is the same
          # answer narrowed to what this model can actually run, which is
          # what `input_targets` and the honest banner read.
          "operation_states": states,
  ```
  and update the docstring's "own 13 context keys" to "own 12 context keys".
- [ ] Rewrite `CreatePageView.get_context_data` (:471-509):
  ```python
      def get_context_data(self, **kwargs):
          from tools.vision.forms import build_constant_form

          context = super().get_context_data(**kwargs)
          # The path alias first (`op/<key>/`), then the canonical query
          # parameter the Operation select produces -- a GET form can only
          # ever emit a query key, never a path segment (spec section 4).
          operation_key = kwargs.get("operation_key") or self.request.GET.get("operation")
          reuse_job = _reuse_job(self.request)

          picked, _raw_connection, _usable = picked_connection(self.request)
          check = services.preflight(picked)
          available = page_operations(check.resolved)

          if not operation_key and reuse_job is not None and get_operation(reuse_job.operation):
              # The URL names no operation of its own -- honor the reused
              # job's operation instead of silently falling back to the
              # default. A URL-named operation always wins over this; an
              # unregistered `job.operation` (feature since switched off,
              # operation renamed) also falls back to the default, honestly.
              operation_key = reuse_job.operation
          operation = resolve_page_operation(operation_key, available)

          form = build_constant_form(
              # The REGISTRY, not `available`: the field list is constant
              # (ADR 0012 D-EDIT-13), so a narrowing model changes which
              # fields are ENABLED and never which exist. The union is
              # computed HERE and handed over -- `forms` owns no
              # service-layer import (resolution 3).
              services.union_params(operations_for(IMAGE_GENERATION_CAPABILITY)),
              operation,
              services.live_options(operation, check.resolved),
              services.live_ignored(operation, check.resolved),
              initial={
                  **services.live_defaults(operation, check.resolved),
                  **_reuse_initial(reuse_job),
              },
              stored_keys=frozenset(
                  item["param_key"]
                  for item in _stored_input_context(
                      stored_input_refs(self.request, operation), operation
                  )
              ),
          )
          context.update(_create_page_context(self.request, operation, check, form))
          return context
  ```
- [ ] Rewrite `input_targets` (:512-538) — no merge, no `_display_label`, still narrowed to what the selected model SUPPORTS (a gallery link that lands on a disabled operation is a dead end). The URL form changes in Task 7; leave `reverse("vision-create-operation", ...)` here:
  ```python
  def input_targets(resolved: ResolvedModel | None = None) -> list[dict]:
      """Every mode an existing image can be fed into, for the SELECTED
      model (`resolved`) -- or the full registry with none selected, exactly
      as before. Each target pre-fills the operation's FIRST file param
      (`Operation.file_params()`): inpaint's image, leaving its mask for the
      operator to choose.

      Narrowed to SUPPORTED operations on purpose, unlike the Operation
      select beside it: the select shows an unsupported mode disabled
      because the operator is standing there choosing, while a "Use in ..."
      link that lands on a mode this model cannot run is simply a dead end.

      `img2img` and `edit` are two entries again, under their own schema
      labels (ADR 0012 D-EDIT-13 retires the merged one) -- a resolved model
      narrows to at most one of them anyway (D-EDIT-5), and the page that
      can genuinely carry both (nothing selected, nothing bound) should say
      so rather than pick one silently.
      """
      targets = []
      for operation in page_operations(resolved):
          params = operation.file_params()
          if not params:
              continue
          targets.append(
              {
                  "label": operation.label,
                  "param_key": params[0].key,
                  "url": reverse("vision-create-operation", args=[operation.key]),
              }
          )
      return targets
  ```
- [ ] Rewrite `_create_page_response` (:1053-1061) so the re-render around an invalid POST hands the template a CONSTANT form carrying the operator's typing:
  ```python
  def _create_page_response(request, operation: Operation, check, form, status: int):
      """Re-render the create page around an invalid or refused submission.

      `form` is the BOUND per-operation form that actually validated
      (`build_form_for`); the page renders the CONSTANT one (ADR 0012
      D-EDIT-13), so this builds that form bound to the same POST -- the
      operator's typing comes back -- and copies the real form's errors
      across, attaching one to `None` when the union has no such field. The
      same trick `_picker_failure_response` already uses for its own
      non-field refusal, and `{{ f.errors }}` in the template needs no
      change.
      """
      from tools.vision.forms import build_constant_form

      refs = stored_input_refs(request, operation)
      page_form = build_constant_form(
          services.union_params(operations_for(IMAGE_GENERATION_CAPABILITY)),
          operation,
          services.live_options(operation, check.resolved),
          services.live_ignored(operation, check.resolved),
          stored_keys=frozenset(refs),
          data=request.POST,
      )
      # Bound, so `add_error` below has a `cleaned_data` to check against --
      # which also means this form has ALREADY produced its own message for
      # every required field the POST left empty. Copying the operation
      # form's identical message on top would show the operator "This field
      # is required." twice under one input, so only what the union did not
      # already say is copied across.
      page_form.is_valid()
      for key, messages in form.errors.items():
          target = key if key in page_form.fields else None
          existing = page_form.errors.get(target or "__all__", [])
          for message in messages:
              if message not in existing:
                  page_form.add_error(target, message)
      return render(
          request,
          "vision/create.html",
          _create_page_context(request, operation, check, page_form),
          status=status,
      )
  ```
- [ ] Rewrite `create.html`'s header. Replace the `<h1>` (:31) and the whole tab-nav block (:33-48) with:
  ```html
  <h1>Image generation</h1>
  ```
  and delete the `.op-chooser` CSS (:13-15).
- [ ] Rewrite the picker block (`create.html` :83-107) so the Operation select joins it. The OUTER `{% if connection_options %}` (:83) and `{% endif %}` (:107) go, along with the comment block at :84-95 (replaced below): the form itself must render unconditionally, because the mode is pickable whether or not there is more than one model to pick from, and the `{% if %}` survives only around the Model label inside it:
  ```html
  {% comment %}
  ONE little GET form for both choices the header offers: which model runs
  this generation, and which mode it runs. A SEPARATE form from the generate
  form on purpose -- that one is `enctype="multipart/form-data"` and carries
  the operator's attached image and typed instruction, and submitting THAT
  as a GET would drop the file outright. Switching must not cost the
  operator their work, so switching is its own GET and the generate form
  learns both choices from hidden inputs below.

  A GET form can only ever produce a QUERY parameter, never a path segment,
  which is why `?operation=` is the canonical URL and `op/<key>/` is kept
  only as a rendering alias (ADR 0012 D-EDIT-13).

  Every registered operation is an option, always. One this model has no
  graph for is `disabled` and carries its reason, so a mode never simply
  vanishes -- `views.page_tabs` used to hide it and say nothing.
  {% endcomment %}
  <form class="model-picker" method="get" action="{% url 'vision-create' %}">
    {% if connection_options %}
    <label for="connection">Model
      <select id="connection" name="connection" data-reload-on-change>
        {% for opt in connection_options %}
        <option value="{{ opt.value }}"{% if opt.selected %} selected{% endif %}>{{ opt.label }}</option>
        {% endfor %}
      </select>
    </label>
    {% endif %}
    <label for="operation">Mode
      <select id="operation" name="operation" data-reload-on-change>
        {% for state in operation_states %}
        {% comment %}
        `disabled` BEFORE `selected`, deliberately: the supports-nothing case
        renders the fallback operation as both, and a test (or a reader)
        looking for `value="txt2img" disabled` must find the two adjacent.
        {% endcomment %}
        <option value="{{ state.operation.key }}"{% if not state.supported %} disabled title="{{ state.reason }}"{% endif %}{% if state.operation.key == operation.key %} selected{% endif %}>{{ state.operation.label }}{% if not state.supported %} — {{ state.reason }}{% endif %}</option>
        {% endfor %}
      </select>
    </label>
    <button type="submit" id="use-model">Use these</button>
    <span class="muted">The model this generation runs on, and the mode it runs. The form below follows both.</span>
  </form>
  ```
  Adjust `.model-picker label { display: block; ... }` to keep the two labelled selects side by side (`.model-picker label { display: flex; flex-direction: column; gap: 0.2rem; flex: 1 1 16rem; }`, and `.model-picker select { width: 100%; }`).
- [ ] Switch the field loops to the `wide` split (no param named in the template any more):
  ```html
    <fieldset{% if not operations_supported %} disabled{% endif %}>
    {% for f in form %}{% if f.field.wide %}{% include "vision/_field.html" %}{% endif %}{% endfor %}
    <div class="row">
      {% for f in form %}{% if not f.field.wide %}{% include "vision/_field.html" %}{% endif %}{% endfor %}
    </div>
    <div><button type="submit">Generate</button></div>
    </fieldset>
  ```
- [ ] Teach the existing picker handler about both selects (`create.html` :245-250) — a change to the one handler that already exists, not new JavaScript:
  ```javascript
    // Progressive enhancement ONLY. Without JS the operator changes a
    // select and presses the button; the same GET happens either way. Both
    // selects (model and mode) live in the one picker form, so both submit
    // it -- the button is hidden only once we know we can do it for them.
    var pickers = document.querySelectorAll('[data-reload-on-change]');
    var useModel = document.getElementById('use-model');
    if (pickers.length && useModel && pickers[0].form) {
      useModel.hidden = true;
      Array.prototype.forEach.call(pickers, function (picker) {
        picker.addEventListener('change', function () { picker.form.submit(); });
      });
    }
  ```
- [ ] Update `tools/vision/README.md` — four places, all of them describing behaviour this task changes:
  1. The adding-an-operation checklist, item 4 (:108-121): drop the "`img2img`/`edit` collapse into one 'Image to image' tab presentation-only (owner ruling 2026-08-24(a)) whenever a selected model narrows to exactly one of the pair" clause, and change "`/vision/op/<key>/` serves that operation's form" to "`/vision/?operation=<key>` serves that operation's form (`op/<key>/` still works as an alias)". Also change "the page's operation chooser" to "the page's Operation select" and add, after "NARROWED to what the selected model's engine reports it can run (Task 10)": "— narrowed to which options are ENABLED, never to which exist (ADR 0012 D-EDIT-13)".
  2. The merged-tab paragraph under "The chooser is not the registry" (:270-273, "`img2img` and `edit` never appear together … see 'The model picker' … below."): replace it with "`img2img` and `edit` never appear together in what a SELECTED model offers (a checkpoint family offers one, an edit family the other, never both), so at most one of them is ever an enabled option — but both are always LISTED, each under its own schema label, the unavailable one disabled with its reason (ADR 0012 D-EDIT-13)."
  3. Retitle "### The model picker" (:664) to "### The model picker and the operation select", and add after its third paragraph:
  ```
  Beside the Model select sits an **Operation select**, in the same little
  GET form, listing EVERY registered mode. One this model's engine has no
  graph for is a `disabled` option carrying its reason ("No inpaint graph
  for the flux2 family."), never an entry that quietly disappears. Because
  a GET form can only produce a query parameter, `?operation=<key>` is the
  canonical URL and `op/<key>/` is kept as a rendering alias.
  ```
  4. REPLACE the whole **One "Image to image" tab, not two.** subsection (:691-707) with:
  ```
  **One constant form.** The page renders the UNION of every registered
  operation's params — the same 20 fields, in the same order, whichever mode
  is picked (`services.union_params` → `forms.build_constant_form`). A field
  is `enabled`, `unused` (this mode does not declare it: "Not used by
  Upscale."), or `ignored` (this model's graph cannot honour it, in the
  engine's own words via `services.live_ignored`). The two disabled states
  carry HTML `disabled`, so the browser never submits them, and
  `required=False`, so nothing an operator was not allowed to answer can
  block the form; `services.fill_engine_blanks` supplies an ignored
  `"choice"` param's value before `validate_params` sees it. Narrowing the
  form to the picked mode is what R2 removed: it read as lost function, and
  a control that vanishes explains nothing. `img2img` and `edit` are two
  operations again, under their own schema labels — the merged "Image to
  image" entry is retired (ADR 0012 D-EDIT-13). A job's card headline is
  unchanged: an `edit` job's heading is its instruction.
  ```
- [ ] Run the page suite:
  `DATABASE_URL=postgres://farabunker:farabunker@localhost:5435/farabunker <repo>/.venv/bin/pytest -q -p no:cacheprovider tools/vision/tests/test_views_create.py`
  Expected: green.
- [ ] Run everything the page touches:
  `DATABASE_URL=postgres://farabunker:farabunker@localhost:5435/farabunker <repo>/.venv/bin/pytest -q -p no:cacheprovider tools/vision models/registry/tests/test_template_comments.py foundation`
  Expected: green. Two known follow-ons to fix HERE if they fail: `test_views_gallery.py::TestUseInLinks` and `test_views_create.py::TestCardActionParity` now see `edit`'s own "Use in Edit an image" link alongside "Use in Image to image" — assert both, do not re-merge them.
- [ ] Commit:
  ```
  git add -A && git commit -m "$(cat <<'EOF'
  feat(vision): R2 T6 — one constant form and an Operation select, no more narrowing

  The page renders the union of every registered operation's params, always,
  each field enabled/unused/ignored with its reason; every registered mode is
  an option in the picker's GET form, the unsupported ones disabled and
  explained. Retires `_merge_image_to_image`/`_display_label`/`page_tabs` and
  the operation-shaped `<h1>`: `img2img` and `edit` are two modes again.

  Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_<id>
  EOF
  )"
  ```

---

### Task 7: `?operation=` becomes the canonical URL, `op/<key>/` an alias

Every link the page generates now says `?operation=`. The path route keeps rendering identically, so nothing bookmarked breaks.

**Files**
- Modify `tools/vision/views.py` — `_create_url` (:792-810), `generate`'s redirect (:785-789), `input_targets`'s `url`
- Modify `tools/vision/templates/vision/_output_actions.html` (:29 and its context comment)
- Modify `tools/vision/urls.py` (:11-15, the `op/<key>/` comment)
- Modify `tools/vision/tests/test_views_generate.py` (Test) — SIX assertions, every one of them broken by `_create_url`'s new shape or by `input_targets`': the redirect assertions at :141, :387, :398-399, :824 and :834, and the `_output_actions.html` link assertions at :620-621
- Modify `tools/vision/tests/test_views_create.py` (Test) — `TestCardActionParity` (:475-508), `TestStoredInputPrefill` (:654-684)
- Modify `tools/vision/tests/test_views_gallery.py` (Test) — `TestUseInLinks` (:292-310), `test_use_in_links_follow_the_role_bound_model_not_registry_order` (:131-159)
- Modify `tools/vision/README.md` — "## The poll-driven page and its no-JS fallback" (:633) and "## Feeding an image back in" (:718)

**Interfaces**
- Consumes: `django.urls.reverse("vision-create")`
- Produces: `views._create_url(operation, connection="") -> str` — always `"/vision/?operation=<key>"`, plus `&connection=<pk>` when one is picked; `views.input_targets()[i]["url"] == "/vision/?operation=<key>"`
- Unchanged: the `vision-create-operation` route and its name — `CreatePageView` still serves it, no redirect

**Steps**

- [ ] Write the failing tests.
  - In `tools/vision/tests/test_views_generate.py`, update SIX assertions. Every no-JS redirect now names its operation, so `?queued=` is never the first query key again. THREE of these are the byte-identical line `assert response["Location"] == f"{reverse('vision-create')}?queued=12"` (:141, :387, :834) — edit them by line number, not by search-and-replace:
    ```python
    # :141 (TestGenerate) and :387 -- both are txt2img submissions
            assert response["Location"] == (
                f"{reverse('vision-create')}?operation=txt2img&queued=12"
            )

    # :398-399 (test_a_plain_post_for_a_named_operation_redirects_to_that_operation)
            assert response["Location"] == (
                f"{reverse('vision-create')}?operation=img2img&queued=12"
            )
            # (the `expected = reverse("vision-create-operation", ...)` line above it goes)

    # :620-621 (the poll fragment's own "Use in ..." links, TestJobStatus)
            assert f"{reverse('vision-create')}?operation=edit" in body
            assert f"{reverse('vision-create')}?operation=img2img" not in body

    # :824 (TestOperationFromThePost, the img2img no-JS redirect)
            assert response["Location"] == (
                f"{reverse('vision-create')}?operation=img2img&queued=12"
            )

    # :834 (TestOperationFromThePost, the default operation's redirect)
            assert response["Location"] == (
                f"{reverse('vision-create')}?operation=txt2img&queued=12"
            )
    ```
    Rename the :834 test to `test_a_no_js_post_names_its_operation_in_the_redirect` and drop its "The default operation's URL stays `vision-create`, unchanged." docstring — that distinction is gone; every redirect now names its operation, and the default one is no longer special.
  - In `tools/vision/tests/test_views_create.py::TestCardActionParity::test_a_recent_card_offers_every_gallery_action`, replace the URL assertion with:
    ```python
            assert (
                f"{reverse('vision-create')}?operation=img2img"
                f"&input_init_image=output:{output.id}"
            ) in body
    ```
  - In `tools/vision/tests/test_views_gallery.py::TestUseInLinks::test_the_gallery_offers_every_file_taking_mode_by_registration_alone`, replace `expected` with:
    ```python
            expected = (
                f"{reverse('vision-create')}?operation=img2img"
                f"&input_init_image=output:{output.id}"
            )
    ```
  - In `tools/vision/tests/test_views_gallery.py::test_use_in_links_follow_the_role_bound_model_not_registry_order`, replace the two assertions with the query form so they stay meaningful rather than trivially true:
    ```python
            assert f"{reverse('vision-create')}?operation=edit" in body
            assert f"{reverse('vision-create')}?operation=img2img" not in body
    ```
  - Add to `tools/vision/tests/test_views_create.py::TestStoredInputPrefill`:
    ```python
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
    ```
    (leave the two existing `op/<key>/` tests exactly as they are — they are now the alias's own regression cover.)
- [ ] Run and watch fail:
  `DATABASE_URL=postgres://farabunker:farabunker@localhost:5435/farabunker <repo>/.venv/bin/pytest -q -p no:cacheprovider tools/vision/tests/test_views_generate.py::TestOperationFromThePost tools/vision/tests/test_views_create.py::TestCardActionParity tools/vision/tests/test_views_create.py::TestStoredInputPrefill tools/vision/tests/test_views_gallery.py`
  Expected: the redirects still say `/vision/op/img2img/?queued=12` and the links still say `/vision/op/img2img/?input_...`.
- [ ] Rewrite `_create_url` (:792-810):
  ```python
  def _create_url(operation: Operation, connection: str = "") -> str:
      """The create page's URL for `operation`, carrying the picked model.

      ONE shape for every generated link: `/vision/?operation=<key>`, plus
      `&connection=<pk>` when a model is picked. The pick and the mode both
      live in the query string, not a session, so a link is a complete
      description of what the page will show -- which is what makes the
      Operation select, the redirect after a submission, and a bookmark all
      agree.

      The query form is canonical because the Operation select is a control
      inside the picker's GET form, and a GET form can only ever produce a
      query parameter (ADR 0012 D-EDIT-13). `op/<key>/` stays routed to the
      same view and renders identically, so every previously issued link
      keeps working -- it is simply not what anything emits any more.
      """
      url = f"{reverse('vision-create')}?operation={operation.key}"
      return f"{url}&connection={connection}" if connection else url
  ```
  and simplify `generate`'s redirect (:785-789) to the one branch that is now needed:
  ```python
      return redirect(f"{_create_url(operation, raw_connection)}&queued={queue_job_id}")
  ```
- [ ] Point `input_targets`'s `url` at the same builder:
  ```python
                  "url": f"{reverse('vision-create')}?operation={operation.key}",
  ```
- [ ] Update `_output_actions.html` (:29) to join with `&`, since the URL already carries a query string:
  ```html
    <a href="{{ target.url }}&input_{{ target.param_key }}=output:{{ output.id }}{% if selected_connection %}&connection={{ selected_connection }}{% endif %}">Use in {{ target.label }}</a>
  ```
  and add one line to its context comment, under `input_targets`:
  ```
                         Each `url` already carries `?operation=<key>` (the
                         canonical form, ADR 0012 D-EDIT-13), so everything
                         appended here joins with `&`.
  ```
- [ ] Update the `op/<slug:operation_key>/` comment in `tools/vision/urls.py` (:11-14):
  ```python
      # A RENDERING ALIAS, kept for links issued before `?operation=` became
      # canonical (ADR 0012 D-EDIT-13). Same view, same page, no redirect and
      # no extra round trip -- `CreatePageView` reads this path segment first
      # and `?operation=` second. Nothing the page generates points here any
      # more; `generate` still reads the same key out of its POST, so an
      # operation is identified the same way whether the operator navigated
      # or submitted.
  ```
- [ ] Add the URL note to `tools/vision/README.md`, at the end of "## The poll-driven page and its no-JS fallback" (before "### The model picker and the operation select"):
  ```
  **The page's URL.** `/vision/?operation=<key>&connection=<pk>` is the
  canonical form: both choices live in the query string, never a session, so
  a link is a complete description of what the page will show — and the
  Operation select can produce it, which a path segment could not (it is a
  control in a GET form). `/vision/op/<key>/` is kept as a rendering alias:
  same view, same page, no redirect, so links issued before this change keep
  working. A path segment wins over `?operation=` when both are present.
  ```
  and update "## Feeding an image back in" (:718-722): the link is now
  `/vision/?operation=<key>&input_<param>=output:<id>`.
- [ ] Run the same command. Expected: green.
- [ ] Sweep for any remaining generated `op/<key>/` link and confirm each hit is either the alias route itself or a deliberate alias test:
  `grep -rn "vision-create-operation" tools/vision --include='*.py' --include='*.html'`
  Expected hits: `urls.py` (the route), `test_views_create.py::TestOperationSurface` (alias parity), `test_views_create.py::TestStoredInputPrefill` (the two alias tests). Nothing in `views.py` except `input_targets`… which this task just changed — so `views.py` should have NO `vision-create-operation` left.
- [ ] Run the whole module:
  `DATABASE_URL=postgres://farabunker:farabunker@localhost:5435/farabunker <repo>/.venv/bin/pytest -q -p no:cacheprovider tools/vision`
  Expected: green.
- [ ] Commit:
  ```
  git add -A && git commit -m "$(cat <<'EOF'
  feat(vision): R2 T7 — ?operation= is the canonical URL, op/<key>/ an alias

  Every link the page generates now names its mode in the query string, which
  is the only thing the Operation select's GET form can produce. The path
  route still renders the identical page, with no redirect, so links issued
  before this keep working.

  Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_<id>
  EOF
  )"
  ```

---

### Task 8: `tools/vision/tools.py` adopts the shared fill

One function, two callers. The tool keeps its own key set and its own `ToolRefused` guard; only the filling itself moves.

**Files**
- Modify `tools/vision/tools.py` (`_fill_engine_params` :320-375; module docstring's engine-param paragraph :79-84)
- Modify `tools/vision/tests/test_tools.py` (Test) — new class

**Interfaces**
- Consumes: `services.fill_engine_blanks(operation, params, resolved, keys=...)`, `services.preflight`, `services.union_params` (in the test only)
- Produces: no new public surface. `_fill_engine_params(operation, raw_params) -> dict` behaviour is unchanged, including its `ToolRefused`.
- INVARIANT this task must not break: `tools/vision/tools.py`'s module-scope imports stay stdlib + `agents.contracts.*` + `models.contracts.*`. `services` is imported inside the function body, exactly as it is today.
- SECOND INVARIANT: `tools/vision/tests/test_tools.py` carries `override_settings(FARABUNKER_FEATURES=frozenset())` at :97, and `tools/rag/tests/test_flag_hygiene.py`'s sweep is FILE-scoped — so nothing added to that file may contain `reverse(` or `Client(`. Neither test below does; keep it that way (build the spec and call the runner directly, never through a URL).

**Steps**

- [ ] Write the failing anti-drift test. Append to `tools/vision/tests/test_tools.py`:
  ```python
  class TestGenerateSpecFollowsTheServiceUnion:
      """`build_generate_spec` and `services.union_params` compute the SAME
      union (every operation's params, de-duplicated by key, first
      declaration wins). They are not one function on purpose:
      `VisionConfig.ready()` calls `build_generate_spec()`, and this module
      must not import the service layer at registration time (its own
      docstring; `foundation/ops/tests/test_column_boundaries.py`). This
      test is what keeps the two from drifting instead."""

      def test_the_tool_schema_is_the_service_union_minus_the_collapsed_file_key(self):
          from models.contracts.operations import all_operations
          from tools.vision import services

          operations = all_operations()
          union = [param.key for param in services.union_params(operations)]
          # Every operation's FIRST file param collapses onto the shared
          # `image` param (this module's own docstring); every other param
          # keeps its own key.
          collapsed = {
              operation.file_params()[0].key
              for operation in operations
              if operation.file_params()
          }
          expected = {key for key in union if key not in collapsed}

          actual = {
              param.key for param in build_generate_spec().params
              if param.key not in ("operation", "image")
          }
          assert actual == expected

      def test_the_shared_image_param_stands_in_for_the_collapsed_one(self):
          keys = [param.key for param in build_generate_spec().params]
          assert "image" in keys
          assert "init_image" not in keys
  ```
- [ ] Write the failing delegation test. Append to the same file:
  ```python
  class TestFillEngineParamsDelegates:
      """`_fill_engine_params` fills every blank ENGINE-OWNED choice param,
      not only the ones the model ignores -- a tool caller may simply have
      omitted `sampler`. It does that through the same
      `services.fill_engine_blanks` the page uses, with its own key set."""

      def test_it_calls_the_shared_filler_with_its_own_wider_key_set(self):
          from models.contracts.operations import TXT2IMG
          from tools.vision import tools as vision_tools

          check = SimpleNamespace(ready=True, resolved=object(), message="")
          with patch("tools.vision.services.preflight", return_value=check), \
               patch(
                   "tools.vision.services.fill_engine_blanks",
                   return_value={"sampler": "euler", "scheduler": "normal"},
               ) as mock_fill:
              filled = vision_tools._fill_engine_params(
                  TXT2IMG, {"prompt": "x", "sampler": "", "scheduler": ""}
              )

          assert filled == {"sampler": "euler", "scheduler": "normal"}
          _operation, _params, resolved = mock_fill.call_args.args
          assert resolved is check.resolved
          assert mock_fill.call_args.kwargs["keys"] == {"sampler", "scheduler"}
  ```
  Add `from types import SimpleNamespace` and `from unittest.mock import patch` to the module's imports if they are not already there.
- [ ] Run and watch fail:
  `DATABASE_URL=postgres://farabunker:farabunker@localhost:5435/farabunker <repo>/.venv/bin/pytest -q -p no:cacheprovider tools/vision/tests/test_tools.py::TestGenerateSpecFollowsTheServiceUnion tools/vision/tests/test_tools.py::TestFillEngineParamsDelegates`
  Expected: the union test passes already (it is a pin, not a change) and the delegation test fails — `fill_engine_blanks` is never called.
- [ ] Rewrite the tail of `_fill_engine_params` (:366-375), keeping everything above `defaults = ...` exactly as it is:
  ```python
      # ONE filler, shared with the page (`services.fill_engine_blanks`,
      # ADR 0012 D-EDIT-13). The page's own rule fills only the params the
      # model IGNORES -- an enabled field it left blank is a real
      # validation failure. A TOOL caller has no form and no disabled
      # widget, so it may simply have omitted `sampler`; this passes its
      # own, wider key set and keeps the same value order (the model's
      # reported default, else the engine's first option).
      return services.fill_engine_blanks(
          operation, raw_params, check.resolved, keys=blank_keys
      )
  ```
  and update the paragraph in this module's own docstring (:79-84) to name the shared function:
  ```
  - An ENGINE-owned param (`sampler`, `scheduler`) the caller leaves blank
    is filled by `services.fill_engine_blanks` -- the same function the page
    uses for the params a model IGNORES -- with this module's own, wider key
    set: the model's reported default, else the first entry
    `services.live_options` reports, never left blank, which
    `validate_params` would refuse for ANY `"choice"`-kind param regardless
    of `required`.
  ```
- [ ] Run the same command. Expected: green.
- [ ] Run the P1 guards and the tool suite together:
  `DATABASE_URL=postgres://farabunker:farabunker@localhost:5435/farabunker <repo>/.venv/bin/pytest -q -p no:cacheprovider agents tools/vision/tests/test_tools.py tools/vision/tests/test_apps.py foundation/ops/tests/test_column_boundaries.py foundation/ops/tests/test_import_law.py`
  Expected: green — in particular `test_no_tool_module_imports_its_service_layer_at_module_scope` and `agents/contracts/tests/test_purity.py`.
- [ ] Confirm by eye that `tools/vision/tools.py`'s module scope still imports only `time`, `dataclasses.replace`, `agents.contracts.tools`, and `models.contracts.operations`:
  `sed -n '103,112p' tools/vision/tools.py`
- [ ] Commit:
  ```
  git add -A && git commit -m "$(cat <<'EOF'
  refactor(vision): R2 T8 — the tool fills blank engine params through the shared service

  `_fill_engine_params` keeps its own preflight/ToolRefused guard and its own
  wider key set, and delegates the filling itself to
  `services.fill_engine_blanks`. `build_generate_spec` deliberately does NOT
  call `services.union_params` — `AppConfig.ready()` must not import the
  service layer — so a test pins the two unions together instead.

  Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_<id>
  EOF
  )"
  ```

---

### Task 9: Close-out — ADR, ROADMAP, both-order suite, migration check, live walk

Nothing here is "done" until fresh pixels on the branch preview say so. No success language before that.

**Files**
- Modify `docs/adr/0012-image-generation-engine-adapter.md` (new D-EDIT-13 after D-EDIT-12, which ends at :790)
- Modify `docs/ROADMAP.md` (Phase 1.55, after the R1 bullet at :156-170)
- Modify `tools/vision/README.md` (final consistency sweep only)

**Interfaces**
- Consumes: everything Tasks 1-8 produced
- Produces: the decision record, the roadmap entry, and the verification artefacts (screenshots in the scratchpad) the owner's doctrine requires

**Steps**

- [ ] Write **D-EDIT-13** in `docs/adr/0012-image-generation-engine-adapter.md`, immediately after D-EDIT-12's last bullet (the bullet ending just before the next heading at :790) and before "### The content-agnostic stance, stated plainly":
  ```markdown
  **D-EDIT-13 (R2, 2026-08-27) — one constant input form.** `/vision/`
  narrowed itself three ways — only the picked operation's params were
  rendered, a mode the model could not run vanished from the nav with no
  trace, and `img2img`/`edit` wore one merged name — and the owner read the
  narrowing as lost function. Five decisions, landed together:

  - **The page renders the UNION of every registered operation's params,
    always.** `services.union_params` de-duplicates by key, first
    declaration wins, in registration order — 20 fields, the same 20
    whichever mode is picked. Each is `enabled`, `unused` (this mode does
    not declare it), or `ignored` (this model's graph cannot honour it);
    the two disabled states carry HTML `disabled`, so the browser never
    submits them, and `required=False`, so nothing an operator was not
    allowed to answer can block the form. The union defines the SET and the
    ORDER; the PICKED operation's own `Param` defines each enabled field's
    rendering rule, which is why `steps` opens at 20 under `edit` and 25
    under `txt2img`. `_MERGED_OPERATION_KEYS`, `_merge_image_to_image`,
    `_display_label`, and `page_tabs` are retired, and the `<h1>` becomes
    the constant "Image generation" — owner ruling 2026-08-24(a)'s merged
    "Image to image" entry is superseded: two operations wearing one name
    was itself a narrowing, and the Operation select names each one.
  - **The "this model ignores that param" fact travels engine → services →
    page, never a `comfyui_workflows` import.** D-EDIT-12 shipped
    `ignored_params` as a reader with no consumer; the new optional engine
    member `InferenceEngine.ignored_params(operation_key, config)` and its
    `getattr`-and-never-raise reader `services.live_ignored` are that
    consumer. `tools/vision` still learns nothing about which engine is
    bound. `services.fill_engine_blanks` supplies an ignored `"choice"`
    param's value between the bound form and `validate_params` — from the
    model's reported default, else the engine's first option — because a
    disabled widget submits nothing and the schema floor refuses a blank
    choice. An ignored `"text"` param (`negative_prompt`) is deliberately
    NOT filled: writing a value into the job record for text the graph
    never read would be a lie about what ran.
  - **D-EDIT-7 still stands.** Nothing here narrows, adds, or removes a
    `Param`; `validate_params` is still the floor every caller shares, and
    the POST path still validates against the PICKED operation's own
    `build_form`. The union form is a RENDERING object and never validates
    a submission.
  - **The catalog now lists unsupported operations, with a reason** —
    `GET /vision/operations/` gains `"supported"`/`"unsupported_reason"`
    per operation and `"ignored"` per param, and lists every registered
    mode rather than only the runnable ones. A deliberate reversal of
    "never tell a tool about a mode it cannot perform": saying why is more
    useful than silence, and a caller that read the array as "modes I can
    run" now filters on `"supported"`. The in-repo consumers are the page
    and `tools/vision/tools.py`.
  - **`?operation=<key>` is the canonical URL; `op/<key>/` is a rendering
    alias.** The Operation select had to be a control inside the picker's
    existing GET form so that changing it reloads with no JavaScript — and
    a GET form can only produce a query parameter. The path route still
    serves the identical page with no redirect, so previously issued links
    keep working, and a path segment wins over the query key when both are
    present.

  `Param.description` moved with the same reasoning: one copy of the text,
  shown as an ⓘ tooltip on hover and keyboard focus rather than an
  always-visible line under every field, and always kept in the DOM. The
  CONTROL carries `aria-describedby` pointing at it (stamped on the widget
  by `build_constant_form`), so a screen-reader user tabbing into a field
  hears the description without ever landing on the ⓘ; the ⓘ points at it
  too, for a pointer user. Situational copy
  (a seed's "leave blank for a random seed", an asset field's "nothing
  installed") still wins over the schema's sentence, exactly as before.
  ```
- [ ] Add the ROADMAP bullet in `docs/ROADMAP.md` Phase 1.55, immediately after the R1 bullet's last line (:158, "…See ADR 0012 D-EDIT-12.") and before the blank line at :159 that precedes "Deferred (see ADR 0012's Consequences)" at :160:
  ```markdown
  - [x] **One constant input form (R2)** — `/vision/` stopped narrowing itself:
    the page renders the union of every registered operation's params (20 fields,
    one fixed order, whichever mode is picked), each one enabled, `unused` ("Not
    used by Upscale."), or `ignored` — disabled with the engine's own reason, via
    the new `InferenceEngine.ignored_params` member and `services.live_ignored`,
    which is what finally consumes D-EDIT-12's reader. Every registered mode is an
    option in the header's GET form, the ones this model has no graph for disabled
    and explained, so `inpaint` never simply disappears; `img2img` and `edit` are
    two modes again under their own labels. `Param.description` became an ⓘ
    hover/focus tooltip. `?operation=` is the canonical URL, `op/<key>/` an alias.
    See ADR 0012 D-EDIT-13.
  ```
  Move nothing out of Deferred.
- [ ] Sweep `tools/vision/README.md` for anything the eight tasks left stale:
  `grep -n "op-chooser\|field-hint\|Image to image\" tab\|_display_label\|page_tabs\|deferred to the constant-form" tools/vision/README.md`
  Expected: no hits. Fix any that appear.
- [ ] Sweep the repo for references to the deleted view helpers:
  `grep -rn "_merge_image_to_image\|_display_label\|page_tabs\|operation_tabs\|page_title" tools/ docs/ --include='*.py' --include='*.html' --include='*.md'`
  Expected: no hits outside `docs/superpowers/` (this plan and the spec) and the ADR's historical D-EDIT text.
- [ ] Run the full suite in collection order:
  `DATABASE_URL=postgres://farabunker:farabunker@localhost:5435/farabunker FARABUNKER_FEATURES='vision,media' <repo>/.venv/bin/pytest -q -p no:cacheprovider`
  Expected: no failures, no new warnings.
- [ ] Run the full suite in the other order:
  `DATABASE_URL=postgres://farabunker:farabunker@localhost:5435/farabunker FARABUNKER_FEATURES='vision,media' <repo>/.venv/bin/pytest -q -p no:cacheprovider scripts agents foundation models tools`
  Expected: identical result. A pass in one order and a failure in the other is a module-global leak, not a flake — fix it before going further.
- [ ] Confirm no model change slipped in:
  `DATABASE_URL=postgres://farabunker:farabunker@localhost:5435/farabunker FARABUNKER_FEATURES='vision,media' <repo>/.venv/bin/python manage.py makemigrations --check --dry-run`
  Expected: "No changes detected".
- [ ] Bring the branch preview up on :8002 and reload it with this worktree's code (the containers bind-mount it, so Python and template changes need a restart, not a rebuild):
  ```
  docker ps --filter name=farabunker-preview-vision-generation --format '{{.Names}}\t{{.Status}}'
  # if nothing is running:
  #   <worktree>/scripts/preview up vision-generation --port 8002 --db-port 5435
  docker restart farabunker-preview-vision-generation-web-1 farabunker-preview-vision-generation-worker-1
  curl -sS -o /dev/null -w '%{http_code}\n' http://localhost:8002/vision/
  ```
  Expected: `200`. NEVER `:8000`, never the main checkout.
- [ ] Live walk, browser, screenshots into `<scratchpad>/`. Connections on this preview: 1 = baseline checkpoint, 2 = the ordinary build, 3 = the distilled 9B build, 4 = the edit-only family. Capture each of these:
  - `http://localhost:8002/vision/?connection=3&operation=txt2img` — the distilled build, text to image. Confirm and screenshot as `r2-klein-txt2img.png`: all 20 fields present; `negative_prompt` and `scheduler` disabled, each showing its `IGNORES` sentence inline; `Inpaint`, `Image to image`, and `Upscale` disabled in the Operation select, each naming the `flux2` family.
  - Hover the ⓘ beside Width, screenshot `r2-tooltip-hover.png`. Then Tab to the same ⓘ and screenshot `r2-tooltip-focus.png` — the tooltip must open on keyboard focus too.
  - Submit a real generation from that page (the ordinary Generate button, not a hand-built enqueue) and screenshot the finished card as `r2-klein-generation.png`. This is the proof that a disabled `scheduler` is back-filled rather than 400'd. If the queue is wedged, the fallback is a direct `models.contracts.queue.enqueue` of the same payload — say so explicitly in the report if that fallback was used.
  - `http://localhost:8002/vision/?connection=1&operation=txt2img` — the baseline checkpoint. Screenshot `r2-sdxl-all-enabled.png`: every txt2img field enabled, no `IGNORES` text anywhere, `Edit an image` disabled in the select with its reason.
  - `http://localhost:8002/vision/?connection=3&operation=inpaint` — confirm the substitution is VISIBLE: the page falls back to a supported mode and the select still shows `Inpaint` disabled with its reason. Screenshot `r2-klein-inpaint-fallback.png`.
- [ ] Only after those screenshots exist and show what they claim, write the verification log into the commit body. Commit:
  ```
  git add -A && git commit -m "$(cat <<'EOF'
  docs(vision): R2 — D-EDIT-13, ROADMAP, and the live verification log

  ADR 0012 D-EDIT-13 records the five decisions R2 lands; the ROADMAP gains
  its Phase 1.55 bullet. Full suite green in both collection orders under
  FARABUNKER_FEATURES='vision,media', makemigrations --check clean, and the
  branch preview at :8002 walked with screenshots (Klein txt2img with
  negative_prompt/scheduler disabled and generating, SDXL fully enabled,
  tooltip on hover and on keyboard focus).

  Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_<id>
  EOF
  )"
  ```
- [ ] Report to the owner with the screenshot paths and the two suite results. Do not push, do not open a PR, and use no success language about anything that was not photographed.

## Plan review

- Round 1 (2026-08-27, adversarial reviewer): AMEND — 14 findings (6 blocking mechanics: an unlisted breaking test in `test_services.py`, option markup `selected`/`disabled` order, `create.html` picker range leaving an unclosed `{% if %}`, four missed `test_views_generate.py` URL assertions, a catalog test that would hit the network; plus double-reported form errors, `aria-describedby` on the wrong element, README/ROADMAP/views line drift, and two design points). All applied by the author. Orchestrator rulings: `union_params` stays in `services` (`models/contracts/operations.py` is in the P1 no-touch zone) and the view passes the union into `build_constant_form`; `operation_states` computed once per render; `live_ignored` computed once per POST.
- Round 2 (scoped re-check): 14/14 verified resolved against the real code; two nits (a `:726` line ref that would misplace the `ignored` computation — corrected to `:708-709`; a "only such deviation" miscount) fixed by the orchestrator. Verdict recorded as CLEAN.
