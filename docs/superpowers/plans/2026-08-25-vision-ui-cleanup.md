# Vision UI Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give every `/vision/` job card the gallery's full action set from ONE shared partial, and rebuild the card itself — media, a labelled facts panel, a status chip, a real footer — into something that does not read as "crude", while wiring honest live progress (engine queue position, elapsed-seconds `ctx.report_progress`) and schema-authored field hints.

**Architecture:** Nine strictly sequential tasks on the `vision-generation` branch. Tasks 1–4 are the owner's two headline items: a new `vision/_output_actions.html` included by BOTH `gallery.html` and `_job_card.html` (parity with no second copy of the links markup), a `layout="rows"` branch inside the existing `_job_facts.html` (one facts source, two presentations), a `.chip` promoted into `templates/_shell.html` so the console and `/vision/` share one status-pill definition, and a two-column card (media left, facts right) whose CSS lives in `modules/vision/templates/vision/base.html` next to the rules the three card-rendering views already share. Task 5 makes the queued placeholder name the PROMPT by exposing the queue's own registered summarizer through `console.jobs.backend.get_job` (`JobStatus.summary`), which serves the XHR path, the no-JS redirect path, and every poll from one source. Tasks 6–7 are the deferred progress work: ComfyUI's `/queue` genuinely knows a pending job's position (its heap entries carry the monotonic `number` the heap is keyed on, so sorting by it reproduces exact pop order), so `core.inference.engines.base.JobStatus` grows `queue_position`, and `services.wait_for` grows an `on_poll` seam that `run_generate` turns into `ctx.report_progress(elapsed, total=None, unit="seconds", label=...)`. Task 8 surfaces `Param.description` as form help text wherever no situational copy already occupies that slot. Task 9 is docs plus the full both-order suite.

**Tech Stack:** Django 5 templates (server-rendered, plain POSTs), vanilla CSS in inline `<style>` blocks (`templates/_shell.html` tokens + `vision/base.html` module rules), vanilla JS only as the existing progressive-enhancement poll script, pytest + `pytest-django` with the Django test client asserting rendered HTML, PostgreSQL on the branch preview port 5435. No new dependencies, no build step, no JS framework, no CDN.

**Spec:** `<scratchpad>/audit2/ui-enhancements-backlog.md` — reproduced verbatim in "Spec (owner's requests)" below, because that path is session-local and the plan must travel with its spec.

## Global Constraints

