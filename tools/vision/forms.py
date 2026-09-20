"""
Schema-driven form building (spec §4.7).

The page never hand-writes a field: `build_form` renders an `Operation`'s
`Param` tuple into a Django form, so adding a parameter -- or a whole
operation -- changes no template and no view. Live option lists (samplers,
schedulers) come from the ENGINE at render time and are injected here,
because only the engine knows what it actually supports.

`build_form` renders ONE operation and is what a submission validates
against. `build_constant_form` renders the UNION of every registered
operation (ADR 0012 D-EDIT-13) and never validates anything: it is the
page's rendering object, so switching modes cannot make a control vanish.
"""
from __future__ import annotations

from collections.abc import Sequence

from django import forms
from django.urls import reverse

from models.contracts.operations import Operation, Param


def _widget_attrs(param: Param) -> dict:
    attrs: dict[str, object] = {}
    if param.step is not None:
        attrs["step"] = param.step
    if param.accept:
        attrs["accept"] = param.accept
    return attrs


def _base_field_for(param: Param, engine_options: dict[str, tuple[str, ...]]) -> forms.Field:
    """One `Param` as a Django field.

    A `"choice"` param whose live options are unavailable (the engine could
    not be asked) degrades to a free-text field rather than an empty
    dropdown: the operator can still type a value, and an unknown one comes
    back as an honestly failed job instead of a page that offers nothing.
    """
    if param.kind == "text":
        return forms.CharField(
            label=param.label,
            required=param.required,
            initial=param.default,
            widget=forms.Textarea(attrs={"rows": 3}),
        )
    if param.kind == "int":
        return forms.IntegerField(
            label=param.label, required=False, initial=param.default,
            min_value=None if param.min is None else int(param.min),
            max_value=None if param.max is None else int(param.max),
            widget=forms.NumberInput(attrs=_widget_attrs(param)),
        )
    if param.kind == "float":
        return forms.FloatField(
            label=param.label, required=False, initial=param.default,
            min_value=param.min, max_value=param.max,
            widget=forms.NumberInput(attrs=_widget_attrs(param)),
        )
    if param.kind == "seed":
        return forms.CharField(
            label=param.label, required=False,
            help_text="Leave blank for a random seed.",
            widget=forms.TextInput(attrs={"inputmode": "numeric"}),
        )
    if param.kind == "choice":
        options = param.choices or engine_options.get(param.key, ())
        if not options:
            return forms.CharField(label=param.label, required=True)
        return forms.ChoiceField(
            label=param.label,
            choices=[(value, value) for value in options],
            initial=options[0],
        )
    if param.kind == "file":
        return forms.FileField(
            label=param.label, required=param.required,
            widget=forms.ClearableFileInput(attrs=_widget_attrs(param)),
        )
    if param.kind == "asset":
        # Options come from the ENGINE (`InferenceEngine.list_assets`), the
        # same way a live `"choice"` param's do -- this platform never
        # ships a list of LoRAs or upscalers, it reports what the operator
        # placed. A required asset with nothing reported degrades to an
        # OPTIONAL empty field carrying an honest sentence: an operator
        # cannot pick a file the engine does not have, and blocking the
        # whole form behind a dropdown with no entries explains nothing.
        options = engine_options.get(param.key, ())
        field_class = forms.MultipleChoiceField if param.multiple else forms.ChoiceField
        # A multiple-choice asset (LoRAs today) renders as the platform's
        # chip-picker, never a raw `<select multiple>` -- the owner's
        # platform-wide ruling (see the document-labels picker this
        # mirrors, `tools/rag/templates/rag/documents.html`). Giving it
        # `CheckboxSelectMultiple` here, in the ONE place that builds this
        # field, means the POST shape does not change at all: still one
        # name, still `getlist`, still the same `choices`/`required`.
        widget = forms.CheckboxSelectMultiple() if param.multiple else None
        if not options:
            # THE RULING (S4): nothing to choose from. For an OPTIONAL
            # asset ("no LoRAs" is a normal answer) that is simply an empty
            # field. For a REQUIRED one the generation genuinely cannot
            # run, so the field stays required and carries the reason --
            # the refusal belongs on the field the operator is looking at,
            # never in a 400 about a field the form called optional.
            # `validate_params` refuses it as well; that is the schema
            # floor a caller with no form (the chatbot tool) lands on.
            missing = (
                f"No {param.label.lower()} is available — the image engine has none "
                f"installed, or no image model is assigned yet. See "
                f"{reverse('setup-index')} and {reverse('inference-console')}, then reload "
                "this page."
            )
            if param.required:
                missing += " This generation cannot run without one."
            field = field_class(
                label=param.label,
                choices=[],
                required=param.required,
                help_text=missing,
                error_messages={"required": missing},
                widget=widget,
            )
        else:
            field = field_class(
                label=param.label,
                choices=[(value, value) for value in options],
                required=param.required,
                widget=widget,
            )
        if param.multiple:
            # `vision/_field.html` picks the chip-picker markup off THIS
            # flag rather than introspecting the widget class in the
            # template -- the template should not have to know Django's
            # widget hierarchy to render a form.
            field.chip_group = True
        return field
    raise ValueError(
        f"Param kind {param.kind!r} ({param.key!r}) has no form field yet."
    )