- **Offline-first UI doctrine.** Plain POSTs; every page must fully work with JavaScript disabled. JS is progressive enhancement only — the existing `create.html` poll script is the one pattern. Never-500: a missing attribute, a stale reference, a malformed id must degrade, never raise.
- **No JS frameworks, no CDNs, no external assets.** Vanilla JS inline or in `base.html` only. The project ships no external stylesheet; all CSS is inline `<style>`.
- **ONE shared actions partial** used by BOTH `gallery.html` and `_job_card.html`. Duplicating the links markup fails review — this is the whole point of the parity item.
- **No new dependencies.** No new Python packages, no new template libraries, no `{% load %}` of anything not already loaded.
- **CSS follows existing conventions.** Shared design tokens live in `templates/_shell.html`; rules that a fragment rendered by several views needs live in `modules/vision/templates/vision/base.html` (that file's own comment states this rule). Page-only rules live in that page's `{% block vision_style %}`. Dark-theme consistent with the console: colours are only ever `var(--token)`, never a literal hex outside a `:root` declaration.
- **Progress is never fabricated.** `ctx.report_progress` is wired inside `run_generate`'s wait loop (queue path only). `total` stays `None` — ComfyUI over HTTP has no per-step fraction (verified: history is written only at `task_done`; `/api/jobs` carries no progress field; per-step progress is WebSocket-only), so a percentage would be invented. `/queue/` already renders `progress_text`/`progress_percent` null-safely. Vision cards show phase + engine queue position, never a percent that does not exist.
- **Tests and docs ship with every task.** TDD: failing test first, run it, minimal implementation, run it, commit. Template assertions go through the Django test client against rendered HTML, matching `modules/vision/tests/test_views_*.py`.
- **Test invocation (ONE at a time, foreground):**
  `DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' <repo>/.venv/bin/python -m pytest <path> -q`
- **No `conftest.py` anywhere.** Class-level `@pytest.mark.django_db`; shared helpers live in `modules/vision/tests/_helpers.py`. Mocking is HTTP-layer only (`patch("core.inference.engines.comfyui.httpx.get", fake.get)`) — engine methods are never mocked away.
- **`modules/` may not import `console.*`.** `modules/vision/models.py`'s `queue_job_id` comment states this. Vision reads the queue only through `core.inference.queue`, and reads any newly added field with `getattr(status, "name", default)` because that seam passes the backend's object through unchanged.
- **Branch-only work.** No commits to `main`, no merges, no deploy. **No migrations are expected** — every change is templates, CSS, view context, one dataclass default field on a frozen non-model dataclass, and one function keyword argument. If any step appears to need a migration, STOP and report it rather than generating one.
- **Baseline:** `1865 passed / 1 skipped` in both orders — **re-measure it, do not assume it.** Run the full suite once before Task 1 and use THAT number as the floor; the branch may have moved since this plan was written.
- **Verification doctrine:** no "done"/"works"/"fixed" language about the page until fresh pixels have been seen on the branch preview (`:8002`). The browser walk is the orchestrator's, not this plan's.

---

## Spec (owner's requests)

From `ui-enhancements-backlog.md`, verbatim:

1. **Action parity on job cards.** The Recent/history cards show only Delete. Every card whose job has outputs must offer the same actions the gallery card has: Use in Image to image · Use in Inpaint · Use in Upscale · Reuse settings · Download. Owner's words: "I don't have the ability to quickly reload the inputs that created that image. I have to go to gallery and then select reuse — I should have all the options available in gallery here as well." Note: cards should reuse ONE shared actions partial with the gallery (no second copy of the links markup) — the architecture audit's no-duplication discipline applies.
2. **Card layout redesign — "the UI looks very crude."** Current card: floating input thumbnail, large output image, long unstyled facts line, bare Delete link, much dead horizontal space. Wants clean structure. Direction to explore in the UI plan: proper card grid/two-column layout (media left, structured facts right), facts as labeled rows or definition list instead of a dot-separated prose line, actions row styled consistently, input thumbnail(s) visually grouped with the output (e.g. small "inputs" strip), status chip placement, consistent spacing/typography with the rest of the console.

Carried from earlier phases: `ctx.report_progress` wiring from ComfyUI status polling → live progress on `/queue/` and on the vision cards; the queued-window placeholder naming the job kind instead of the prompt; `Param.description` not wired into form help text (situational copy wins) — render descriptions as hints where no situational copy exists.

---

## Design

The card the owner called crude is one flat column: a floating input thumbnail, a full-width output, a dot-separated prose line, a bare "Delete". The redesign gives it four horizontal bands and a two-column body.

```
┌─ .card.job-card ─────────────────────────────────────────────────────────┐
│ .job-head            padding .75rem 1rem, border-bottom 1px var(--border)│
│   <h3 class="job-title">a lighthouse at dusk…</h3>        [ DONE ] chip  │
├──────────────────────────────────────────────────────────────────────────┤
│ .job-body   grid: minmax(0,1.7fr) minmax(200px,1fr); gap 1rem; pad 1rem  │
│  ┌ .job-media ───────────────────────┐  ┌ .job-details ────────────────┐ │
│  │ .job-outputs                      │  │ <dl class="job-facts">       │ │
│  │  grid auto-fit minmax(200px,1fr)  │  │   Seed        42             │ │
│  │  ┌ figure.output-tile ─────────┐  │  │   Width       512            │ │
│  │  │ [   generated image   ]     │  │  │   Height      512            │ │
│  │  │ Use in Image to image ·     │  │  │   Steps       20             │ │
│  │  │ Use in Inpaint · Use in     │  │  │   Model       sdxl.safeten…  │ │
│  │  │ Upscale · Reuse settings ·  │  │  │ </dl>                        │ │
│  │  │ Download   (.output-actions)│  │  │ .job-meta  comfyui · 25 Aug  │ │
│  │  └─────────────────────────────┘  │  └──────────────────────────────┘ │
│  │ .job-inputs   INPUTS [72px][72px] │                                    │
│  └───────────────────────────────────┘                                    │
├──────────────────────────────────────────────────────────────────────────┤
│ .job-notes    error / unreachable / stale / engine-position / no-JS note  │
├──────────────────────────────────────────────────────────────────────────┤
│ .job-foot     border-top, right-aligned              [ Delete ▸ ]         │
└──────────────────────────────────────────────────────────────────────────┘
```

Regions and decisions:

- **Header band.** The prompt becomes an `<h3 class="job-title">` (was a bare `<strong>`), 0.98rem/600, `overflow-wrap: anywhere` so a URL-shaped prompt cannot widen the card. The status moves from muted grey text to a `.chip` on the right — `chip-done` (outlined `var(--ok)`), `chip-failed` (outlined `var(--danger)`), `chip-running` (outlined `var(--accent)`), `chip-queued` (the neutral filled pill). The chip class comes straight from `job.status`, whose values are already slugs (`queued`/`running`/`done`/`failed`), so no Python mapping is needed for the real card.
- **Body, two columns.** `minmax(0, 1.7fr)` for media and `minmax(200px, 1fr)` for facts. The `minmax(0, …)` is load-bearing: without it a wide image forces the media column past its share and the facts column collapses. One breakpoint only — `@media (max-width: 720px)` drops to a single column. This is the "dead horizontal space" fix: at 1040px page width a 512×512 output no longer leaves half the card empty.
- **Outputs as tiles.** Each output is a `figure.output-tile` — image, then the shared action row directly beneath it. A batch of four lays out as an auto-fit grid rather than four stacked full-width images. `max-height: 360px; object-fit: contain` keeps a tall portrait output from dominating the page.
- **Actions.** `.output-actions` is a wrapping flex row of `var(--accent)` links, separated by gap rather than the gallery's typed `·` characters. It is the SAME partial in both places, so the gallery caption gets the same treatment for free.
- **Facts as a definition list.** `<dl class="job-facts">` rendered as a two-column CSS grid (`auto 1fr`), labels in `var(--muted)`, values in `var(--text)`. The gallery caption keeps the compact dot-separated form — a 240px-wide figcaption is the one place the prose line reads better — and both render from the SAME `GenerationJob.facts` through the SAME partial, switched by a `layout` variable.
- **Inputs strip.** Inputs move under the outputs inside the media column, behind a small uppercase `INPUTS` label, at 72px (down from 96px) so they read as provenance rather than as a second result. Previously they floated above the output with no label at all.
- **Notes band.** Error, "engine unreachable", "still queued", the engine's own queue position, and the no-JS "Refresh to update." note collect in one band instead of scattering between the images and the facts line.
- **Footer band.** A top border and the delete disclosure right-aligned, so "Delete" reads as a card action rather than as loose text after the facts.
- **The queued placeholder** uses the same head/notes/foot grammar with no body — prompt as title (from the queue's own summarizer), neutral `QUEUED` chip, position line in the notes band.
- **Typography and spacing** reuse what the console already uses: 0.85rem muted secondary text, 6–8px radii, 1rem card padding, `var(--border)` hairlines. Nothing new is invented; the chip is *moved* into `_shell.html` precisely so the console and `/vision/` cannot drift.

---

## Dependency Table (strictly sequential — execute in this order)

| Task | Depends on | Why this order |
| --- | --- | --- |
| 1. Shared `_output_actions.html` + `input_targets` in every card context | — | The parity item; the redesign in Task 4 places this partial, so it must exist first. |
| 2. `_job_facts.html` gains `layout="rows"` | — | Task 4's details column includes it with `layout="rows"`. |
| 3. `.chip` promoted into `templates/_shell.html` | — | Tasks 4 and 5 both put a chip in a card header. |
| 4. Card layout redesign (`_job_card.html` + `base.html` CSS) | 1, 2, 3 | Consumes all three: the actions partial, the rows layout, the chip. |
| 5. Queued card: real summary + matching chrome | 3, 4 | Must match the redesigned card's grammar, and needs the chip. |
| 6. Engine queue position (`JobStatus.queue_position`) | 4 | Renders into the card's notes band, which Task 4 creates. |
| 7. `ctx.report_progress` wiring (`wait_for(on_poll=…)`) | 6 | Reports the phase the engine read in Task 6; ordered after it so one poll shape is final before it is reported. |
| 8. `Param.description` as form hints | — (ordered here) | Independent of the card work; touches `forms.py`/`create.html` only. |
| 9. Docs sweep + full both-order suite + preview note | 1–8 | Verification is last by definition. |

---

### Task 1: One shared output-actions partial (parity)

**Files:**
- Create: `modules/vision/templates/vision/_output_actions.html`
- Modify: `modules/vision/templates/vision/gallery.html` (replace the inline links in `<figcaption>`)
- Modify: `modules/vision/templates/vision/_job_card.html` (include it under each output)
- Modify: `modules/vision/templates/vision/base.html` (add `.output-actions` rules)
- Modify: `modules/vision/views.py` (`_render_card`, `CreateView.get_context_data`, `_create_page_response` all supply `input_targets`)
- Modify: `docs/DEV.md` (`## Generate images (/vision/)` section)
- Test: `modules/vision/tests/test_views_create.py`, `modules/vision/tests/test_views_gallery.py`

**Interfaces:**
- Consumes: `modules.vision.views.input_targets() -> list[dict]` — each dict is `{"label": str, "param_key": str, "url": str}` (exists today, used by the gallery).
- Produces: the partial `vision/_output_actions.html`, which requires exactly two context names: `output` (a `GeneratedOutput`) and `job` (its `GenerationJob`, passed explicitly so no template ever dereferences `output.job` and triggers a per-output query), plus `input_targets` inherited from the view context. Tasks 4 and 5 include it as `{% include "vision/_output_actions.html" with output=output job=job %}`.

- [x] **Step 1: Write the failing tests**

Add to `modules/vision/tests/test_views_create.py`, after `class TestSharedCardChrome`:

```python
@pytest.mark.django_db
class TestCardActionParity:
    """A Recent card must offer exactly what a gallery card offers, from the
    SAME partial -- the owner's request was "I should have all the options
    available in gallery here as well", and a second copy of the links
    markup is how the two silently drift apart."""

    def test_a_recent_card_offers_every_gallery_action(self, client, tmp_path):
        output = stored_output(tmp_path)

        # The registry is pinned exactly as `test_views_gallery.py`'s
        # `TestUseInLinks` (line 234) pins it: `input_targets()` reads the
        # live registry, so a test that asserts a specific mode's link must
        # own what is registered rather than inherit whatever ran first.
        with patch.dict(operations._OPERATIONS, {"txt2img": TXT2IMG, "img2img": IMG2IMG}):
            body = client.get(reverse("vision-create")).content.decode()

        assert "Use in Image to image" in body
        assert (
            reverse("vision-create-operation", args=["img2img"])
            + f"?input_init_image=output:{output.id}"
        ) in body
        assert f"{reverse('vision-create')}?reuse={output.job.id}" in body
        assert f"{reverse('vision-output-file', args=[output.id])}?download=1" in body

    def test_the_poll_fragment_offers_them_too(self, client, tmp_path):
        """`job_status` returns the card on its own; a card that loses its
        actions the moment the page polls is not parity."""
        output = stored_output(tmp_path)

        with patch.dict(operations._OPERATIONS, {"txt2img": TXT2IMG, "img2img": IMG2IMG}):
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
```

`test_views_create.py` already imports `patch`, `operations`, `TXT2IMG` and `stored_output`; add `IMG2IMG` to its `from core.inference.operations import ...` line (today it imports `TXT2IMG, UPSCALE, Operation, Param`).

Add to `modules/vision/tests/test_views_gallery.py`, inside `class TestUseInLinks`:

```python
    def test_the_gallery_links_come_from_the_shared_partial(self, client, tmp_path):
        """The gallery's own links must be the SAME markup the card renders,
        not a surviving second copy."""
        stored_output(tmp_path)

        response = client.get(reverse("vision-gallery"))

        assert "vision/_output_actions.html" in [t.name for t in response.templates]
```

- [x] **Step 2: Run the tests to verify they fail**

Run: `DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' <repo>/.venv/bin/python -m pytest modules/vision/tests/test_views_create.py::TestCardActionParity modules/vision/tests/test_views_gallery.py::TestUseInLinks -q`

Expected: FAIL — the create-page assertions find no "Use in Image to image", and both template-name assertions fail because `vision/_output_actions.html` does not exist.

- [x] **Step 3: Create the partial**

Create `modules/vision/templates/vision/_output_actions.html`:

```html
{% comment %}
The ONE action set an existing output offers, rendered by the gallery
figure AND by the job card. It was the gallery's alone; the Recent cards
showed only Delete, so reloading the inputs that made an image meant a
trip to the gallery (the owner's own words). Sharing the markup -- rather
than copying five links into the card -- is what keeps a mode registered
tomorrow from appearing in one place and not the other.

Context it needs:
  `output`        the `GeneratedOutput` these actions act on;
  `job`           its `GenerationJob`, passed EXPLICITLY by the caller.
                  The gallery has it select_related; the card has it in
                  hand. Reading `output.job` here instead would issue one
                  query per tile on a page showing a dozen.
  `input_targets` `views.input_targets()` -- every registered mode that
                  takes a file, read from the REGISTRY, so this template
                  names no operation of its own.
{% endcomment %}
<div class="output-actions">
  {% for target in input_targets %}
  <a href="{{ target.url }}?input_{{ target.param_key }}=output:{{ output.id }}">Use in {{ target.label }}</a>
  {% endfor %}
  <a href="{% url 'vision-create' %}?reuse={{ job.id }}">Reuse settings</a>
  <a href="{% url 'vision-output-file' output.id %}?download=1">Download</a>
</div>
```

- [x] **Step 4: Include it from both templates**

In `modules/vision/templates/vision/gallery.html`, replace the `<figcaption>` block (lines 21–31) with:

```html
    <figcaption>
      {{ output.job.headline|truncatechars:100 }}<br>
      Size {{ output.width|default:"?" }}×{{ output.height|default:"?" }} ·
      {% include "vision/_job_facts.html" with facts=output.job.facts %}<br>
      {{ output.job.engine }}
    </figcaption>
    {% include "vision/_output_actions.html" with output=output job=output.job %}
```

In `modules/vision/templates/vision/_job_card.html`, replace the outputs block (lines 34–42) with:

```html
  {% if job.outputs.all %}
  <div class="job-images">
    {% for output in job.outputs.all %}
    <figure class="output-tile">
      <a href="{% url 'vision-output-file' output.id %}" target="_blank" rel="noopener">
        <img src="{% url 'vision-output-file' output.id %}" alt="Generated image {{ forloop.counter }}">
      </a>
      {% include "vision/_output_actions.html" with output=output job=job %}
    </figure>
    {% endfor %}
  </div>
  {% endif %}
```

(Task 4 restructures the surrounding markup; this step only puts the actions where they belong.)

- [x] **Step 5: Give every card-rendering view `input_targets`**

In `modules/vision/views.py`, `_render_card` (line 373):

```python
def _render_card(request, job: GenerationJob) -> HttpResponse:
    # `input_targets` rides along because the card's shared actions partial
    # reads it -- the same registry list the gallery passes. Every view that
    # renders a card must supply it, or the card silently loses half its
    # actions the first time the page polls.
    return render(
        request,
        "vision/_job_card.html",
        {"job": job, "input_targets": input_targets()},
    )
```

In `CreateView.get_context_data`, next to `context["jobs"]`:

```python
        context["jobs"] = _recent_jobs()
        context["input_targets"] = input_targets()
        context["queued_card"] = _queued_placeholder(self.request)
```

In `_create_page_response`'s context dict, next to `"jobs"`:

```python
            "jobs": _recent_jobs(),
            "input_targets": input_targets(),
            "queued_card": _queued_placeholder(request),
```

- [x] **Step 6: Add the actions CSS**

In `modules/vision/templates/vision/base.html`, inside `{% block extra_style %}`, append AFTER the `.job-error` rule (line 46) — the last rule of the card-fragment block. Task 4 replaces the rules ABOVE this point and must not disturb what you add here:

```css
  /* The shared actions partial's row -- rendered by the card AND by the
     gallery figure, so its rules live here beside the other fragment
     rules, never on a page. Gap-separated rather than the typed "·"
     the gallery used to carry: a separator that wraps with the links. */
  .output-actions { display: flex; flex-wrap: wrap; gap: 0.15rem 0.6rem; font-size: 0.85rem; margin-top: 0.35rem; }
  .output-actions a { color: var(--accent); text-decoration: none; }
  .output-actions a:hover { text-decoration: underline; }
```

- [x] **Step 7: Run the tests to verify they pass**

Run: `DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' <repo>/.venv/bin/python -m pytest modules/vision/tests/test_views_create.py::TestCardActionParity modules/vision/tests/test_views_gallery.py::TestUseInLinks -q`

Expected: PASS (3 + 3 tests).

- [x] **Step 8: Run both view test modules for regressions**

Run: `DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' <repo>/.venv/bin/python -m pytest modules/vision/tests/test_views_create.py modules/vision/tests/test_views_gallery.py modules/vision/tests/test_views_queue.py -q`

Expected: PASS. If a gallery test asserted the literal ` · ` between "Use in …" links, update that assertion to the link text/href it actually cares about — the separator is now CSS gap, and asserting on a typed middot pins presentation, not behaviour.

- [x] **Step 9: Document it**

In `docs/DEV.md`, in `## Generate images (/vision/)`, replace the sentence "The card polls until the image appears; the **Gallery** tab lists everything generated, with "Reuse settings" to send a previous job's parameters back into the form." with:

```markdown
   The card polls until the image appears. Every finished card — on the
   Generate page and in the **Gallery** — offers the same actions: **Use
   in …** for each registered mode that takes an image, **Reuse settings**
   to send that job's parameters back into the form, and **Download**.
   Both surfaces render one shared partial
   (`modules/vision/templates/vision/_output_actions.html`), so a mode
   registered tomorrow appears on both without a template edit.
```

- [x] **Step 10: Commit**

```bash
git add modules/vision/templates/vision/_output_actions.html \
        modules/vision/templates/vision/gallery.html \
        modules/vision/templates/vision/_job_card.html \
        modules/vision/templates/vision/base.html \
        modules/vision/views.py \
        modules/vision/tests/test_views_create.py \
        modules/vision/tests/test_views_gallery.py \
        docs/DEV.md
git commit -m "$(cat <<'EOF'
feat(vision): share one output-actions partial between the card and the gallery

Recent cards offered only Delete; reloading the inputs that made an image
meant a trip to the gallery. Both surfaces now include
vision/_output_actions.html, and every view that renders a card supplies
input_targets so the actions survive a poll.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_<id>
EOF
)"
```

---

### Task 2: Facts as labelled rows (one partial, two layouts)

**Files:**
- Modify: `modules/vision/templates/vision/_job_facts.html`
- Modify: `modules/vision/templates/vision/base.html` (add `.job-facts` rules)
- Test: `modules/vision/tests/test_views_create.py`, `modules/vision/tests/test_views_gallery.py`

**Interfaces:**
- Consumes: `modules.vision.models.GenerationJob.facts -> list[tuple[str, str]]` (unchanged).
- Produces: `vision/_job_facts.html` accepts an optional `layout` variable. `layout="rows"` renders `<dl class="job-facts">` with a `<dt>`/`<dd>` pair per fact; anything else (including the variable being absent) renders today's inline dot-separated line. Task 4 includes it as `{% include "vision/_job_facts.html" with facts=job.facts layout="rows" %}`.

- [x] **Step 1: Write the failing tests**

Add to `modules/vision/tests/test_views_create.py`:

```python
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
```

Add to `modules/vision/tests/test_views_gallery.py`:

```python
@pytest.mark.django_db
class TestGalleryKeepsTheCompactFactsLine:
    def test_the_caption_still_reads_as_one_line(self, client):
        _job_with_output(seed=42)

        body = client.get(reverse("vision-gallery")).content.decode()

        assert "Seed 42 ·" in body
        assert '<dl class="job-facts">' not in body
```

- [x] **Step 2: Run the tests to verify they fail**

Run: `DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' <repo>/.venv/bin/python -m pytest modules/vision/tests/test_views_create.py::TestFactsLayouts modules/vision/tests/test_views_gallery.py::TestGalleryKeepsTheCompactFactsLine -q`

Expected: FAIL on the two card assertions (`<dl class="job-facts">` is absent; the dot line is still there). The gallery test passes already — it is the guard that this task does not change the caption.

- [x] **Step 3: Add the layout branch to the partial**

Replace the last line of `modules/vision/templates/vision/_job_facts.html` (the `{% for %}` line) with:

```html
{% comment %}
TWO presentations, ONE source. `layout="rows"` is the job card's labelled
definition list -- the owner's "facts as labeled rows instead of a
dot-separated prose line". Anything else (including no `layout` at all) is
the compact inline line, which is still the right shape inside a 240px
gallery figcaption where a two-column list wraps into nonsense. A second
partial would have been a second place to keep the same iteration.
{% endcomment %}
{% if layout == "rows" %}
<dl class="job-facts">
  {% for label, value in facts %}
  <dt>{{ label }}</dt><dd>{{ value }}</dd>
  {% endfor %}
</dl>
{% else %}{% for label, value in facts %}{{ label }} {{ value }}{% if not forloop.last %} · {% endif %}{% endfor %}{% endif %}
```

- [x] **Step 4: Switch the card to it**

In `modules/vision/templates/vision/_job_card.html`, replace line 49:

```html
  <p class="muted">{% include "vision/_job_facts.html" with facts=job.facts %}</p>
```

with:

```html
  {% include "vision/_job_facts.html" with facts=job.facts layout="rows" %}
```

- [x] **Step 5: Add the facts CSS**

In `modules/vision/templates/vision/base.html`, after the `.output-actions` rules from Task 1:

```css
  /* Labelled rows, not a prose line. A two-column grid rather than a
     floated <dt> so a long value wraps under itself instead of under the
     label. */
  .job-facts { display: grid; grid-template-columns: auto minmax(0, 1fr); gap: 0.15rem 0.75rem; margin: 0; font-size: 0.85rem; }
  .job-facts dt { color: var(--muted); }
  .job-facts dd { margin: 0; overflow-wrap: anywhere; }
```

- [x] **Step 6: Run the tests to verify they pass**

Run: `DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' <repo>/.venv/bin/python -m pytest modules/vision/tests/test_views_create.py::TestFactsLayouts modules/vision/tests/test_views_gallery.py::TestGalleryKeepsTheCompactFactsLine -q`

Expected: PASS (3 tests).

- [x] **Step 7: Run the vision suite for regressions**

Run: `DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' <repo>/.venv/bin/python -m pytest modules/vision -q`

Expected: PASS. Any existing test asserting a card's facts as `"Seed 42 · Width 512"` must be updated to the `<dt>`/`<dd>` form — that assertion was pinning the card's presentation and this task deliberately changes it.

- [x] **Step 8: Commit**

```bash
git add modules/vision/templates/vision/_job_facts.html \
        modules/vision/templates/vision/_job_card.html \
        modules/vision/templates/vision/base.html \
        modules/vision/tests/test_views_create.py \
        modules/vision/tests/test_views_gallery.py
git commit -m "$(cat <<'EOF'
feat(vision): render card facts as labelled rows, gallery keeps the line

_job_facts.html grows a `layout` variable: "rows" for the card's <dl>,
the existing inline line everywhere else. One source, one partial, two
presentations.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_<id>
EOF
)"
```

---

### Task 3: One `.chip`, owned by the shared shell

**Files:**
- Modify: `templates/_shell.html` (tokens + `.chip` + status modifiers)
- Modify: `console/jobs/templates/jobs/queue.html` (delete its local copy)
- Test: `console/jobs/tests/test_views.py`

**Interfaces:**
- Produces: a `.chip` class available on every page that extends `_shell.html`, plus `.chip-done`, `.chip-failed`, `.chip-running`, `.chip-queued` modifiers. Tasks 4 and 5 render `<span class="chip chip-{{ job.status }}">` / `<span class="chip chip-{{ card.state_class }}">`.

- [x] **Step 1: Write the failing test**

Add to `console/jobs/tests/test_views.py`, as a new class at the end of the file:

```python
@pytest.mark.django_db
class TestChipStyleIsSharedNotPageLocal:
    """The Queue page and the /vision/ cards render the same pill. Its rules
    live in templates/_shell.html so the two cannot drift; the Queue page
    keeps rendering it unchanged."""

    def test_the_queue_page_still_renders_chips(self, client):
        body = client.get(reverse("jobs-queue")).content.decode()

        assert ".chip {" in body
        assert "--chip-bg:" in body

    def test_the_queue_page_declares_the_rules_exactly_once(self, client):
        body = client.get(reverse("jobs-queue")).content.decode()

        assert body.count(".chip {") == 1
        assert body.count("--chip-bg: #eef1f8") == 1
```

`client` is pytest-django's own fixture (this module deliberately defines no local override — see its docstring) and `reverse` is already imported there; the URL name is `jobs-queue`, as every other test in the file uses.

- [x] **Step 2: Run the characterization test and record that it passes**

This is the one step in this plan that does NOT start red. The chip already renders on the Queue page; these tests exist to prove the move does not change that, so they must pass BEFORE and AFTER.

Run: `DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' <repo>/.venv/bin/python -m pytest console/jobs/tests/test_views.py::TestChipStyleIsSharedNotPageLocal -q`

Expected: the first test PASSES already (queue.html has its own copy) and the second PASSES too. This is a characterization test — it must keep passing after the rules move. Record that it passes now; Step 5 re-runs it as the real gate.

- [x] **Step 3: Add the chip to the shared shell**

In `templates/_shell.html`, add to the light `:root` block, after `--ok: #1b7f4f;`:

```css
    --chip-bg: #eef1f8;
    --chip-text: #3a4a6b;
```

and to the `@media (prefers-color-scheme: dark)` `:root` block, after `--ok: #79d68f;`:

```css
      --chip-bg: #2a2e3a;
      --chip-text: #b7c2e0;
```

Then, immediately before `{% block extra_style %}{% endblock %}`:

```css
  /* The one status pill this project has. It began on the Queue page
     (console/jobs/templates/jobs/queue.html) and the /vision/ job cards
     want the same object, so it lives HERE rather than being copied into
     a second page's <style> -- the same reason the design tokens above do.
     The modifiers colour by MEANING, using the tokens already declared:
     an outlined pill so a row of chips never reads as a row of buttons. */
  .chip {
    display: inline-block;
    padding: 0.1rem 0.5rem;
    border-radius: 999px;
    background: var(--chip-bg);
    color: var(--chip-text);
    font-size: 0.78rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.02em;
  }
  .chip-done { background: transparent; color: var(--ok); border: 1px solid var(--ok); }
  .chip-failed { background: transparent; color: var(--danger); border: 1px solid var(--danger); }
  .chip-running { background: transparent; color: var(--accent); border: 1px solid var(--accent); }
  .chip-queued { border: 1px solid transparent; }
```

- [x] **Step 4: Delete the queue page's copy**

In `console/jobs/templates/jobs/queue.html`, remove `--chip-bg`/`--chip-text` from BOTH `:root` blocks (light at lines 41–42, dark at lines 48–49) and remove the whole `.chip { … }` rule (lines 195–204). Leave `--error-bg`/`--error-text` and every other rule untouched.

- [x] **Step 5: Run the test to verify it still passes**

Run: `DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' <repo>/.venv/bin/python -m pytest console/jobs/tests/test_views.py -q`

Expected: PASS, including both new tests — the chip rules now arrive from `_shell.html` and appear exactly once in the rendered page.

- [x] **Step 6: Run the console suite for regressions**

Run: `DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' <repo>/.venv/bin/python -m pytest console -q`

Expected: PASS.

- [x] **Step 7: Commit**

```bash
git add templates/_shell.html console/jobs/templates/jobs/queue.html console/jobs/tests/test_views.py
git commit -m "$(cat <<'EOF'
refactor(ui): move the status chip into the shared shell

The Queue page owned the only .chip; the /vision/ cards need the same
pill. Rules and tokens move to templates/_shell.html with meaning-coloured
modifiers, and queue.html drops its local copy.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_<id>
EOF
)"
```

---

### Task 4: The card redesign

**Files:**
- Modify: `modules/vision/templates/vision/_job_card.html` (full restructure)
- Modify: `modules/vision/templates/vision/base.html` (card CSS)
- Modify: `docs/adr/0012-image-generation-engine-adapter.md` (amendment bullet)
- Test: `modules/vision/tests/test_views_create.py`

**Interfaces:**
- Consumes: `vision/_output_actions.html` (Task 1), `vision/_job_facts.html` with `layout="rows"` (Task 2), `.chip`/`.chip-<status>` (Task 3), and the untouched `vision/_delete_control.html`.
- Produces: the card's region class names, which Tasks 5 and 6 target — `.job-head`, `.job-title`, `.job-body`, `.job-media`, `.job-outputs`, `.output-tile`, `.job-inputs`, `.job-details`, `.job-meta`, `.job-notes`, `.job-foot`. `data-job-poll` and the element id `job-<uuid>` are unchanged, because `create.html`'s poll script finds cards by exactly those.

- [x] **Step 1: Write the failing tests**

Add to `modules/vision/tests/test_views_create.py`:

```python
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
        assert 'class="no-js-note"' in body
```

`JobInput` is already imported at the top of `test_views_create.py`; check the exact constructor kwargs against `modules/vision/models.py::JobInput` and match the ones `TestJobInputsAreVisible` in this same file already uses rather than inventing new ones.

- [x] **Step 2: Run the tests to verify they fail**

Run: `DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' <repo>/.venv/bin/python -m pytest modules/vision/tests/test_views_create.py::TestCardStructure -q`

Expected: FAIL — no `job-body`, no `job-foot`, no chip, no `output-tile`, no `Inputs` label.

- [x] **Step 3: Rewrite the card**

Replace the whole body of `modules/vision/templates/vision/_job_card.html` (keep the file's existing comment header, extended) with:

```html
{% comment %}
One job card. Rendered inline on the create page and returned on its own by
`job_status` -- the SAME fragment either way, so polling swaps like for like.

`data-job-poll` is present only while the job can still change: the script in
create.html keeps polling exactly as long as that attribute exists, so a
finished or failed job costs no further requests, and a page with JS disabled
simply shows the "refresh to update" note instead.

FOUR BANDS (2026-08-25 redesign): head (prompt + status chip), body (media
left, facts right), notes (everything the job wants to say about itself),
foot (the delete control). The two-column body is the fix for the dead
horizontal space a 512x512 output used to leave; `minmax(0, 1.7fr)` on the
media column is load-bearing -- without the `0` minimum a wide image pushes
past its share and squeezes the facts column to nothing.

Every class here is styled in `vision/base.html`, not on a page: this
fragment is rendered by the create page, by `queue_job_status`, and
standalone by `job_status`.
{% endcomment %}
<div class="card job-card" id="job-{{ job.id }}"
     {% if not job.is_terminal %}data-job-poll="{% url 'vision-job-status' job.id %}"{% endif %}>
  <div class="job-head">
    <h3 class="job-title">{{ job.headline|truncatechars:120 }}</h3>
    {% comment %}
    `job.status` is already a slug (`queued`/`running`/`done`/`failed` --
    `GenerationJob.Status`), so the modifier class needs no Python mapping;
    the human words stay `get_status_display`'s.
    {% endcomment %}
    <span class="chip chip-{{ job.status }}">{{ job.get_status_display }}</span>
  </div>

  <div class="job-body">
    <div class="job-media">
      {% if job.outputs.all %}
      <div class="job-outputs">
        {% for output in job.outputs.all %}
        <figure class="output-tile">
          <a href="{% url 'vision-output-file' output.id %}" target="_blank" rel="noopener">
            <img src="{% url 'vision-output-file' output.id %}" alt="Generated image {{ forloop.counter }}">
          </a>
          {% include "vision/_output_actions.html" with output=output job=job %}
        </figure>
        {% endfor %}
      </div>
      {% elif not job.is_terminal %}
      <div class="job-placeholder muted">No image yet.</div>
      {% endif %}

      {% comment %}
      What the job was GIVEN, grouped UNDER what it produced and behind a
      label -- it used to float above the output at output size, with
      nothing saying what it was. Empty for a mode with no file params.
      {% endcomment %}
      {% if job.inputs.all %}
      <div class="job-inputs">
        <span class="strip-label">Inputs</span>
        <div class="job-images">
          {% for job_input in job.inputs.all %}
          <a href="{% url 'vision-input-file' job_input.id %}" target="_blank" rel="noopener"
             title="{{ job_input.param_key }}">
            <img src="{% url 'vision-input-file' job_input.id %}" alt="Input {{ job_input.param_key }}">
          </a>
          {% endfor %}
        </div>
      </div>
      {% endif %}
    </div>

    <div class="job-details">
      {% include "vision/_job_facts.html" with facts=job.facts layout="rows" %}
      <p class="job-meta muted">{{ job.engine }} · {{ job.created_at|date:"j M Y, H:i" }}</p>
    </div>
  </div>

  <div class="job-notes">
    {% if job.error %}<p class="job-error">{{ job.error }}</p>{% endif %}
    {% if job.unreachable %}<p class="muted">Engine unreachable, still checking.</p>{% endif %}
    {% if job.is_stale %}<p class="muted">Still queued — check that the image engine is running.</p>{% endif %}
    {% if not job.is_terminal %}<p class="muted no-js-note">Refresh to update.</p>{% endif %}
  </div>

  <div class="job-foot">
    {% include "vision/_delete_control.html" %}
  </div>
</div>
```

- [x] **Step 4: Write the card CSS**

In `modules/vision/templates/vision/base.html`, replace ONLY the original card-fragment rules — `.job-card img`, `.job-images`, `.job-images img`, `.job-inputs img`, `.job-head`, `.job-error` (lines 41–46 as they stood before this plan began) — with the block below. Do NOT replace by line number: Tasks 1 and 2 appended the `.output-actions` and `.job-facts` blocks immediately after `.job-error`, and those must survive this edit intact. Delete the six named rules, paste this in their place, and leave everything after it alone:

```css
  /* --- the shared job card (2026-08-25 redesign) -------------------
     Four bands inside a `.card`, so the card's own 1rem padding is
     dropped and each band pads itself; that is what lets the head and
     foot carry full-width hairlines. */
  .job-card { padding: 0; }
  .job-card img { max-width: 100%; border-radius: 6px; display: block; }

  .job-head {
    display: flex; justify-content: space-between; gap: 1rem; align-items: center;
    padding: 0.75rem 1rem; border-bottom: 1px solid var(--border);
  }
  .job-title { margin: 0; font-size: 0.98rem; font-weight: 600; line-height: 1.35; overflow-wrap: anywhere; }

  .job-body {
    display: grid; grid-template-columns: minmax(0, 1.7fr) minmax(200px, 1fr);
    gap: 1rem; padding: 1rem; align-items: start;
  }
  /* One breakpoint. Below it the facts read better under the image than
     beside it, and 200px of facts column would squeeze the media. */
  @media (max-width: 720px) { .job-body { grid-template-columns: minmax(0, 1fr); } }

  .job-outputs { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 0.75rem; }
  .output-tile { margin: 0; display: flex; flex-direction: column; }
  .output-tile img { width: 100%; height: auto; max-height: 360px; object-fit: contain; }
  .job-placeholder {
    border: 1px dashed var(--border); border-radius: 6px;
    padding: 2rem 1rem; text-align: center;
  }

  .job-inputs { margin-top: 0.75rem; display: flex; align-items: center; gap: 0.5rem; flex-wrap: wrap; }
  .strip-label { font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.04em; color: var(--muted); }
  .job-images { display: flex; flex-wrap: wrap; gap: 0.5rem; margin: 0; }
  .job-inputs img { opacity: 0.85; max-height: 72px; width: auto; }

  .job-details { display: grid; gap: 0.6rem; }
  .job-meta { margin: 0; }

  .job-notes { padding: 0 1rem; }
  .job-notes p { margin: 0 0 0.5rem; }
  .job-error { color: var(--danger); }

  .job-foot {
    display: flex; justify-content: flex-end;
    padding: 0.6rem 1rem; border-top: 1px solid var(--border);
  }
  /* The shared delete control keeps its 0.5rem top margin inside a gallery
     figure; inside the card's own footer band the band supplies the space. */
  .job-foot .delete-disclosure { margin-top: 0; }
```

Note: the old `.job-images img { max-height: 320px; }` rule is deliberately gone — outputs are sized by `.output-tile img` now, and `.job-images` is the inputs strip's container only.

- [x] **Step 5: Run the tests to verify they pass**

Run: `DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' <repo>/.venv/bin/python -m pytest modules/vision/tests/test_views_create.py::TestCardStructure -q`

Expected: PASS (5 tests).

- [x] **Step 6: Run the vision suite for regressions**

Run: `DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' <repo>/.venv/bin/python -m pytest modules/vision -q`

Expected: PASS. Two known adjustment points: `TestSharedCardChrome::test_the_card_rules_come_from_the_module_base_not_the_page` still holds (the rules are still in `base.html`); any test asserting `<strong>` around a headline must move to `<h3 class="job-title">`.

- [x] **Step 7: Document it**

In `docs/adr/0012-image-generation-engine-adapter.md`, in the Consequences section near the existing job-card bullet (~line 341), append:

```markdown
- **Card presentation, amended 2026-08-25.** The job card is four bands
  (head / body / notes / foot) with a two-column body — media left,
  `GenerationJob.facts` as a `<dl>` right — a `.chip` status pill from
  `templates/_shell.html`, and the SAME output actions the gallery
  offers, from the shared `vision/_output_actions.html`. What the card
  renders is still schema-driven; only its shape changed. The gallery
  caption keeps the compact dot-separated facts line: same partial,
  `layout` variable, one source.
```

- [x] **Step 8: Commit**

```bash
git add modules/vision/templates/vision/_job_card.html \
        modules/vision/templates/vision/base.html \
        modules/vision/tests/test_views_create.py \
        docs/adr/0012-image-generation-engine-adapter.md
git commit -m "$(cat <<'EOF'
feat(vision): rebuild the job card into four bands with a two-column body

Head (prompt + status chip), body (media left, labelled facts right),
notes, foot (delete). Inputs become a labelled strip under the outputs;
outputs become tiles carrying the shared action row.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_<id>
EOF
)"
```

---

### Task 5: The queued placeholder names the prompt

**Files:**
- Modify: `console/jobs/backend.py` (`JobStatus.summary`, new public `summarize_job`, `get_job`)
- Modify: `console/jobs/views.py` (import `summarize_job`, drop the private `_summarize`)
- Modify: `modules/vision/views.py` (`_queue_card_context`, `_queue_status`, `generate`)
- Modify: `modules/vision/templates/vision/_queued_card.html`
- Modify: `docs/adr/0013-inference-execution-queue.md`
- Test: `console/jobs/tests/test_backend.py`, `modules/vision/tests/test_views_queue.py`

**Interfaces:**
- Produces: `console.jobs.backend.summarize_job(kind: str, payload: dict) -> tuple[str, str]` returning `(summary, kind_label)` — the function `console.jobs.views._summarize` is today, moved and made public. `console.jobs.backend.JobStatus` gains a field `summary: str` (the summarizer's line for this job, `""` never expected but tolerated). `core.inference.queue.get_job` passes the object through unchanged, so `modules.vision.views` reads it as `getattr(job_status, "summary", "")`.
- Consumes: `modules.vision.jobs.summarize_generate(payload) -> str` (already registered as `vision.generate`'s summarizer; it already returns the truncated prompt).
- Produces: `_queue_card_context(queue_job_id, job_status)` keeps its signature and gains two keys — `state_class` (a chip modifier) and a `label` that is now the queue's summary when there is one.

- [x] **Step 1: Write the failing tests**

In `console/jobs/tests/test_backend.py`, first give the existing `_register` helper (line ~66) a summarizer parameter, so a test can register a kind whose summarizer actually reads the payload:

```python
def _register(
    key, *, planner=f"{MODULE}.plan_no_models", summarizer=f"{MODULE}.summarize_noop",
    default_priority=None,
):
    register_job_kind(
        JobKind(
            key=key,
            label=key,
            planner=planner,
            handler=f"{MODULE}.handle_noop",
            summarizer=summarizer,
            default_priority=default_priority,
        )
    )
```

Add two module-level summarizers beside `summarize_noop`:

```python
def summarize_prompt(payload):
    """Reads the payload, so a job's `summary` can be told apart from its
    kind's label."""
    return payload.get("prompt", "")


def summarize_boom(payload):
    raise RuntimeError("this summarizer is broken")
```

Then add the test class at the end of `class TestGetJob`'s section:

```python
@pytest.mark.django_db
class TestJobStatusSummary:
    """A single-job read must be able to NAME the job. The queue still
    carries no payload across this seam -- what crosses is the string the
    job kind's OWN registered summarizer produced, which is the same thing
    the Queue page has always rendered from `QueueRow.payload`."""

    def test_get_job_carries_the_kinds_own_summary(self):
        _register("test.summary", summarizer=f"{MODULE}.summarize_prompt")
        job = InferenceJob.objects.create(
            kind="test.summary", priority=50, state=QUEUED,
            payload={"prompt": "a lighthouse"},
        )

        assert backend.get_job(job.pk).summary == "a lighthouse"

    def test_an_unregistered_kind_degrades_to_its_key(self):
        job = InferenceJob.objects.create(
            kind="gone.kind", priority=50, state=QUEUED, payload={}
        )

        assert backend.get_job(job.pk).summary == "gone.kind"

    def test_a_raising_summarizer_degrades_to_the_kind_label(self):
        _register("test.broken", summarizer=f"{MODULE}.summarize_boom")
        job = InferenceJob.objects.create(
            kind="test.broken", priority=50, state=QUEUED, payload={}
        )

        # `_register` uses the key as the label, so this is the label path.
        assert backend.get_job(job.pk).summary == "test.broken"
```

Add to `modules/vision/tests/test_views_queue.py` — first extend the `_Status` double:

```python
class _Status:
    """The shape `core.inference.queue.get_job` returns (console-side
    `JobStatus`), reduced to what this page reads."""

    def __init__(self, state="queued", position=1, result=None, error="", summary=""):
        self.state = state
        self.position = position
        self.result = result
        self.error = error
        self.summary = summary
```

then add:

```python
@pytest.mark.django_db
class TestQueuedCardNamesTheWork:
    """The placeholder used to say "Image generation" -- the job KIND --
    while the operator waited. It now says what they typed, taken from the
    queue's own registered summarizer, so the XHR path, the no-JS redirect
    path and every poll all read from ONE source."""

    def test_the_placeholder_shows_the_prompt(self, client):
        with patch(
            "modules.vision.views.get_job",
            return_value=_Status(summary="a lighthouse at dusk"),
        ):
            body = client.get(f"{reverse('vision-create')}?queued=7").content.decode()

        assert "a lighthouse at dusk" in body

    def test_it_falls_back_to_the_job_kind_label_with_no_summary(self, client):
        with patch("modules.vision.views.get_job", return_value=_Status(summary="")):
            body = client.get(f"{reverse('vision-create')}?queued=7").content.decode()

        # `modules/vision/apps.py` registers the kind with this label.
        assert "Generate an image" in body

    def test_the_placeholder_wears_a_chip(self, client):
        with patch("modules.vision.views.get_job", return_value=_Status(summary="x")):
            body = client.get(f"{reverse('vision-create')}?queued=7").content.decode()

        assert '<span class="chip chip-queued">' in body
```

The job-kind label asserted in the second test is `modules/vision/jobs.py`'s registered `JobKind.label`; confirm its exact text in `modules/vision/apps.py` and use that string.

- [x] **Step 2: Run the tests to verify they fail**

Run: `DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' <repo>/.venv/bin/python -m pytest console/jobs/tests/test_backend.py::TestJobStatusSummary modules/vision/tests/test_views_queue.py::TestQueuedCardNamesTheWork -q`

Expected: FAIL — `JobStatus` has no `summary` attribute, and the placeholder still renders the kind label with no chip.

- [x] **Step 3: Move the summarizer helper into the backend**

In `console/jobs/backend.py`, add to the imports from `core.inference.jobkinds` whatever is missing (`get_job_kind`, `resolve_dotted_path`), and add above `get_job`:

```python
def summarize_job(kind: str, payload: dict) -> tuple[str, str]:
    """`(summary, kind_label)` for one job.

    Resolves the registered `JobKind` fresh at call time (never stored --
    matches `core.inference.jobkinds`' own "dotted path, resolved lazily"
    contract) and calls its `summarizer(payload)`. Degrades to the raw
    `kind` key -- for both the label and the summary -- when the kind is
    unregistered (a kind renamed/removed after jobs referencing it were
    already enqueued, or a kind a test process never registered) or when
    the summarizer itself raises: a broken or incompatible summarizer must
    never take a page down, only lose this one job's summary text.

    Lives HERE rather than in `console.jobs.views` (where it began as
    `_summarize`) because `get_job` needs it too: the queue's single-job
    read shape carries no payload by design, so the only honest way for a
    caller to NAME a job is the string the job's own kind produced.
    """
    try:
        kind_spec = get_job_kind(kind)
    except ValueError:
        return kind, kind
    try:
        summary = resolve_dotted_path(kind_spec.summarizer)(payload)
    except Exception:  # noqa: BLE001 -- a broken summarizer loses one line, never a page
        summary = kind_spec.label
    return summary, kind_spec.label
```

Add the field to `JobStatus`, after `progress`:

```python
    progress: dict | None
    # The job kind's OWN one-line summary of this job (`summarize_job`
    # above). Not the payload: `QueueRow` carries the payload because a
    # listing page re-summarizes many rows at render time; this seam stays
    # payload-free and hands over the finished string, which is what a
    # single-job caller (the /vision/ queued card) actually needs to name
    # the work while it waits.
    summary: str
```

and populate it in `get_job`'s return:

```python
        error=job.error,
        progress=job.progress,
        summary=summarize_job(job.kind, job.payload)[0],
    )
```

Extend `JobStatus`'s class docstring with a sentence naming `summary` the way the `progress` sentence does.

- [x] **Step 4: Point the Queue page at the moved helper**

In `console/jobs/views.py`: delete the `_summarize` function, add `summarize_job` to the `from console.jobs.backend import …` line, drop `get_job_kind`/`resolve_dotted_path` from the `core.inference.jobkinds` import if nothing else in the module uses them (grep the file first), and change `_present_row`'s call:

```python
    summary, kind_label = summarize_job(row.kind, row.payload)
```

- [x] **Step 5: Use the summary in the vision queued card context**

In `modules/vision/views.py`, add next to `_QUEUE_STATE_LABELS`:

```python
# Which chip modifier a queue state wears. NOT a third spelling of `state`
# (the card still renders `state_label` and branches on `terminal`): this
# is the presentation class, derived once here so the template carries no
# state vocabulary of its own -- and it collapses five queue words onto
# the four the shared chip actually has.
_QUEUE_STATE_CLASSES = {
    "queued": "queued",
    "running": "running",
    "succeeded": "done",
    "failed": "failed",
    "cancelled": "failed",
}
```

and rewrite `_queue_card_context`'s return:

```python
def _queue_card_context(queue_job_id: int, job_status) -> dict:
    """What `_queued_card.html` needs for one queued submission."""
    state = getattr(job_status, "state", "queued")
    # The job KIND was all this card could say while the queue's single-job
    # read carried nothing but state and position. It now carries the
    # kind's own `summary` -- for `vision.generate` that is the operator's
    # prompt (`jobs.summarize_generate`) -- so the card names the work from
    # the first tick, on the XHR path, the no-JS redirect path, and every
    # poll, all from one source. `getattr` because this seam passes the
    # BACKEND's object through unchanged and this module must not assume a
    # console-side shape.
    summary = (getattr(job_status, "summary", "") or "").strip()
    return {
        "queue_job_id": queue_job_id,
        "poll_url": reverse("vision-queue-status", args=[queue_job_id]),
        "label": summary or get_job_kind(JOB_KIND).label,
        "state_label": _QUEUE_STATE_LABELS.get(state, state),
        "state_class": _QUEUE_STATE_CLASSES.get(state, "queued"),
        "position": getattr(job_status, "position", None),
        "error": getattr(job_status, "error", "") or "",
        "terminal": state in _QUEUE_TERMINAL_STATES,
    }
```

Add the never-raising read helper above `generate`:

```python
def _queue_status(queue_job_id: int):
    """The queue's own read of a job, or `None` when the queue cannot be
    read at all. A card that cannot name its job is a smaller failure than
    a 503 for a submission the queue already accepted."""
    try:
        return get_job(queue_job_id)
    except QueueUnavailable:
        return None
```

and use it in `generate`'s XHR success path, replacing `{"card": _queue_card_context(queue_job_id, None)}`:

```python
    if _is_xhr(request):
        return render(
            request,
            "vision/_queued_card.html",
            {"card": _queue_card_context(queue_job_id, _queue_status(queue_job_id))},
            status=202,
        )
```

- [x] **Step 6: Update the queued card template**

Replace `modules/vision/templates/vision/_queued_card.html`'s markup (keeping the file's comment header, with its "names the job KIND, not the prompt" paragraph rewritten) with:

```html
{% comment %}
The card for a submission that is IN THE QUEUE and has not started
generating yet. It stands in the Recent list exactly where the real job
card will stand, and `data-job-poll` points at `/vision/queue/<id>/`,
which answers with the REAL card (`_job_card.html`) the moment the worker
has created the generation row -- the script in create.html follows
whatever `data-job-poll` the fragment it receives carries, so the swap
needs no JavaScript of its own.

It names the WORK, not the job kind (amended 2026-08-25): `card.label` is
the queue's own `summary` for this job -- the string the registered kind's
summarizer produced, which for `vision.generate` is the operator's prompt.
The queue's single-job read still carries no payload; what crosses is a
finished display string, not the submission.

Same head/notes/foot grammar as `_job_card.html`, minus a body: a card
that becomes another card must not visibly change shape when it does.
{% endcomment %}
<div class="card job-card" id="queue-job-{{ card.queue_job_id }}"
     {% if not card.terminal %}data-job-poll="{{ card.poll_url }}"{% endif %}>
  <div class="job-head">
    <h3 class="job-title">{{ card.label|truncatechars:120 }}</h3>
    <span class="chip chip-{{ card.state_class }}">{{ card.state_label }}</span>
  </div>

  <div class="job-notes">
    {% if card.position %}
    <p class="muted">Waiting in the queue — position {{ card.position }}.</p>
    {% endif %}
    {% if card.error %}<p class="job-error">{{ card.error }}</p>{% endif %}
    {% if not card.terminal %}<p class="muted no-js-note">Refresh to update.</p>{% endif %}
  </div>
</div>
```

The `.job-notes` band needs bottom padding when there is no foot beneath it; add to `modules/vision/templates/vision/base.html` after the `.job-notes p` rule:

```css
  /* The queued placeholder has no body and no foot band, so its notes
     must pad themselves top AND bottom; on a full card the head's border
     and the foot band supply both. */
  .job-notes:last-child { padding-top: 0.5rem; padding-bottom: 0.5rem; }
```

- [x] **Step 7: Run the tests to verify they pass**

Run: `DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' <repo>/.venv/bin/python -m pytest console/jobs/tests/test_backend.py::TestJobStatusSummary modules/vision/tests/test_views_queue.py::TestQueuedCardNamesTheWork -q`

Expected: PASS (6 tests: 3 in `TestJobStatusSummary`, 3 in `TestQueuedCardNamesTheWork`).

- [x] **Step 8: Run both suites for regressions**

Run: `DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' <repo>/.venv/bin/python -m pytest console modules/vision -q`

Expected: PASS. (`console/jobs/backend.py`'s `get_job` is the only place that constructs a `JobStatus`, and it does so by keyword — no test builds one, so the new field breaks no call site.)

- [x] **Step 9: Document it**

In `docs/adr/0013-inference-execution-queue.md`, in the Consequences list, add:

```markdown
- **`JobStatus.summary`, added 2026-08-25.** The single-job read shape
  still carries no payload; it carries the string the job's OWN registered
  summarizer produced (`console.jobs.backend.summarize_job`, moved there
  from `console.jobs.views._summarize` so the Queue page and this seam
  cannot drift). It exists because a caller waiting on a job needs to name
  it: `/vision/`'s queued placeholder said "Image generation" — the job
  kind — for the whole queued window, on every path.
```

- [x] **Step 10: Commit**

```bash
git add console/jobs/backend.py console/jobs/views.py \
        console/jobs/tests/test_backend.py \
        modules/vision/views.py \
        modules/vision/templates/vision/_queued_card.html \
        modules/vision/templates/vision/base.html \
        modules/vision/tests/test_views_queue.py \
        docs/adr/0013-inference-execution-queue.md
git commit -m "$(cat <<'EOF'
feat(queue): carry a job kind's own summary on the single-job read

JobStatus gains `summary` from the newly shared backend.summarize_job, so
/vision/'s queued placeholder names the prompt instead of the job kind --
on the XHR path, the no-JS redirect path, and every poll.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_<id>
EOF
)"
```

---

### Task 6: The engine's own queue position

**Files:**
- Modify: `core/inference/engines/base.py` (`JobStatus.queue_position`)
- Modify: `core/inference/engines/comfyui.py` (`_pending_position`, `_queue_state`)
- Modify: `modules/vision/services.py` (`refresh_job` sets a transient `job.engine_position`)
- Modify: `modules/vision/templates/vision/_job_card.html` (one line in the notes band)
- Test: `modules/vision/tests/test_comfyui_generator.py`, `modules/vision/tests/test_services.py`, `modules/vision/tests/test_views_create.py`

**Interfaces:**
- Produces: `core.inference.engines.base.JobStatus.queue_position: int | None = None` — 1-based position among the engine's PENDING jobs, or `None` for any engine or state that does not know one. A defaulted field on a frozen dataclass; every existing `JobStatus(...)` construction stays valid.
- Produces: `GenerationJob.engine_position` — a TRANSIENT attribute (never a DB column, never a migration) that `services.refresh_job` sets on every call, exactly like the existing `job.unreachable`.

**Verified engine facts (do not re-litigate):** ComfyUI 0.33.0 exposes no fractional progress over HTTP — `/history/<id>` is written only at `task_done` (`execution.py::PromptQueue.task_done`), `/api/jobs/<id>` carries no progress field, and per-step progress is WebSocket-only. What `/queue` DOES give is the pending list, whose entries are `[number, prompt_id, prompt, extra_data, outputs_to_execute]`; `number` is the monotonic key the queue's `heapq` is ordered by, so sorting the returned list by it reproduces ComfyUI's exact pop order. The list itself is a heap dump and is NOT already in that order — reading a bare list index would be a lie.

- [x] **Step 1: Write the failing tests**

Add to `modules/vision/tests/test_comfyui_generator.py`, inside `class TestStatus`:

```python
    def test_a_pending_job_reports_its_position_in_pop_order(self):
        """ComfyUI's /queue hands back its own heap list, which is NOT in
        execution order -- but every entry's first element is the monotonic
        `number` the heap is keyed on, so sorting by it is the real order.
        REF is queued third by number while sitting first in the dump."""
        fake = FakeComfyUI(
            queue_pending=[
                [9, REF, {}, {}, []],
                [3, "other-a", {}, {}, []],
                [5, "other-b", {}, {}, []],
            ]
        )
        with patch("core.inference.engines.comfyui.httpx.get", fake.get):
            state = _generator().status(REF)

        assert state.state == "queued"
        assert state.queue_position == 3

    def test_a_running_job_has_no_position(self):
        fake = FakeComfyUI(queue_running=[[0, REF, {}, {}, []]])
        with patch("core.inference.engines.comfyui.httpx.get", fake.get):
            assert _generator().status(REF).queue_position is None

    def test_a_malformed_pending_entry_never_breaks_the_poll(self):
        fake = FakeComfyUI(queue_pending=[None, [], [1], ["x", REF, {}, {}, []]])
        with patch("core.inference.engines.comfyui.httpx.get", fake.get):
            state = _generator().status(REF)

        assert state.state == "queued"
        assert state.queue_position == 1
```

Add to `modules/vision/tests/test_services.py`, using the same `StubGenerator` + `_registered(StubEngine(...))` idiom `TestRefreshJob` already uses (the service layer's contract is with the SEAM, not with ComfyUI):

```python
@pytest.mark.django_db
class TestRefreshJobCarriesTheEnginePosition:
    """A TRANSIENT attribute, like `unreachable` -- the card reads it, the
    database never stores it, and no migration is involved."""

    def _submit(self, generator):
        _bind()
        with _registered(StubEngine(generator)):
            return services.submit_job("txt2img", dict(RAW))

    def test_a_queued_job_carries_the_position_the_engine_reported(self):
        generator = StubGenerator(states=[JobStatus(state="queued", queue_position=3)])
        job = self._submit(generator)
        with _registered(StubEngine(generator)):
            refreshed = services.refresh_job(job)

        assert refreshed.status == GenerationJob.Status.QUEUED
        assert refreshed.engine_position == 3

    def test_an_engine_that_reports_no_position_leaves_it_none(self):
        generator = StubGenerator(states=[JobStatus(state="queued")])
        job = self._submit(generator)
        with _registered(StubEngine(generator)):
            refreshed = services.refresh_job(job)

        assert refreshed.engine_position is None

    def test_a_running_job_reports_no_position(self):
        generator = StubGenerator(states=[JobStatus(state="running")])
        job = self._submit(generator)
        with _registered(StubEngine(generator)):
            refreshed = services.refresh_job(job)

        assert refreshed.status == GenerationJob.Status.RUNNING
        assert refreshed.engine_position is None
```

Add to `modules/vision/tests/test_views_create.py`:

```python
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

        with patch("modules.vision.views.services.refresh_job", side_effect=_refreshed):
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

        with patch("modules.vision.views.services.refresh_job", side_effect=_refreshed):
            body = client.get(reverse("vision-job-status", args=[job.id])).content.decode()

        assert "Waiting on the image engine — position 3." in body
```

- [x] **Step 2: Run the tests to verify they fail**

Run: `DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' <repo>/.venv/bin/python -m pytest modules/vision/tests/test_comfyui_generator.py::TestStatus modules/vision/tests/test_services.py::TestRefreshJobCarriesTheEnginePosition modules/vision/tests/test_views_create.py::TestEnginePositionOnTheCard -q`

Expected: FAIL — `JobStatus` has no `queue_position`, `refresh_job` sets no `engine_position`, the card renders no position line.

- [x] **Step 3: Add the field to the platform's `JobStatus`**

In `core/inference/engines/base.py`, in the `JobStatus` dataclass:

```python
    state: str
    error: str | None = None
    progress: float | None = None
    # 1-based position among the jobs the ENGINE has waiting, or `None` for
    # any state or engine that does not genuinely know one. Distinct from
    # `progress`: a position is a fact about a queue, not a fraction of
    # work done, and reporting it as one would be the fabricated bar the
    # field above refuses. `None` is the honest default -- an adapter that
    # cannot see a position simply never sets it.
    queue_position: int | None = None
```

Extend the class docstring with a sentence naming `queue_position` the same way it names `progress`.

- [x] **Step 4: Read the position in the ComfyUI adapter**

In `core/inference/engines/comfyui.py`, add beside `_queue_ref`:

```python
def _pending_position(pending: object, engine_ref: str) -> int | None:
    """1-based position of `engine_ref` among ComfyUI's PENDING entries, in
    the order ComfyUI will actually pop them.

    `/queue` hands back a dump of ComfyUI's own `heapq` list, which is heap
    order, not execution order -- index 0 is next, the rest are not sorted.
    Every entry's FIRST element is the monotonic `number` the heap is keyed
    on (`[number, prompt_id, prompt, extra_data, outputs_to_execute]`), so
    sorting by it reproduces pop order exactly. An entry that does not fit
    that shape is skipped, and one whose `number` is not a number sorts
    last, rather than crashing a poll -- the same tolerance `_queue_ref`
    applies, for the same reason: a position is a display nicety and must
    never fail a live job.
    """
    ordered: list[tuple[float, str]] = []
    for entry in pending or []:
        ref = _queue_ref(entry)
        if ref is None:
            continue
        number = entry[0]
        ordered.append((float(number) if isinstance(number, (int, float)) else float("inf"), ref))
    ordered.sort(key=lambda item: item[0])
    for index, (_number, ref) in enumerate(ordered, start=1):
        if ref == engine_ref:
            return index
    return None
```

and change `_queue_state`'s pending branch:

```python
    def _queue_state(self, engine_ref: str) -> JobStatus:
        """`running` / `queued` / `lost`, from `/queue`.

        A `queued` answer carries the job's real position among the pending
        entries (`_pending_position`); `running` carries none, because a job
        that is running has a state that says more than any number could.
        """
        response = httpx.get(f"{self.endpoint}/queue", timeout=DISCOVERY_TIMEOUT)
        response.raise_for_status()
        queue = response.json() or {}
        for entry in queue.get("queue_running") or []:
            if _queue_ref(entry) == engine_ref:
                return JobStatus(state="running")
        position = _pending_position(queue.get("queue_pending"), engine_ref)
        if position is not None:
            return JobStatus(state="queued", queue_position=position)
        return JobStatus(state="lost")
```

- [x] **Step 5: Carry it onto the job as a transient**

In `modules/vision/services.py`, in `refresh_job`:

```python
    job.unreachable = False
    # TRANSIENT, exactly like `unreachable` above: the card renders it, no
    # column stores it, and a job read straight from the database simply
    # does not have it (Django templates resolve a missing attribute to the
    # empty string, so the card's `{% if %}` is false and nothing renders).
    job.engine_position = None
    if job.is_terminal or not job.engine_ref:
        return job
```

and in the `queued` branch:

```python
    if state.state == "queued":
        job.engine_position = state.queue_position
        return job
```

Extend `refresh_job`'s docstring with a sentence naming `engine_position` alongside `unreachable`.

- [x] **Step 6: Render it in the card's notes band**

In `modules/vision/templates/vision/_job_card.html`, inside `.job-notes`, above the `is_stale` line:

```html
    {% if job.engine_position %}<p class="muted">Waiting on the image engine — position {{ job.engine_position }}.</p>{% endif %}
```

- [x] **Step 7: Run the tests to verify they pass**

Run: `DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' <repo>/.venv/bin/python -m pytest modules/vision/tests/test_comfyui_generator.py::TestStatus modules/vision/tests/test_services.py::TestRefreshJobCarriesTheEnginePosition modules/vision/tests/test_views_create.py::TestEnginePositionOnTheCard -q`

Expected: PASS.

- [x] **Step 8: Run the vision suite and confirm no migration is implied**

Run: `DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' <repo>/.venv/bin/python -m pytest modules/vision -q`
Then: `DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' <repo>/.venv/bin/python manage.py makemigrations --check --dry-run`

Expected: PASS, and `No changes detected`. If a migration IS detected, `engine_position` was declared as a model field by mistake — it must be a plain attribute set in `refresh_job`.

- [x] **Step 9: Commit**

```bash
git add core/inference/engines/base.py core/inference/engines/comfyui.py \
        modules/vision/services.py \
        modules/vision/templates/vision/_job_card.html \
        modules/vision/tests/test_comfyui_generator.py \
        modules/vision/tests/test_services.py \
        modules/vision/tests/test_views_create.py
git commit -m "$(cat <<'EOF'
feat(vision): read the engine's real queue position onto the card

JobStatus gains an optional queue_position; the ComfyUI adapter derives it
by sorting /queue's heap dump on the monotonic number the heap is keyed
on, and refresh_job carries it to the card as a transient attribute.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_<id>
EOF
)"
```

---

### Task 7: Wire `ctx.report_progress` from the wait loop

**Files:**
- Modify: `modules/vision/services.py` (`wait_for` gains `on_poll`)
- Modify: `modules/vision/jobs.py` (`run_generate` reports; module docstring corrected)
- Modify: `docs/adr/0013-inference-execution-queue.md`
- Test: `modules/vision/tests/test_services.py`, `modules/vision/tests/test_jobs.py`

**Interfaces:**
- Produces: `modules.vision.services.wait_for(job, timeout, interval=1.0, on_poll=None) -> GenerationJob`. `on_poll` is `Callable[[GenerationJob, float], None] | None`, called after EVERY refresh with the freshly refreshed job and the seconds elapsed since the wait began. Existing callers are unaffected (`test_services.py`'s two calls and `run_generate`'s own).
- Consumes: `core.inference.jobkinds.JobContext.report_progress(done, total=None, *, unit, label="")` — `unit` must be one of `"seconds"`, `"pages"`, `"items"`; the worker throttles the write to `PROGRESS_INTERVAL_SECONDS = 5`, so calling it on every loop iteration is explicitly fine.

**Why seconds with no total:** ComfyUI reports no fraction over HTTP (Task 6's verified engine facts). `total=None` makes `console.jobs.views._progress_text_and_percent` render `"{done} {label}"` with no bar — `mm:ss` for the `seconds` unit — which is precisely its documented no-fabricated-total path. Passing the 600s wait budget as `total` was rejected: it would draw a bar that fills as the deadline approaches, which is a picture of our patience, not of the generation.

- [x] **Step 1: Write the failing tests**

Add to `modules/vision/tests/test_services.py`:

```python
@pytest.mark.django_db
class TestWaitForReportsEachPoll:
    """The loop is the only place that knows a poll happened, so the
    reporting seam belongs to it. `TestWaitFor` above pins the unchanged
    no-callback behaviour; these pin the new argument."""

    def test_on_poll_runs_for_every_refresh_with_the_job_and_elapsed_seconds(self, tmp_path):
        _bind()
        generator = StubGenerator(
            states=[JobStatus(state="running"), JobStatus(state="done")],
            outputs=[("a.png", PNG, "image/png")],
        )
        seen = []

        with _registered(StubEngine(generator)), override_settings(GENERATED_DIR=tmp_path):
            job = services.submit_job("txt2img", dict(RAW))
            services.wait_for(
                job, timeout=5, interval=0,
                on_poll=lambda polled, elapsed: seen.append((polled.status, elapsed)),
            )

        assert [status for status, _elapsed in seen] == [
            GenerationJob.Status.RUNNING,
            GenerationJob.Status.DONE,
        ]
        assert all(elapsed >= 0.0 for _status, elapsed in seen)

    def test_a_caller_that_passes_no_callback_is_unaffected(self):
        _bind()
        generator = StubGenerator(states=[JobStatus(state="queued")])

        with _registered(StubEngine(generator)):
            job = services.submit_job("txt2img", dict(RAW))
            waited = services.wait_for(job, timeout=0, interval=0)

        assert waited.status == GenerationJob.Status.QUEUED
```

Add to `modules/vision/tests/test_jobs.py`, inside `class TestRunGenerateEndToEnd`:

```python
    def test_the_handler_reports_progress_from_the_wait_loop(self, tmp_path):
        """T3's seam, wired: the queue page shows a live "0:07 generating"
        line for a running generation instead of nothing at all. Seconds
        with NO total, because ComfyUI reports no fraction over HTTP -- a
        percentage here would be invented.

        The engine is scripted to be RUNNING on the first poll and finished
        by the second, which is the only shape that produces a reportable
        tick: the terminal tick reports nothing (see `run_generate`'s own
        `report`)."""
        _bind_comfyui()
        fake = FakeComfyUI(
            prompt_id="p-99",
            queue_running=[[0, "p-99", {}, {}, []]],
            images={"out.png": PNG},
        )
        real_get = fake.get
        reported = []

        def scripted_get(url, params=None, timeout=None):
            """Script the SERVER, not the adapter: the job is running when
            the first poll asks, and its history has landed before the
            second. The adapter's real URL building and parsing still run."""
            response = real_get(url, params=params, timeout=timeout)
            if "/queue" in url:
                fake.history = history_success("p-99", filenames=("out.png",))
                fake.queue_running = []
            return response

        with patch("core.inference.engines.comfyui.httpx.get", scripted_get), patch(
            "core.inference.engines.comfyui.httpx.post", fake.post
        ), patch("modules.vision.services.time.sleep"), override_settings(
            GENERATED_DIR=tmp_path
        ):
            jobs.run_generate(
                {"operation": "txt2img", "params": dict(RAW)},
                [],
                make_job_ctx(_report=reported.append),
            )

        assert reported, "run_generate must report at least one progress tick"
        assert reported[-1]["unit"] == "seconds"
        assert reported[-1]["total"] is None
        assert reported[-1]["label"] == "generating"
        assert reported[-1]["done"] >= 0

    def test_a_finished_job_gets_no_final_progress_tick(self, tmp_path):
        """A generation already done on its first poll reports nothing:
        there is no honest label for a terminal tick."""
        _bind_comfyui()
        fake = FakeComfyUI(
            prompt_id="p-99",
            history=history_success("p-99", filenames=("out.png",)),
            images={"out.png": PNG},
        )
        reported = []

        with patch("core.inference.engines.comfyui.httpx.get", fake.get), patch(
            "core.inference.engines.comfyui.httpx.post", fake.post
        ), override_settings(GENERATED_DIR=tmp_path):
            jobs.run_generate(
                {"operation": "txt2img", "params": dict(RAW)},
                [],
                make_job_ctx(_report=reported.append),
            )

        assert reported == []

    def test_a_still_queued_generation_reports_the_queued_label(self, tmp_path):
        _bind_comfyui()
        fake = FakeComfyUI(prompt_id="p-1", queue_pending=[[1, "p-1", {}, {}, []]])
        reported = []

        with patch("core.inference.engines.comfyui.httpx.get", fake.get), patch(
            "core.inference.engines.comfyui.httpx.post", fake.post
        ), patch("modules.vision.jobs.GENERATE_WAIT_TIMEOUT_SECONDS", 0.0):
            jobs.run_generate(
                {"operation": "txt2img", "params": dict(RAW)},
                [],
                make_job_ctx(_report=reported.append),
            )

        assert reported[-1]["label"] == "waiting on the image engine"
```

- [x] **Step 2: Run the tests to verify they fail**

Run: `DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' <repo>/.venv/bin/python -m pytest modules/vision/tests/test_services.py::TestWaitForReportsEachPoll modules/vision/tests/test_jobs.py::TestRunGenerateEndToEnd -q`

Expected: FAIL — `wait_for() got an unexpected keyword argument 'on_poll'`, and `reported` is empty.

- [x] **Step 3: Add the `on_poll` seam**

Replace `modules/vision/services.py::wait_for` with:

```python
def wait_for(
    job: GenerationJob,
    timeout: float,
    interval: float = 1.0,
    on_poll: Callable[[GenerationJob, float], None] | None = None,
) -> GenerationJob:
    """Poll `refresh_job` until the job is terminal or `timeout` seconds
    pass, then return it as-is.

    For SYNCHRONOUS callers (tests, a future chatbot tool, a management
    command). The page never uses this -- it polls from the browser instead,
    so a request thread is never held open by a generation.

    `on_poll` (optional) is called after EVERY refresh with
    `(job, elapsed_seconds)`. This loop is the only place that knows a poll
    happened, which is why the seam is here rather than inside
    `refresh_job` (which a page also calls, once, per request, and which
    has no notion of elapsed time). `modules.vision.jobs.run_generate`
    passes the callback that turns each tick into a
    `JobContext.report_progress` call; every other caller passes nothing
    and this loop behaves exactly as it did. An exception raised by
    `on_poll` is deliberately NOT swallowed: a reporter is the caller's own
    code, and a silent except here would hide a bug in it behind a
    generation that looks fine.
    """
    started = time.monotonic()
    deadline = started + timeout
    while True:
        job = refresh_job(job)
        if on_poll is not None:
            on_poll(job, time.monotonic() - started)
        if job.is_terminal or time.monotonic() >= deadline:
            return job
        time.sleep(interval)
```

Add `from collections.abc import Callable` to the module's imports if it is not already there (check the top of the file; `from __future__ import annotations` is already present, so the annotation itself is lazy either way).

- [x] **Step 4: Report from the handler**

In `modules/vision/jobs.py`, add above `run_generate`:

```python
# What the queue page CALLS the phase a generation is in, per
# `GenerationJob.Status`. Short, kind-owned words, exactly what
# `JobContext.report_progress`'s `label` is for -- the numbers beside them
# are elapsed seconds, which is the only quantity ComfyUI's HTTP surface
# genuinely lets us count (no per-step fraction exists there: history is
# written at task_done, and per-node progress is WebSocket-only).
_PROGRESS_LABELS = {
    GenerationJob.Status.QUEUED: "waiting on the image engine",
    GenerationJob.Status.RUNNING: "generating",
}
```

Replace the `wait_for` call in `run_generate`:

```python
    def report(polled: GenerationJob, elapsed: float) -> None:
        """One `wait_for` tick -> one progress report. Called on EVERY poll;
        the worker throttles the actual database write itself
        (`console.jobs.worker.PROGRESS_INTERVAL_SECONDS`), which is exactly
        why `JobContext`'s docstring invites a handler to call this from a
        loop without reasoning about write cost.

        `total=None` on purpose: nothing in ComfyUI's HTTP surface reports
        how much of a generation is done, and the wall-clock budget is our
        patience, not the work -- either as a denominator would draw a bar
        that means nothing. `_progress_text_and_percent` renders a
        total-less report as bare `mm:ss` plus the label, with no bar.

        A TERMINAL tick reports nothing at all: `wait_for` calls back after
        every refresh, including the one that found the job done or failed,
        and there is no honest label for that tick -- `_PROGRESS_LABELS`
        has no word for a finished job, so it would fall through to
        "generating" and describe the job as still working at the exact
        moment it stopped. The queue's own terminal writeback is what has
        the last word about a finished job.
        """
        if polled.is_terminal:
            return
        ctx.report_progress(
            int(elapsed),
            total=None,
            unit="seconds",
            label=_PROGRESS_LABELS.get(polled.status, "generating"),
        )

    job = services.wait_for(
        job, timeout=GENERATE_WAIT_TIMEOUT_SECONDS, on_poll=report
    )
```

- [x] **Step 5: Correct the module and function docstrings**

In `modules/vision/jobs.py`, `run_generate`'s docstring paragraph beginning "`ctx` (T3, ...)" currently states that `ctx.report_progress` "is still not wired". Replace that paragraph with:

```
    `ctx` (T3, `core.inference.jobkinds.JobContext`): `ctx.job_id` is
    stamped onto the `GenerationJob` for correlation once `submit_job`
    returns (see the comment at that call site). `ctx.report_progress` is
    wired through `services.wait_for`'s `on_poll` seam (added 2026-08-25
    for exactly this): every poll of the wait loop reports the elapsed
    seconds and the phase word for the generation's current status, so the
    Queue page shows a live line for a running generation instead of
    nothing. `total` is always `None` -- ComfyUI's HTTP surface reports no
    fraction of a generation (history lands only at `task_done`; per-node
    progress is WebSocket-only), and the engine's own queue position, which
    it CAN report, travels on `JobStatus.queue_position` to the job card
    instead, because a position is not a fraction.
```

Also update step 3 of the numbered list in the same docstring to mention `on_poll`.

- [x] **Step 6: Run the tests to verify they pass**

Run: `DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' <repo>/.venv/bin/python -m pytest modules/vision/tests/test_services.py::TestWaitForReportsEachPoll modules/vision/tests/test_jobs.py::TestRunGenerateEndToEnd -q`

Expected: PASS.

- [x] **Step 7: Run the vision and console suites**

Run: `DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' <repo>/.venv/bin/python -m pytest modules/vision console/jobs -q`

Expected: PASS.

- [x] **Step 8: Document it**

In `docs/adr/0013-inference-execution-queue.md`, at the bullet that records progress landing (~line 304), append:

```markdown
  `vision.generate` is the first kind to USE it (2026-08-25): its wait loop
  reports elapsed seconds with `total=None` and a phase label
  ("waiting on the image engine" / "generating"), because ComfyUI's HTTP
  surface exposes no fraction of a generation. The Queue page therefore
  shows a live `mm:ss` line and no bar, which is the honest rendering
  `_progress_text_and_percent` was written for.
```

In `docs/DEV.md`, `## Generate images (/vision/)`, add after the numbered steps:

```markdown
While a generation runs, `/queue/` shows a live elapsed line for it, and
the job card names the engine's own queue position when the image engine
still has the job waiting. Neither shows a percentage: ComfyUI reports no
per-step progress over HTTP, and a made-up bar is worse than none.

Two different "position N" numbers can appear, and they count different
queues. *Waiting in the queue — position N* on a queued placeholder is
farabunker's OWN execution queue (`/queue/`): how many submissions are
ahead of yours here. *Waiting on the image engine — position N* on a job
card is ComfyUI's queue: farabunker has already handed the job over, and
the engine has N-1 other prompts — possibly submitted from ComfyUI's own
interface — to run first.
```

- [x] **Step 9: Commit**

```bash
git add modules/vision/services.py modules/vision/jobs.py \
        modules/vision/tests/test_services.py modules/vision/tests/test_jobs.py \
        docs/adr/0013-inference-execution-queue.md docs/DEV.md
git commit -m "$(cat <<'EOF'
feat(vision): report generation progress from the wait loop

wait_for grows an on_poll seam; run_generate turns each tick into
ctx.report_progress(elapsed, total=None, unit="seconds", label=phase), so
/queue/ shows a live line for a running generation. No fabricated total --
ComfyUI's HTTP surface has no fraction to report.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_<id>
EOF
)"
```

---

### Task 8: `Param.description` as form hints

**Files:**
- Modify: `modules/vision/forms.py` (`_field_for` wrapper)
- Modify: `modules/vision/templates/vision/create.html` (hint on the prompt fields; hint CSS)
- Modify: `docs/adr/0012-image-generation-engine-adapter.md`
- Test: `modules/vision/tests/test_forms.py`, `modules/vision/tests/test_views_create.py`

**Interfaces:**
- Consumes: `core.inference.operations.Param.description: str` (already populated across `TXT2IMG`, `IMG2IMG`, `INPAINT`, `UPSCALE`).
- Produces: `modules.vision.forms._base_field_for(param, engine_options) -> forms.Field` (today's `_field_for` body, renamed) and a new `_field_for(param, engine_options) -> forms.Field` that applies the description as `help_text` only where the field has none.

- [x] **Step 1: Write the failing tests**

Add to `modules/vision/tests/test_forms.py`:

```python
class TestSchemaDescriptionsBecomeHints:
    """The schema already carries a sentence per param. Situational copy
    still WINS -- a seed field's "leave blank for a random seed" and an
    asset field's "nothing installed" explain the situation the operator is
    actually in, which a generic description cannot."""

    def test_a_param_description_becomes_the_fields_help_text(self):
        form = build_form(TXT2IMG, {})

        width = next(param for param in TXT2IMG.params if param.key == "width")
        assert form.fields["width"].help_text == width.description

    def test_situational_copy_is_not_overwritten(self):
        form = build_form(TXT2IMG, {})

        assert form.fields["seed"].help_text == "Leave blank for a random seed."

    def test_a_param_with_no_description_gets_no_hint(self):
        operation = Operation(
            "nodesc", "No description", "image-generation",
            (Param("steps", "int", "Steps", default=20),),
            "image/png",
        )

        form = build_form(operation, {})

        assert form.fields["steps"].help_text == ""
```

`TXT2IMG`, `Operation`, `Param` and `build_form` are already imported at the top of `test_forms.py`; the positional `Operation(...)` shape above is the one that module's other literals use (line ~65).

Add to `modules/vision/tests/test_views_create.py`:

```python
@pytest.mark.django_db
class TestFieldHintsRender:
    def test_the_page_shows_a_hint_under_a_described_field(self, client):
        body = client.get(reverse("vision-create")).content.decode()

        width = next(param for param in TXT2IMG.params if param.key == "width")
        assert width.description[:40] in body

    def test_the_prompt_field_shows_its_hint_too(self, client):
        body = client.get(reverse("vision-create")).content.decode()

        prompt = next(param for param in TXT2IMG.params if param.key == "prompt")
        assert prompt.description[:40] in body
```

- [x] **Step 2: Run the tests to verify they fail**

Run: `DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' <repo>/.venv/bin/python -m pytest modules/vision/tests/test_forms.py::TestSchemaDescriptionsBecomeHints modules/vision/tests/test_views_create.py::TestFieldHintsRender -q`

Expected: FAIL — help text is empty for `width`, and the prompt field renders no hint at all (`create.html`'s prompt block never rendered `help_text`).

- [x] **Step 3: Apply the description as a fallback help text**

In `modules/vision/forms.py`, rename the existing `def _field_for(param, engine_options) -> forms.Field:` to `def _base_field_for(param: Param, engine_options: dict[str, tuple[str, ...]]) -> forms.Field:` (body unchanged, including its docstring), and add immediately after it:

```python
def _field_for(param: Param, engine_options: dict[str, tuple[str, ...]]) -> forms.Field:
    """`_base_field_for`, plus the SCHEMA's own sentence as help text
    wherever the field does not already carry copy of its own.

    Situational copy wins, which is the ruling this phase inherits: a seed's
    "Leave blank for a random seed." and an asset field's "no LoRA is
    available — the image engine has none installed" both describe the
    situation the operator is in right now, and a generic parameter
    description would be a downgrade. Everything else -- the great majority
    of params, all of which `core.inference.operations` already describes --
    gets the schema's sentence rather than nothing.

    Applied HERE, in one place, rather than inside each of the seven
    branches above: a new param kind gets this behaviour for free, and no
    branch can forget it.
    """
    field = _base_field_for(param, engine_options)
    if not field.help_text and param.description:
        field.help_text = param.description
    return field
```

- [x] **Step 4: Render the hint on the prompt fields too**

In `modules/vision/templates/vision/create.html`, the prompt/negative-prompt block becomes:

```html
  {% for field in form %}
    {% if field.name == "prompt" or field.name == "negative_prompt" %}
      <div>
        <label for="{{ field.id_for_label }}">{{ field.label }}</label>
        {{ field }}
        {% if field.help_text %}<span class="muted field-hint">{{ field.help_text }}</span>{% endif %}
        {{ field.errors }}
      </div>
    {% endif %}
  {% endfor %}
```

and the `.row` loop's existing hint gains the class:

```html
        {% if field.help_text %}<span class="muted field-hint">{{ field.help_text }}</span>{% endif %}
```

Add to `create.html`'s `{% block vision_style %}`:

```css
  /* Schema-authored hints (`Param.description`). A block so a sentence
     sits under its field instead of colliding with the next label. */
  .field-hint { display: block; margin-top: 0.25rem; line-height: 1.35; }
```

- [x] **Step 5: Run the tests to verify they pass**

Run: `DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' <repo>/.venv/bin/python -m pytest modules/vision/tests/test_forms.py modules/vision/tests/test_views_create.py -q`

Expected: PASS, including the whole existing form suite — the asset-missing tests must still see their own `missing` sentence, which is what the "situational copy wins" branch protects.

- [x] **Step 6: Document it**

In `docs/adr/0012-image-generation-engine-adapter.md`, near the schema-driven-form bullet (~line 341), append:

```markdown
- **`Param.description` reaches the form, amended 2026-08-25.** Every
  described param renders its sentence as the field's help text; a field
  that already carries SITUATIONAL copy (the seed's "leave blank", an
  asset kind with nothing installed) keeps it. Applied once in
  `forms._field_for`, so a new param kind inherits the behaviour.
```

- [x] **Step 7: Commit**

```bash
git add modules/vision/forms.py modules/vision/templates/vision/create.html \
        modules/vision/tests/test_forms.py modules/vision/tests/test_views_create.py \
        docs/adr/0012-image-generation-engine-adapter.md
git commit -m "$(cat <<'EOF'
feat(vision): surface Param.description as form hints

Every described param renders its schema sentence as help text; fields
with situational copy of their own keep it. The prompt fields render a
hint at all for the first time.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_<id>
EOF
)"
```

---

### Task 9: Docs sweep and full verification

**Files:**
- Modify: `docs/ROADMAP.md`
- Modify: `docs/superpowers/plans/2026-08-25-vision-ui-cleanup.md` (this file — check off completed steps only)
- Otherwise: none, unless a failure demands it.

- [x] **Step 1: Update the roadmap line**

In `docs/ROADMAP.md`, the `/vision/` module bullet (~line 88) reads "prompt-to-image page, poll-driven job cards with a no-JS fallback, a gallery with 'reuse settings', and generic job/output records". Replace it with:

```markdown
- [x] **`/vision/` module** — prompt-to-image page, poll-driven job cards with a no-JS
  fallback (four-band cards: prompt + status chip, media beside a labelled facts
  panel, the same Use-in/Reuse/Download actions the gallery offers from one shared
  partial), a gallery, live queue-position and elapsed-time reporting, and generic
  job/output records
```

- [x] **Step 2: Confirm no migration was introduced**

Run: `DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' <repo>/.venv/bin/python manage.py makemigrations --check --dry-run`

Expected: `No changes detected`. A detected change means a transient attribute was declared as a model field — fix that, do not generate a migration.

- [x] **Step 3: Full suite, default order**

Run: `DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' <repo>/.venv/bin/python -m pytest -q`

Expected: PASS, at or above the baseline MEASURED before Task 1 (written here as `1865 passed / 1 skipped`, which must be re-measured rather than assumed), plus the ~35 tests this plan adds; it removes none.

- [x] **Step 4: Full suite, the other order**

Run: `DATABASE_URL='postgres://farabunker:farabunker@localhost:5435/farabunker' <repo>/.venv/bin/python -m pytest console modules scripts -q`

Expected: PASS with the SAME counts as Step 3. This is the same set of tests (`pytest.ini`'s `testpaths` is `modules console scripts`), collected in a different order — `console/` first, so registries the vision app populates at import time are exercised in the opposite sequence. Naming the paths explicitly is the only difference.

- [x] **Step 5: Read both counts and compare**

Write down both `N passed, M skipped` lines. They must match. If they do not, a test is order-dependent — fix the TEST (usually an unscoped `patch.dict` on a registry), never the ordering.

- [x] **Step 6: Walk the spec**

Name the task that closed each spec item: action parity → Task 1; card redesign → Tasks 2, 3, 4; queued-placeholder polish → Task 5; `ctx.report_progress` wiring → Tasks 6, 7; `Param.description` hints → Task 8. Report anything unaccounted for as a gap rather than quietly leaving it. Note explicitly, in the report, which of the following the implementation did NOT do and why: no percent is shown anywhere for a generation (ComfyUI reports no fraction over HTTP); the vision cards show phase + engine queue position rather than a numeric elapsed clock (the `mm:ss` formatter lives in `console/jobs/views.py` and `modules/` may not import `console.*`, so duplicating it for a card was declined).

- [x] **Step 7: Report, do not claim**

Report both suite results verbatim, the migration check output, and the spec walk. Per the verification doctrine, use NO "done"/"working"/"fixed" language about the page until someone has loaded the branch preview at `:8002`, submitted a generation, and watched the queued card become a real card with its actions, facts panel, and chip. That browser walk is the orchestrator's, and it is the gate — not this suite.

- [x] **Step 8: Commit the docs sweep**

```bash
git add docs/ROADMAP.md docs/superpowers/plans/2026-08-25-vision-ui-cleanup.md
git commit -m "$(cat <<'EOF'
docs: record the vision UI cleanup phase

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_<id>
EOF
)"
```

---

## Open questions for the owner

1. **No percentage anywhere.** ComfyUI 0.33.0 genuinely reports no fraction of a generation over HTTP (verified against the installed source: history is written at `task_done`; `/api/jobs/<id>` has no progress field; per-node progress is WebSocket-only). The plan therefore ships elapsed seconds on `/queue/` and the engine's real queue position on the card. A true progress bar would need a WebSocket client in the adapter — a separate, larger piece of work.
2. **Elapsed time on the card.** `/queue/` shows `mm:ss`; the card does not, because the formatter lives in `console/jobs/views.py` and `modules/` may not import `console.*`. If the owner wants a clock on the card, the cheapest honest options are a third home for the formatter (`core/`) or Django's `|timesince`, which renders "0 minutes" for a 40-second generation.
3. **Actions repeat per output tile.** The shared partial is output-scoped, so a batch of four images shows four action rows — the gallery already behaves exactly this way. If that reads as noise on a batch, the alternative is a job-level row plus per-tile Download, which costs the exact parity the owner asked for.

---

## Review history

**r1 (2026-08-24) — verdict AMEND, 6 findings + 2 nits, all applied in one round.**

- **F1 — CSS insertion-point collision.** Task 1 Step 6 said "after the `.job-inputs img` rule" and Task 4 Step 4 said "replace … lines 41–46"; executed literally, Task 4 would have wiped or split the `.output-actions` and `.job-facts` blocks Tasks 1–2 appended inside that range. Task 1 Step 6 now says to append AFTER the `.job-error` rule (line 46, the block's last), and Task 4 Step 4 replaces the six rules BY NAME (`.job-card img`, `.job-images`, `.job-images img`, `.job-inputs img`, `.job-head`, `.job-error`), explicitly forbidding a line-number replacement and requiring everything after them to survive.
- **F2 — the progress label lied on the terminal tick.** `wait_for` calls back after the refresh that finds a job done, and `_PROGRESS_LABELS` has no word for a finished job, so that tick would have fallen through to "generating" at the exact moment the job stopped. `run_generate`'s `report` now returns early on `polled.is_terminal`, with the reason written into its docstring. The happy-path test was rewritten to script the engine RUNNING-then-finished (so a genuine reportable tick exists) and asserts `label == "generating"` outright; a second test pins that an already-finished generation reports nothing at all.
- **F3 — dead defensive code.** Dropped `.job-notes:empty { display: none; }` (whitespace text nodes make `:empty` unmatchable, and `padding: 0 1rem` already gives an empty band zero height) and dropped Task 5 Step 8's warning about positionally-constructed `JobStatus` objects in tests (verified: `console/jobs/backend.py:230` is the only construction in the repo and it is keyword-only, so the warning would have sent an implementer hunting for nothing).
- **F4 — test-count arithmetic.** Task 1 Step 7 said "4 + 3"; `TestCardActionParity` has 3 tests and `TestUseInLinks` ends with 3 → now "3 + 3". Task 5 Step 7 said 5; it is 6 (3 `TestJobStatusSummary` + 3 `TestQueuedCardNamesTheWork`).
- **F5 — a step heading demanding a failure its own body denies.** Task 3 Step 2 is renamed "Run the characterization test and record that it passes", with a sentence saying this is the one step in the plan that does not start red and why.
- **F6 — registry-order determinism.** `input_targets()` reads the live operation registry, so the parity tests now pin it with the same `patch.dict(operations._OPERATIONS, {"txt2img": TXT2IMG, "img2img": IMG2IMG})` its sibling at `modules/vision/tests/test_views_gallery.py:234` uses. Applied to both card tests that assert a specific mode's link (the create-page test named in the finding and the poll-fragment test beside it, which asserts the same string for the same reason).
- **Nit 1 — queued-card spacing.** The placeholder has no body and no foot, so its notes band now pads top as well as bottom: `.job-notes:last-child { padding-top: 0.5rem; padding-bottom: 0.5rem; }`.
- **Nit 2 — two different "position N" numbers.** Task 7's `docs/DEV.md` block now distinguishes them: *Waiting in the queue — position N* counts farabunker's own execution queue, *Waiting on the image engine — position N* counts ComfyUI's, which may also hold prompts submitted from ComfyUI's own interface.
- **Also applied:** the `1865 passed / 1 skipped` baseline is now marked **re-measure, do not assume** in both places it appears (Global Constraints and Task 9 Step 3) — the branch may have moved since this plan was written, so the floor is whatever a full run reports before Task 1.