def _field_for(param: Param, engine_options: dict[str, tuple[str, ...]]) -> forms.Field:
    """`_base_field_for`, plus the SCHEMA's own sentence as help text
    wherever the field does not already carry copy of its own.

    Situational copy wins, which is the ruling this phase inherits: a seed's
    "Leave blank for a random seed." and an asset field's "no LoRA is
    available — the image engine has none installed" both describe the
    situation the operator is in right now, and a generic parameter
    description would be a downgrade. Everything else -- the great majority
    of params, all of which `models.contracts.operations` already describes --
    gets the schema's sentence rather than nothing.

    Applied HERE, in one place, rather than inside each of the seven
    branches above: a new param kind gets this behaviour for free, and no
    branch can forget it.
    """
    field = _base_field_for(param, engine_options)
    if not field.help_text and param.description:
        field.help_text = param.description
    # `field.kind`/`field.multiple` are stamped HERE, in the path BOTH
    # `build_form` and `build_constant_form` call, unlike `state`/`reason`/
    # `wide` below (which only the constant form's rendering loop can
    # know, since only it has multiple operations to compare). A kind
    # never differs by which builder made the field, so both agree on it.
    # `vision/_field.html` reads these as `f.field.kind` (the
    # `field--kind-*` CSS class) and `f.field.multiple` (the
    # `field--multi` class).
    field.kind = param.kind
    field.multiple = bool(getattr(param, "multiple", False))
    return field


def _finish_form(
    fields: dict[str, forms.Field],
    operation: Operation,
    initial: dict | None,
    stored_keys,
    class_name: str,
    data=None,
    files=None,
) -> forms.Form:
    """The tail `build_form` and `build_constant_form` both end with, once
    each has finished deciding its OWN `fields` dict: prefill from
    `initial`, relax `required` for `stored_keys`, then build and bind the
    `forms.Form` subclass. Identical either way, so it lives here instead
    of twice.

    A `"file"` param's stored `initial` value is a basename string (see
    `operations._file_reference`), and a browser cannot re-send a file
    from a name. Prefilling one would render "Currently: beach.png" for a
    file the engine will never receive, so file fields always start empty
    and the operator picks again.

    `stored_keys` names file params a STORED image already answers (the
    gallery's "use in ..." link, carried as `input_<key>`): those fields
    stop being required, because the operator has already said which image
    to use and attaching a second one would be answering twice. They stay
    RENDERED and optional -- attaching a file replaces the stored choice.

    `class_name` is cosmetic only (Django uses it for `repr`/debugging);
    each caller keeps its own so a traceback still says which form it was.
    """
    if initial:
        file_keys = operation.file_param_keys()
        for key, value in initial.items():
            if key in fields and key not in file_keys:
                fields[key].initial = value
    for key in stored_keys:
        if key in fields:
            fields[key].required = False
    form_class = type(class_name, (forms.Form,), fields)
    return form_class(data=data, files=files)


def build_form(
    operation: Operation,
    engine_options: dict[str, tuple[str, ...]],
    data=None,
    files=None,
    initial: dict | None = None,
    stored_keys=frozenset(),
) -> forms.Form:
    """A bound or unbound form for `operation`.

    `engine_options` maps a choice param's key to the engine's live options
    (`InferenceEngine.list_choices`). `initial` prefills fields -- the
    gallery's "reuse settings" path passes a previous job's params.

    Form validation is the *live* layer (real option lists, widget bounds);
    `models.contracts.operations.validate_params` remains the schema floor
    every caller shares, including the future chatbot tool that never builds
    a form at all.

    The tail (`initial` prefill, `stored_keys` relaxation, the form class
    itself) is `_finish_form`, shared with `build_constant_form`.
    """
    fields = {param.key: _field_for(param, engine_options) for param in operation.params}
    return _finish_form(fields, operation, initial, stored_keys, "GenerationForm", data, files)


def build_constant_form(
    union: Sequence[Param],
    operation: Operation,
    engine_options: dict[str, tuple[str, ...]],
    ignored: dict[str, str],
    initial: dict | None = None,
    stored_keys=frozenset(),
    data=None,
    files=None,
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

    This form never validates a submission. `data` and `files` exist for
    ONE caller: the non-XHR re-render around an invalid, refused, or
    not-ready POST (`views._create_page_response`), which binds both so
    the operator's typed values AND attached uploads come back -- without
    `files`, a re-render would show "This field is required." under a
    file the operator did in fact attach -- and then copies the real
    form's errors across.

    The tail (`initial` prefill, `stored_keys` relaxation, the form class
    itself) is `_finish_form`, shared with `build_form`.
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
        # Only stamped when the template actually renders a `.tip` --
        # `_field.html` renders it only `{% if f.help_text or
        # f.field.reason %}`, so an unconditional stamp would point a
        # description-less param at a nonexistent id.
        if field.help_text or reason:
            field.widget.attrs["aria-describedby"] = f"id_{key}-info"
        if state != "enabled":
            field.required = False
            field.widget.attrs["disabled"] = True
        fields[key] = field

    return _finish_form(
        fields, operation, initial, stored_keys, "ConstantGenerationForm", data, files
    )
