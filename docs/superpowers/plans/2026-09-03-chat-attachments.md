# Chat Attachments Implementation Plan

> **STATUS:** historical: superseded in part; landed as the integration in `2026-09-20-attachments-integration-land.md`.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `/chat/` turns accept file attachments (every RAG-ingestible type); the agent both understands them (capability-gated native multimodal, extraction fallback) and operates on them (vision img2img; ingest-into-the-library is CUT — see Task 8's blocker note).

**Architecture:** Conversation-scoped `ConversationFile` rows on the ADR 0009 managed-store shape; a new `file:<id>` artifact kind; extraction inline in the `agent.turn` job with a hash-validated `extract.json` sidecar; `file:` refs rewritten to vision's own `input:<id>` at the tool boundary via `resolve_dotted_path` (import law intact).

**Tech Stack:** Django 5 / Python 3.13, pytest (`pytest.mark.django_db`), LlamaIndex ChatMessage blocks, existing seams in `tools/rag` (readers/extract/transcode/sidecar) and `tools/vision` (stage_upload).

**Spec:** `docs/superpowers/specs/2026-09-03-chat-attachments-design.md` — read it first; it argues every decision here.

## Global Constraints

- Branch: `chat-attachments` off main `4abef8a`. Work ONLY in the worktree `<worktree>` — NEVER in `<repo>` itself (that checkout IS the live deployment).
- Import law: `agents/*` NEVER imports `tools.*` — reach tools seams via `resolve_dotted_path` (`models.contracts.jobkinds`), the exact pattern `agents/runtime/loop.py:456-540` uses [range updated post-review: 576-605]. `agents/chat` additionally never imports `agents.runtime` internals beyond what it already does.
- No `conftest.py` anywhere (repo rule). Test helpers live in each app's `tests/_helpers.py`.
- No Django signals; filesystem cleanup happens in the service delete path (the `delete_document` precedent).
- All comments/docstrings explain WHY with file:line citations, matching the house style you see in every file you open.
- Test command (native venv, never docker): `<repo>/.venv/bin/pytest` — run from the WORKTREE root. Full gates at the end: `pytest -q` AND reversed-order `pytest -q scripts foundation models tools`.
- Every task ships its docs with its code; commit per task with a conventional-commit message.
- ONE touch OUTSIDE the agents zone (Task 6's `models/contracts/catalog.py` `accepts` field) is additive-only; the orchestrator announces it to the peer session before merge — implementers just build it. `tools/rag/tools.py` is NOT touched by this branch (Task 8 is cut).

---

### Task 1: Settings + conversation-file store helper

**Files:**
- Modify: `config/settings.py` (after line 180, beside `DOCUMENTS_DIR`)
- Create: `agents/store.py`
- Test: `agents/tests/test_store.py`

**Interfaces:**
- Produces: `settings.CONVERSATION_FILES_DIR: Path`; `settings.CHAT_ATTACHMENTS_PER_TURN: int` (default 10); `settings.CHAT_ATTACHMENT_MAX_BYTES: int` (default 512 MiB); `settings.CHAT_ATTACHMENT_EXCERPT_CHARS: int` (default 6000).
- Produces: `agents.store.file_dir(conversation_id, file_pk) -> Path`; `store_upload(conversation_id, file_pk, uploaded) -> tuple[str, str, int]` returning `(abs_path, sha256_hex, size_bytes)`; `remove_conversation_files(conversation_id) -> None`; `basename(name) -> str` (the untrusted-filename normalizer -- public because Task 4's service stamps `original_name` with it).

- [ ] **Step 1: Write the failing tests**

```python
# agents/tests/test_store.py
"""The conversation-file store: layout, chunked writes, guarded removal."""
from __future__ import annotations

import io
import uuid
from pathlib import Path

from django.conf import settings

from agents import store


class FakeUpload:
    """The three attributes the writer reads off a Django UploadedFile."""
    def __init__(self, name: str, data: bytes, content_type: str = "text/plain"):
        self.name = name
        self.content_type = content_type
        self._data = data
    def chunks(self):
        yield self._data


class TestTheStore:
    def test_file_dir_is_the_documents_dir_shape(self):
        cid = uuid.uuid4()
        assert store.file_dir(cid, 7) == settings.CONVERSATION_FILES_DIR / str(cid) / "7"

    def test_store_upload_writes_bytes_and_returns_path_hash_size(self, tmp_path, settings):
        settings.CONVERSATION_FILES_DIR = tmp_path
        cid = uuid.uuid4()
        path, digest, size = store.store_upload(cid, 3, FakeUpload("notes.txt", b"hello"))
        assert Path(path).read_bytes() == b"hello"
        assert Path(path).name == "notes.txt"
        assert size == 5
        import hashlib
        assert digest == hashlib.sha256(b"hello").hexdigest()

    def test_a_windows_style_filename_is_reduced_to_its_basename(self, tmp_path, settings):
        settings.CONVERSATION_FILES_DIR = tmp_path
        path, _, _ = store.store_upload(uuid.uuid4(), 1, FakeUpload("C:\\evil\\..\\x.txt", b"x"))
        assert Path(path).name == "x.txt"

    def test_remove_conversation_files_deletes_only_that_conversations_tree(self, tmp_path, settings):
        settings.CONVERSATION_FILES_DIR = tmp_path
        keep, gone = uuid.uuid4(), uuid.uuid4()
        store.store_upload(keep, 1, FakeUpload("a.txt", b"a"))
        store.store_upload(gone, 1, FakeUpload("b.txt", b"b"))
        store.remove_conversation_files(gone)
        assert not (tmp_path / str(gone)).exists()
        assert (tmp_path / str(keep) / "1" / "a.txt").exists()

    def test_removing_an_unknown_conversation_is_a_quiet_no_op(self, tmp_path, settings):
        settings.CONVERSATION_FILES_DIR = tmp_path
        store.remove_conversation_files(uuid.uuid4())  # must not raise
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv-relative pytest agents/tests/test_store.py -v` (use the venv path from Global Constraints)
Expected: FAIL — `ImportError: cannot import name 'store'` / missing settings.

- [ ] **Step 3: Implement settings**

In `config/settings.py`, directly after the `DOCUMENTS_DIR` line (line 180), add:

```python
# Conversation-scoped attachment store (chat-attachments spec, 2026-09-03):
# `<DATA_DIR>/conversations/<conversation-uuid>/<ConversationFile pk>/<basename>`
# -- the DOCUMENTS_DIR/<doc_id>/<basename> shape (ADR 0009) one level deeper,
# because a conversation owns MANY files and each keeps its own directory so
# same-named uploads never collide. Host-mounted under DATA_DIR (ADR 0006).
CONVERSATION_FILES_DIR = DATA_DIR / "conversations"

# Upload caps for one chat turn -- declared ONCE, in Python, and read by
# `agents.chat.service.start_turn`; never retyped in a template or script
# (the poll-tuning precedent, agents/chat/service.py).
CHAT_ATTACHMENTS_PER_TURN = _optional_int("CHAT_ATTACHMENTS_PER_TURN") or 10
CHAT_ATTACHMENT_MAX_BYTES = (
    _optional_int("CHAT_ATTACHMENT_MAX_MB") or 512
) * 1024 * 1024

# How much extracted text of one attachment is injected into the model's
# prompt (spec section 3). A v1 honesty cap, NOT the end state -- the spec's
# "Named future seam" section records retrieval-over-attachment as the real
# answer for heavyweight documents.
CHAT_ATTACHMENT_EXCERPT_CHARS = _optional_int("CHAT_ATTACHMENT_EXCERPT_CHARS") or 6000
```

- [ ] **Step 4: Implement the store module**

```python
# agents/store.py
"""The conversation-file store (chat-attachments spec section 1).

Single source of truth for where a conversation's attached files live on
disk, exactly as `tools/rag/store.py` is for documents (ADR 0009) --
nothing else touches this layout directly. The chunked writer and the
basename normalizer are modelled line-for-line on
`tools/vision/store.py:66-93` (`_basename`/`_write_upload`); a shared
copy across the column boundary would be an import the law forbids, the
same reasoning `agents/chat/views/turns.py::_is_xhr` records.
"""
from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

from django.conf import settings


def file_dir(conversation_id, file_pk) -> Path:
    """`<CONVERSATION_FILES_DIR>/<conversation uuid>/<file pk>/` -- one
    directory per file so two same-named uploads never collide (the
    vision staging rule, tools/vision/store.py:60-63)."""
    return settings.CONVERSATION_FILES_DIR / str(conversation_id) / str(file_pk)


def basename(name: str) -> str:
    """The last path component, whichever separator style -- an uploaded
    filename is untrusted; see tools/vision/store.py:69-77."""
    return Path(name.replace("\\", "/")).name


def store_upload(conversation_id, file_pk, uploaded) -> tuple[str, str, int]:
    """Write `uploaded` into its own directory; return
    `(abs_path, sha256_hex, size_bytes)`. Hash and size are computed in
    the same chunked pass as the write -- one read of the upload, never
    two."""
    dest_dir = file_dir(conversation_id, file_pk)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_path = dest_dir / basename(uploaded.name)
    digest = hashlib.sha256()
    size = 0
    with open(dest_path, "wb") as handle:
        for chunk in uploaded.chunks():
            handle.write(chunk)
            digest.update(chunk)
            size += len(chunk)
    return str(dest_path), digest.hexdigest(), size


def remove_conversation_files(conversation_id) -> None:
    """Delete one conversation's whole attachment tree, if any.

    Resolved from the setting and the id -- never from a row's stored
    path -- so a corrupted `path` column can never aim this at the wrong
    directory (the `remove_staged_input` caution, tools/vision/store.py)."""
    tree = settings.CONVERSATION_FILES_DIR / str(conversation_id)
    if tree.is_dir():
        shutil.rmtree(tree, ignore_errors=True)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest agents/tests/test_store.py -v` → all PASS.

- [ ] **Step 6: Commit**

```bash
git add config/settings.py agents/store.py agents/tests/test_store.py
git commit -m "feat(chat): conversation-file store + attachment settings"
```

---

### Task 2: ConversationFile model, migration, ownership, delete cleanup

**Files:**
- Modify: `agents/models.py` (after `Conversation`, before `ToolInvocation`)
- Create: `agents/migrations/0006_conversationfile.py` (via `makemigrations`; confirm 0006 is free: `ls agents/migrations/` — 0005 is the latest on this base)
- Modify: `agents/apps.py:107` (register `OwnedRows` beside the Conversation line)
- Modify: `identity/tests/test_owned_rows_registry.py` (the exact-set pin gains the new key)
- Modify: `agents/visibility.py::delete_conversation` (lines 268-287)
- Test: `agents/tests/test_models.py` (append), `agents/tests/test_apps.py` (ownership registration follows the existing pattern there)

**Interfaces:**
- Produces: `agents.models.ConversationFile` with fields `conversation` (FK, CASCADE, related_name `"files"`), `path: str`, `original_name: str`, `media_type: str`, `file_hash: str` (indexed), `size_bytes: int`, `owner_kind`/`owner_key` (blank defaults, IA-1 shape), `created_at`.

- [ ] **Step 1: Write the failing tests** (append to `agents/tests/test_models.py`)

```python
class TestConversationFile:
    def test_rows_cascade_with_their_conversation(self, tmp_path, settings):
        from agents.models import ConversationFile
        settings.CONVERSATION_FILES_DIR = tmp_path
        conversation = make_conversation()
        row = ConversationFile.objects.create(
            conversation=conversation, path="", original_name="a.txt",
            media_type="text/plain", file_hash="0" * 64, size_bytes=1,
        )
        conversation.delete()
        assert not ConversationFile.objects.filter(pk=row.pk).exists()

    def test_delete_conversation_removes_the_disk_tree_too(self, tmp_path, settings):
        from agents import store
        from agents.models import ConversationFile
        from agents.visibility import delete_conversation
        from identity.contracts.principals import Principal
        settings.CONVERSATION_FILES_DIR = tmp_path
        conversation = make_conversation(owner_kind="user", owner_key="u1")
        row = ConversationFile.objects.create(
            conversation=conversation, path="", original_name="a.txt",
            media_type="text/plain", file_hash="0" * 64, size_bytes=1,
        )
        tree = tmp_path / str(conversation.id)
        (tree / str(row.pk)).mkdir(parents=True)
        (tree / str(row.pk) / "a.txt").write_bytes(b"x")
        assert delete_conversation(Principal(kind="user", key="u1"), conversation)
        assert not tree.exists()
```

(Adjust the `Principal` construction to match `identity/contracts/principals.py`'s actual signature — read that file; `user_principal` from `identity.testing` is the ready-made helper if constructing one directly is awkward.)

- [ ] **Step 2: Run to verify failure** — `pytest agents/tests/test_models.py -k ConversationFile -v` → ImportError.

- [ ] **Step 3: Implement the model** (in `agents/models.py`, after `Conversation`)

```python
class ConversationFile(models.Model):
    """One attached file, owned by its conversation (chat-attachments
    spec section 1).

    Bytes live in the managed store
    (`agents/store.py::file_dir`) -- `path` records the stored
    copy, never the browser's transient upload. CASCADE with the
    conversation; the DISK tree goes in the same service call
    (`agents.visibility.delete_conversation`), never via a signal --
    this repository uses no Django signals anywhere (the Share-cleanup
    precedent in that same function).

    `file_hash` is the extraction sidecar's validation key
    (`source_sha256`, the `tools/rag/media.py::
    _load_finished_sidecar_if_matching` contract). Owner columns are
    IA-1's shape, stamped via `identity.access.owner_fields` at create.
    """

    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE,
                                     related_name="files")
    path = models.CharField(max_length=1024)
    original_name = models.CharField(max_length=512)
    media_type = models.CharField(max_length=100, blank=True, default="")
    file_hash = models.CharField(max_length=64, db_index=True)
    size_bytes = models.BigIntegerField(default=0)
    owner_kind = models.CharField(max_length=32, blank=True, default="")
    owner_key = models.CharField(max_length=200, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"{self.conversation_id}:{self.original_name}"
```

- [ ] **Step 4: Make the migration** — `python manage.py makemigrations agents` (venv python, worktree). Verify it lands as `0006_*`; if another 0006 exists on the branch base, STOP and renumber (the vision-0006 lesson).

- [ ] **Step 5: Register ownership** (in `agents/apps.py`, beside line 107)

```python
        register_owned_rows(
            OwnedRows("agents.conversationfile", "Attachments", "agents.ConversationFile"))
```

- [ ] **Step 6: Extend delete_conversation** (in `agents/visibility.py:278-287`) — inside the existing `transaction.atomic()` block, after `conversation.delete()` add nothing; instead capture the id first and remove the tree AFTER the transaction commits (a rolled-back delete must not have already destroyed bytes):

```python
    if not may_manage_conversation(principal, conversation):
        return False
    conversation_id = conversation.pk
    with transaction.atomic():
        Share.objects.filter(target_type=Share.Target.CONVERSATION,
                             target_key=str(conversation.pk)).delete()
        conversation.delete()
    # AFTER the commit, never inside it: a rollback must find the bytes
    # still there. Chat-attachments spec section 1; ignore-errors removal
    # (`agents.store.remove_conversation_files`) because a
    # half-removed tree on a full disk must not resurrect the 500 this
    # view exists to avoid.
    from agents.store import remove_conversation_files
    remove_conversation_files(conversation_id)
    return True
```

- [ ] **Step 7: Run migration + tests** — `python manage.py migrate agents` then `pytest agents/tests/test_models.py agents/tests/test_apps.py -v` → PASS. `identity/tests/test_owned_rows_registry.py:22` pins the registry as an EXACT set — add `"agents.conversationfile"` to `expected` and update its docstring's table count; also update `agents/apps.py:97`'s "The three owned tables in this column" comment to four.

- [ ] **Step 8: Commit** — `git commit -m "feat(agents): ConversationFile model, ownership registration, delete cleanup"` (add all touched files).

---

### Task 3: `file:` artifact kind + rendering split

**Files:**
- Modify: `agents/contracts/artifacts.py` (ARTIFACT_KINDS, _TITLED_KINDS, _URL_NAMES, _SHAPE)
- Modify: `agents/chat/rendering.py::artifact_links` (~line 347)
- Test: `agents/contracts/tests/test_artifacts.py` (append), `agents/chat/tests/test_rendering.py` (append; find the module by `grep -rn "artifact_links" agents/chat/tests/`)

**Interfaces:**
- Produces: `file:<pk>` and `file:<pk>:<quoted name>` parse/mint/title/url exactly like `document`; `artifact_url_name("file") == "chat-file"`.
- Produces: `artifact_links` routes a `file` entry into the IMAGES list when `mimetypes.guess_type(title)` says `image/*`, else into FILES.

- [ ] **Step 1: Failing tests** (append; mirror the existing test style in each file)

```python
# agents/contracts/tests/test_artifacts.py
def test_a_file_reference_parses_and_carries_a_title():
    from agents.contracts.artifacts import artifact_title, mint_artifact, parse_artifact
    ref = mint_artifact("file", 9, "holiday photo.png")
    assert parse_artifact(ref) == ("file", 9)
    assert artifact_title(ref) == "holiday photo.png"

def test_the_file_kind_maps_to_the_chat_file_url_name():
    from agents.contracts.artifacts import artifact_url_name
    assert artifact_url_name("file") == "chat-file"
```

```python
# agents/chat/tests/test_rendering.py (append)
def test_an_image_attachment_ref_lands_in_images_and_a_pdf_in_files():
    from agents.chat.rendering import artifact_links
    images, files = artifact_links(["file:1:photo.png", "file:2:paper.pdf"])
    assert [e["reference"] for e in images] == ["file:1:photo.png"]
    assert [e["reference"] for e in files] == ["file:2:paper.pdf"]
```

- [ ] **Step 2: Run to verify failure** → ValueError "not an artifact reference".

- [ ] **Step 3: Implement.** In `artifacts.py`: `ARTIFACT_KINDS = ("output", "input", "document", "file")`; `_TITLED_KINDS = ("document", "file")` (an attachment's own filename is exactly the title case R5 built this mechanism for); `_URL_NAMES["file"] = "chat-file"`; extend `_SHAPE`'s sentence with `file:<id>`. In `rendering.py::artifact_links`, where the entry is routed (read the images/files append logic in place — around lines 355-380): a `file` kind consults `mimetypes.guess_type(title or "")[0]` (stdlib — no `tools.*` import, which this module's docstring forbids) and joins `images` when it starts with `"image/"`, else `files`; title falls back to `f"File {pk}"` like documents fall back.

- [ ] **Step 4: Run** the two test files → PASS. Also `pytest agents/contracts agents/chat -q` — the existing `"output:12:extra"` refusal pin and every rendering test must still pass untouched.

- [ ] **Step 5: Commit** — `git commit -m "feat(agents): file:<id> artifact kind, rendered by media type"`.

---

### Task 4: Upload path — service + view + form

**Files:**
- Modify: `agents/chat/service.py::start_turn` (line 116; also `_BLANK` handling)
- Modify: `agents/chat/views/turns.py::turn_create` (line 47)
- Modify: `agents/chat/templates/chat/conversation.html` (`#turn-form`, ~line 250)
- Test: `agents/chat/tests/test_turn_create.py` (append), `agents/chat/tests/test_thread.py` (append the form-markup test and the full-render attachment-card pin below)
- NOTE: the card-render test needs Task 5's `chat-file` URL name; if Task 5 has not landed yet in your ordering, mark it `xfail(reason="chat-file lands in Task 5")` and un-xfail it there.

**Interfaces:**
- Consumes: Task 1 store + settings; Task 2 model; Task 3 `mint_artifact`.
- Produces: `start_turn(conversation, text, *, connection="", actor, files=())` — `files` is a sequence of Django `UploadedFile`s; on success the USER turn's `artifacts` holds one `file:<pk>:<quoted name>` ref per stored file, and `ConversationFile` rows exist with owner columns stamped from `actor`.

- [ ] **Step 1: Failing tests** (append to `test_turn_create.py`; use its existing fixtures — it already imports `fake_turn_queue` and friends from `_helpers`; mirror the surrounding tests' construction style exactly)

```python
class TestAttachments:
    def _upload(self, name="notes.txt", data=b"hello", content_type="text/plain"):
        from django.core.files.uploadedfile import SimpleUploadedFile
        return SimpleUploadedFile(name, data, content_type=content_type)

    def test_an_attachment_becomes_a_row_and_a_ref_on_the_user_turn(
        self, client, fake_turn_queue, tmp_path, settings
    ):
        settings.CONVERSATION_FILES_DIR = tmp_path
        conversation = make_conversation()
        response = client.post(
            reverse("chat-turn", args=[conversation.id]),
            {"text": "what is this?", "attachments": self._upload()},
        )
        assert response.status_code == 302
        from agents.models import ConversationFile, Turn
        row = ConversationFile.objects.get(conversation=conversation)
        assert row.original_name == "notes.txt"
        assert row.size_bytes == 5
        assert Path(row.path).read_bytes() == b"hello"
        user_turn = conversation.turns.get(role=Turn.Role.USER)
        assert user_turn.artifacts == [f"file:{row.pk}:notes.txt"]

    def test_a_file_with_no_text_is_a_valid_turn(self, client, fake_turn_queue, tmp_path, settings):
        settings.CONVERSATION_FILES_DIR = tmp_path
        conversation = make_conversation()
        response = client.post(
            reverse("chat-turn", args=[conversation.id]),
            {"text": "", "attachments": self._upload()},
        )
        assert response.status_code == 302

    def test_too_many_attachments_is_an_honest_400(self, client, tmp_path, settings):
        settings.CONVERSATION_FILES_DIR = tmp_path
        settings.CHAT_ATTACHMENTS_PER_TURN = 1
        conversation = make_conversation()
        response = client.post(
            reverse("chat-turn", args=[conversation.id]),
            {"text": "hi", "attachments": [self._upload("a.txt"), self._upload("b.txt")]},
        )
        assert response.status_code == 400
        from agents.models import ConversationFile
        assert not ConversationFile.objects.exists()  # rejected = no rows, no files

    def test_an_oversize_attachment_is_an_honest_400(self, client, tmp_path, settings):
        settings.CONVERSATION_FILES_DIR = tmp_path
        settings.CHAT_ATTACHMENT_MAX_BYTES = 3
        conversation = make_conversation()
        response = client.post(
            reverse("chat-turn", args=[conversation.id]),
            {"text": "hi", "attachments": self._upload(data=b"toolong")},
        )
        assert response.status_code == 400

    def test_an_unrecognized_extension_is_an_honest_400(self, client, tmp_path, settings):
        settings.CONVERSATION_FILES_DIR = tmp_path
        conversation = make_conversation()
        response = client.post(
            reverse("chat-turn", args=[conversation.id]),
            {"text": "hi", "attachments": self._upload(name="prog.exe")},
        )
        assert response.status_code == 400

    def test_a_failed_enqueue_leaves_no_rows_and_no_disk_files(
        self, client, fake_queue_down, tmp_path, settings
    ):
        settings.CONVERSATION_FILES_DIR = tmp_path
        conversation = make_conversation()
        client.post(
            reverse("chat-turn", args=[conversation.id]),
            {"text": "hi", "attachments": self._upload()},
        )
        from agents.models import ConversationFile
        assert not ConversationFile.objects.exists()
        assert list((tmp_path / str(conversation.id)).rglob("*.txt")) == []
```

```python
# test_thread.py (append to TestTheThread)
    def test_a_user_turns_attachment_renders_as_a_link_on_its_card(
        self, client, tmp_path, settings
    ):
        """The full-render pin the spec's section 5 asks for: a user
        card shows its attachments through the existing artifact
        includes -- `_turn_card.html` already includes
        `_artifact_files.html` for every non-tool card."""
        settings.CONVERSATION_FILES_DIR = tmp_path
        conversation = make_conversation()
        from agents.models import ConversationFile, Turn
        row = ConversationFile.objects.create(
            conversation=conversation, path="", original_name="paper.pdf",
            media_type="application/pdf", file_hash="0" * 64, size_bytes=1,
        )
        make_turn(conversation=conversation, role=Turn.Role.USER, text="read this",
                  state=Turn.State.DONE, artifacts=[f"file:{row.pk}:paper.pdf"])
        body = client.get(
            reverse("chat-conversation", args=[conversation.id])
        ).content.decode()
        assert f'href="{reverse("chat-file", args=[row.pk])}"' in body
        assert "paper.pdf" in body

    def test_the_form_is_multipart_and_offers_an_attachment_input(self, client):
        conversation = make_conversation()
        body = client.get(
            reverse("chat-conversation", args=[conversation.id])
        ).content.decode()
        assert 'enctype="multipart/form-data"' in body
        assert 'name="attachments"' in body
        assert "multiple" in body
```

- [ ] **Step 2: Run to verify failure.**

- [ ] **Step 3: Implement `start_turn`.** Changes, in order:

1. Signature: `def start_turn(conversation, text: str, *, connection: str = "", actor, files=()) -> TurnStart:`
2. Blank check becomes: `if not message and not files: return TurnStart(False, 400, _BLANK)` — and reword `_BLANK` to mention "a message or an attachment".
3. Add a validation pass BEFORE any row is written (module-level constants beside `_BLANK`; every sentence a user sees, declared once):

```python
def _refuse_attachments(files) -> str:
    """The first thing wrong with this batch of uploads, or ''.

    Extension recognition is the RAG readers' single-sourced answer,
    reached by dotted path because `agents/*` may not import `tools.*`
    (import-law rule 3; the `agents/runtime/loop.py:456-540` pattern) [range updated post-review: 576-605].
    FAIL CLOSED: if the seam will not resolve, uploads are refused with
    an honest sentence -- accepting a file nothing can ever extract
    would be the quiet version of the same failure.
    """
    from django.conf import settings
    from models.contracts.jobkinds import resolve_dotted_path
    if len(files) > settings.CHAT_ATTACHMENTS_PER_TURN:
        return (f"At most {settings.CHAT_ATTACHMENTS_PER_TURN} files can be "
                f"attached to one message.")
    try:
        medium_for = resolve_dotted_path("tools.rag.readers.medium_for")
    except Exception:  # noqa: BLE001 -- fail closed, honestly
        logger.exception("chat: attachment medium seam failed to resolve")
        return "Attachments are not available on this install."
    for uploaded in files:
        if uploaded.size > settings.CHAT_ATTACHMENT_MAX_BYTES:
            cap_mb = settings.CHAT_ATTACHMENT_MAX_BYTES // (1024 * 1024)
            return f"{uploaded.name!r} is larger than the {cap_mb} MB attachment cap."
        ext = Path(uploaded.name.replace("\\", "/")).suffix.lower()
        try:
            medium_for(ext)
        except ValueError:
            return (f"{uploaded.name!r} is not a supported attachment type; "
                    f"supported types are the ones the library can ingest.")
    return ""
```

Call it right after the blank check: `refusal = _refuse_attachments(files)` → `return TurnStart(False, 400, refusal)` when non-empty.

4. Inside the EXISTING `transaction.atomic()` block (service.py:171-198), BEFORE the USER turn create, store the files and build `refs`; the USER turn create then passes `artifacts=refs`. `ConversationFile.conversation` is the conversation (not the turn), so the rows do not depend on the turn existing first. Track written dirs for cleanup:

```python
            stored_dirs: list = []
            refs: list[str] = []
            for uploaded in files:
                row = ConversationFile.objects.create(
                    conversation=conversation,
                    path="", original_name=chat_store.basename(uploaded.name),
                    media_type=getattr(uploaded, "content_type", "") or "",
                    file_hash="", size_bytes=0, **owner_fields(actor),
                )
                path, digest, size = chat_store.store_upload(
                    conversation.id, row.pk, uploaded)
                stored_dirs.append(chat_store.file_dir(conversation.id, row.pk))
                ConversationFile.objects.filter(pk=row.pk).update(
                    path=path, file_hash=digest, size_bytes=size)
                refs.append(mint_artifact("file", row.pk, row.original_name))
```

and the user-turn create gains `artifacts=refs`. Imports at top of service.py: `from agents import store as chat_store`, `from agents.contracts.artifacts import mint_artifact`, `from identity.access import owner_fields`, `from pathlib import Path`.

5. Every existing `except` branch after the atomic block already returns a refusal; each must now also remove `stored_dirs` from disk (the DB rows roll back; the bytes do not). Hoist `stored_dirs: list = []` ABOVE the `try:` and add a small helper called from each failure branch:

```python
def _discard_stored(dirs) -> None:
    """A rolled-back turn's bytes go with it -- rows vanished in the
    rollback, and orphan directories nothing references would defeat
    the conversation-scoped lifecycle (spec section 1)."""
    import shutil
    for d in dirs:
        shutil.rmtree(d, ignore_errors=True)
```

6. `_title_if_unset(conversation, message)` — when `message` is blank and files exist, title from the first file's name instead: change the call to `_title_if_unset(conversation, message or files[0].name)`.

- [ ] **Step 4: Implement `turn_create`.** One line change plus one:

```python
    files = request.FILES.getlist("attachments")
    start = start_turn(conversation, text, connection=connection, actor=principal,
                       files=files)
```

- [ ] **Step 5: Implement the form.** In `conversation.html`'s `#turn-form`: add `enctype="multipart/form-data"` to the `<form>` tag, and inside `.turn-controls` before the Send button:

```html
    <label class="attach-label">Attach
      <input type="file" name="attachments" multiple>
    </label>
```

No JS changes: the submit handler already posts `new FormData(form)`, which carries file inputs natively, and `form.reset()` clears them.

Also REMOVE `required` from the `<textarea name="text">` (conversation.html line ~191): the server-side rule is now "a message OR an attachment" (`start_turn`'s `if not message and not files`), and leaving `required` would make a real browser refuse the file-only submission the server accepts (the test client bypasses HTML5 validation, so only a markup test catches this). Add to the form-markup test: assert the textarea tag no longer carries `required`, so the blank-and-no-file 400 stays the single source of that rule.

- [ ] **Step 6: Run** `pytest agents/chat/tests/test_turn_create.py agents/chat/tests/test_thread.py -v` → PASS; then `pytest agents/chat -q` for no regressions.

- [ ] **Step 7: Commit** — `git commit -m "feat(chat): attachments ride the turn form into conversation-scoped storage"`.

---

### Task 5: `chat-file` serving endpoint

**Files:**
- Create: `agents/chat/views/files.py`
- Modify: `agents/chat/urls.py` (one `path()` line), `agents/chat/views/__init__.py` if it re-exports views (check)
- Modify: `identity/routes.py` (one `"chat-file": "O"` row beside line 61)
- Modify: `identity/tests/test_route_matrix.py` (World seeding + one driver — see Step 4)
- Test: `agents/chat/tests/test_files_view.py`, plus drivers in `agents/chat/tests/test_never_500.py` (registration dict at ~line 567)

**Interfaces:**
- Consumes: Task 2 model.
- Produces: URL name `chat-file`, path `files/<int:file_id>/`, serving `ConversationFile` bytes gated by `visible_conversations(principal)`.

- [ ] **Step 1: Failing tests**

```python
# agents/chat/tests/test_files_view.py
"""`chat-file` serves a conversation's own bytes, by its visibility."""
from __future__ import annotations

from pathlib import Path

import pytest
from django.urls import reverse

from agents.chat.tests._helpers import make_conversation, make_user, posture, sign_in
from identity.contracts.postures import POSTURE_ENTERPRISE

pytestmark = pytest.mark.django_db


def _file_row(conversation, tmp_path, name="pic.png", data=b"\x89PNG"):
    from agents.models import ConversationFile
    row = ConversationFile.objects.create(
        conversation=conversation, path="", original_name=name,
        media_type="image/png", file_hash="0" * 64, size_bytes=len(data),
    )
    dest = tmp_path / str(conversation.id) / str(row.pk)
    dest.mkdir(parents=True)
    (dest / name).write_bytes(data)
    ConversationFile.objects.filter(pk=row.pk).update(path=str(dest / name))
    row.refresh_from_db()
    return row


class TestChatFile:
    def test_it_serves_the_bytes_with_the_stored_media_type(self, client, tmp_path, settings):
        settings.CONVERSATION_FILES_DIR = tmp_path
        row = _file_row(make_conversation(), tmp_path)
        response = client.get(reverse("chat-file", args=[row.pk]))
        assert response.status_code == 200
        assert response["Content-Type"] == "image/png"

    def test_an_unknown_file_is_a_404(self, client):
        assert client.get(reverse("chat-file", args=[999999])).status_code == 404

    def test_a_row_whose_bytes_are_gone_is_a_404_not_a_500(self, client, tmp_path, settings):
        settings.CONVERSATION_FILES_DIR = tmp_path
        row = _file_row(make_conversation(), tmp_path)
        Path(row.path).unlink()
        assert client.get(reverse("chat-file", args=[row.pk])).status_code == 404

    def test_anothers_conversation_file_is_a_404_under_accounts(self, client, tmp_path, settings):
        settings.CONVERSATION_FILES_DIR = tmp_path
        posture(POSTURE_ENTERPRISE)
        owner, stranger = make_user("owner"), make_user("stranger")
        conversation = make_conversation(owner_kind="user", owner_key=owner.username)
        row = _file_row(conversation, tmp_path)
        sign_in(client, stranger)
        assert client.get(reverse("chat-file", args=[row.pk])).status_code == 404
```

(`posture`/`make_user`/`sign_in` usage: copy the exact incantation from `agents/chat/tests/test_visibility.py` — do not invent argument shapes.)

- [ ] **Step 2: Run to verify failure** → NoReverseMatch.

- [ ] **Step 3: Implement the view**

```python
# agents/chat/views/files.py
"""GET /chat/files/<id>/ -- one attachment's bytes.

Access is the CONVERSATION'S answer: the row is fetched through
`visible_conversations(principal)`, so whoever may read the thread may
read its files, and nobody else learns the row exists (404, the same
secrecy `chat-conversation` itself keeps). Class "O" in
`identity/routes.py`, like every other conversation-addressed route.

`FileResponse` streams from the managed-store path; a row whose bytes
are missing on disk is a 404 with a log line, never a 500 -- the
`test_never_500` charter every chat route answers to.
"""
from __future__ import annotations

import logging

from django.http import FileResponse, Http404

from agents.models import ConversationFile
from agents.visibility import visible_conversations
from identity.request import principal_for_request

logger = logging.getLogger(__name__)


def conversation_file(request, file_id: int):
    principal = principal_for_request(request)
    row = ConversationFile.objects.filter(
        pk=file_id, conversation__in=visible_conversations(principal),
    ).first()
    if row is None:
        raise Http404(f"Attachment {file_id} does not exist.")
    try:
        handle = open(row.path, "rb")
    except OSError:
        logger.warning("chat: attachment %s row exists but bytes are missing at %r",
                       file_id, row.path)
        raise Http404(f"Attachment {file_id} does not exist.")
    return FileResponse(handle, content_type=row.media_type or "application/octet-stream",
                        filename=row.original_name)
```

- [ ] **Step 4: Wire url + route class.** `agents/chat/urls.py`: `path("files/<int:file_id>/", conversation_file, name="chat-file"),` with the import beside the others. `identity/routes.py`: add `"chat-file": "O",` beside the other `chat-` rows (line ~61). Also add the route-matrix half, or `identity/tests/test_route_matrix.py::test_every_route_has_a_driver` fails (line ~517 pins `ROUTE_RULES` and `_DRIVERS` as the same set): (a) add a `conversation_file` field to `World` and seed it in `_build_world()` beside `turn = make_turn(conversation=conversation)` (line ~428) — a `ConversationFile` on `world.conversation` with bytes written under the conversation-files dir, following `_seed_document_files`'s own write-real-bytes-and-stamp-the-path shape (line ~405); (b) add `"chat-file": lambda w: ("get", reverse("chat-file", args=[w.conversation_file.pk]), {})` to `_DRIVERS` beside `"chat-turn-status"` (line ~144). `_assert_never_500` already skips the body check for a `FileResponse` (line ~505), so no other change is needed.

- [ ] **Step 5: never-500 drivers.** In `test_never_500.py`, read the four-driver pattern at lines 373-404 (`chat-turn-status`'s own). Drivers take `(client, monkeypatch)` ONLY — no `tmp_path`/`settings` fixtures reach them. Build the row's bytes under a `tempfile.mkdtemp()` directory and point the setting at it with `monkeypatch.setattr("django.conf.settings.CONVERSATION_FILES_DIR", Path(tmpdir))` inside the driver. The four: normal (a real row with bytes → 200), missing id (999999999 → 404), queue_down (identical to normal — this view never touches the queue), malformed (`client.get("/chat/files/not-an-int/")` → 404, the route itself does not match). Then register `"chat-file": (...)` in the dict at line ~567.

- [ ] **Step 6: Run** `pytest agents/chat/tests/test_files_view.py agents/chat/tests/test_never_500.py identity -q` → PASS (identity's route matrix included).

- [ ] **Step 7: Commit** — `git commit -m "feat(chat): chat-file endpoint serves attachments by conversation visibility"`.

---

### Task 6: Extraction + capability-gated prompt injection

**Files:**
- Create: `agents/runtime/attachments.py`
- Modify: `agents/runtime/prompt.py` (`history_messages`/`build_messages`), `agents/runtime/loop.py::_run_turn` (thread `resolved` into `build_messages`), `agents/runtime/jobs.py::plan_turn` (extraction roles)
- Modify: `models/contracts/catalog.py` (`CatalogEntry.accepts` field — ADDITIVE, peer-announced; see Global Constraints)
- Test: `agents/runtime/tests/test_attachments.py`, `agents/runtime/tests/test_prompt.py` (append), `models/contracts/tests/test_catalog.py` (append one field test; find the real test path with `ls models/contracts/tests/` first)

**Interfaces:**
- Consumes: Task 2 model, Task 3 grammar.
- Produces: `attachments.file_rows_for(turn) -> list[ConversationFile]` (parses `file:` refs off `turn.artifacts`, same-conversation only); `attachments.extracted_text(row, job_ctx=None) -> str` (sidecar-cached, capped by `CHAT_ATTACHMENT_EXCERPT_CHARS`, honest failure sentences); `attachments.native_media_types(resolved) -> frozenset[str]` (the capability gate); `CatalogEntry.accepts: tuple[str, ...] = ()`.

- [ ] **Step 1: Failing tests**

```python
# agents/runtime/tests/test_attachments.py
"""Attachment extraction: dispatch, sidecar cache, honest failures.

Every tools.* seam is reached by dotted path at runtime, so tests patch
`models.contracts.jobkinds.resolve_dotted_path` targets by DOTTED NAME
via monkeypatch on the resolved callables' own modules -- never by
importing tools.* here (this test file lives under agents/)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agents.runtime import attachments
from agents.tests._helpers import make_conversation

pytestmark = pytest.mark.django_db


def _row(conversation, tmp_path, name, data=b"x", media_type=""):
    from agents import store
    from agents.models import ConversationFile
    import hashlib
    row = ConversationFile.objects.create(
        conversation=conversation, path="", original_name=name,
        media_type=media_type, file_hash=hashlib.sha256(data).hexdigest(),
        size_bytes=len(data),
    )
    dest = tmp_path / str(conversation.id) / str(row.pk)
    dest.mkdir(parents=True)
    (dest / name).write_bytes(data)
    ConversationFile.objects.filter(pk=row.pk).update(path=str(dest / name))
    row.refresh_from_db()
    return row


class TestFileRowsFor:
    def test_it_returns_this_conversations_rows_in_ref_order(self, tmp_path, settings):
        settings.CONVERSATION_FILES_DIR = tmp_path
        conversation = make_conversation()
        a = _row(conversation, tmp_path, "a.txt")
        b = _row(conversation, tmp_path, "b.txt")
        from agents.tests._helpers import make_turn
        turn = make_turn(conversation=conversation,
                         artifacts=[f"file:{b.pk}:b.txt", f"file:{a.pk}:a.txt"])
        assert [r.pk for r in attachments.file_rows_for(turn)] == [b.pk, a.pk]

    def test_a_ref_from_another_conversation_is_dropped(self, tmp_path, settings):
        settings.CONVERSATION_FILES_DIR = tmp_path
        theirs = _row(make_conversation(), tmp_path, "x.txt")
        from agents.tests._helpers import make_agent, make_turn
        other = make_conversation(agent=make_agent(slug="second"))
        turn = make_turn(conversation=other, artifacts=[f"file:{theirs.pk}:x.txt"])
        assert attachments.file_rows_for(turn) == []


class TestExtractedText:
    def test_a_txt_file_reads_via_the_prose_seam_and_writes_a_sidecar(
        self, tmp_path, settings, monkeypatch
    ):
        settings.CONVERSATION_FILES_DIR = tmp_path
        row = _row(make_conversation(), tmp_path, "notes.txt", b"the content")
        calls = []
        class Doc:  # the one attribute joined
            text = "the content"
        def fake_resolver(dotted):
            calls.append(dotted)
            if dotted.endswith("medium_for"):
                return lambda ext: "prose"
            return lambda path: [Doc()]
        monkeypatch.setattr(attachments, "resolve_dotted_path", fake_resolver)
        assert attachments.extracted_text(row) == "the content"
        assert calls == ["tools.rag.readers.medium_for",
                         "tools.rag.readers.read_prose_documents"]
        sidecar = json.loads((Path(row.path).parent / "extract.json").read_text())
        assert sidecar["source_sha256"] == row.file_hash
        # second call: cache hit, no seam resolution
        calls.clear()
        assert attachments.extracted_text(row) == "the content"
        assert calls == []

    def test_the_excerpt_cap_truncates_with_a_notice(self, tmp_path, settings, monkeypatch):
        settings.CONVERSATION_FILES_DIR = tmp_path
        settings.CHAT_ATTACHMENT_EXCERPT_CHARS = 10
        row = _row(make_conversation(), tmp_path, "big.txt", b"0123456789ABCDEF")
        class Doc:
            text = "0123456789ABCDEF"
        def fake_resolver(dotted):
            if dotted.endswith("medium_for"):
                return lambda ext: "prose"
            return lambda path: [Doc()]
        monkeypatch.setattr(attachments, "resolve_dotted_path", fake_resolver)
        text = attachments.extracted_text(row)
        assert text.startswith("0123456789")
        assert "truncated" in text

    def test_a_failed_extraction_is_an_honest_sentence_not_an_exception(
        self, tmp_path, settings, monkeypatch
    ):
        settings.CONVERSATION_FILES_DIR = tmp_path
        row = _row(make_conversation(), tmp_path, "bad.pdf", b"broken")
        def boom(dotted):
            raise RuntimeError("no seam")
        monkeypatch.setattr(attachments, "resolve_dotted_path", boom)
        text = attachments.extracted_text(row)
        assert "could not be read" in text
        # and the failure is NOT cached as a sidecar
        assert not (Path(row.path).parent / "extract.json").exists()


class TestNativeMediaTypes:
    def test_a_catalog_entry_that_accepts_images_gates_open(self):
        from models.contracts.bindings import ResolvedModel
        from models.contracts import catalog
        entry = catalog.CatalogEntry(
            name="T", engine="ollama", model_id="multimodal:1b",
            capability="chat", accepts=("image",))
        try:
            catalog.CATALOG.append(entry)
            resolved = ResolvedModel(engine="ollama", model_id="multimodal:1b",
                                     endpoint="http://x")
            assert attachments.native_media_types(resolved) == frozenset({"image"})
        finally:
            catalog.CATALOG.remove(entry)

    def test_an_unknown_model_gates_closed(self):
        from models.contracts.bindings import ResolvedModel
        resolved = ResolvedModel(engine="ollama", model_id="nope:1b", endpoint="http://x")
        assert attachments.native_media_types(resolved) == frozenset()
```

- [ ] **Step 2: Run to verify failure.**

- [ ] **Step 3: Implement `models/contracts/catalog.py` first** (smallest piece): add to `CatalogEntry`:

```python
    # Media types this model accepts NATIVELY in a chat message, e.g.
    # ("image",) -- the chat-attachments capability gate
    # (agents/runtime/attachments.py::native_media_types) reads it; an
    # empty tuple means text-only, which is every entry that predates
    # the field. ADDITIVE ONLY: nothing else in this column consumes it.
    accepts: tuple[str, ...] = ()
```

and stamp `accepts=("image",)` on the catalog's two vision entries. One appended test in the catalog's own test module pins the default `()`.

- [ ] **Step 4: Implement `agents/runtime/attachments.py`**

```python
# agents/runtime/attachments.py
"""Attachment understanding for the turn runtime (chat-attachments spec
section 3).

Import law: NOTHING from `tools.*` is imported here -- every extraction
seam is reached through `resolve_dotted_path`, the exact mechanism
`agents/runtime/loop.py:456-540` uses for vision narrowing [range updated post-review: 576-605], and for the
same reason. `resolve_dotted_path` is bound at MODULE level so tests
monkeypatch `agents.runtime.attachments.resolve_dotted_path` and every
call site sees it.

The sidecar is `extract.json` BESIDE the stored file, validated by
`source_sha256` against the row's `file_hash` -- deliberately the same
contract `tools/rag/media.py::_load_finished_sidecar_if_matching`
enforces, so one mental model covers both stores. Extraction runs once
per file per conversation; every later turn re-reads the sidecar.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from django.conf import settings

from agents.contracts.artifacts import parse_artifact
from models.contracts.jobkinds import resolve_dotted_path

logger = logging.getLogger(__name__)

_UNREADABLE = "[Attachment {name!r} could not be read: {why}]"
_TRUNCATED = "\n[truncated at {cap} characters — the full file is attached to the conversation]"




def file_rows_for(turn):
    """This turn's `ConversationFile` rows, in artifact order.

    SAME-CONVERSATION ONLY: a ref naming another conversation's row is
    dropped with a log line -- refs arrive from a database row a tool
    or an operator may have edited, and silence here would be a
    cross-thread read."""
    from agents.models import ConversationFile
    pks = []
    for ref in turn.artifacts or []:
        try:
            kind, pk = parse_artifact(ref)
        except ValueError:
            continue
        if kind == "file":
            pks.append(pk)
    if not pks:
        return []
    rows = {r.pk: r for r in ConversationFile.objects.filter(
        pk__in=pks, conversation_id=turn.conversation_id)}
    dropped = [pk for pk in pks if pk not in rows]
    if dropped:
        logger.warning("attachments: refs %s not in conversation %s; dropped",
                       dropped, turn.conversation_id)
    return [rows[pk] for pk in pks if pk in rows]


def _sidecar_path(row) -> Path:
    return Path(row.path).parent / "extract.json"


def _read_cached(row) -> str | None:
    """The finished sidecar's text, or None -- total over whatever bytes
    are on disk (the tools/rag/sidecar.py rule)."""
    try:
        data = json.loads(_sidecar_path(row).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("source_sha256") != row.file_hash:
        return None
    text = data.get("text")
    return text if isinstance(text, str) else None


def _write_sidecar(row, text: str) -> None:
    payload = json.dumps({"source_sha256": row.file_hash, "text": text})
    tmp = _sidecar_path(row).with_suffix(".json.tmp")
    tmp.write_text(payload, encoding="utf-8")
    tmp.replace(_sidecar_path(row))


def _extract(row, job_ctx) -> str:
    """Dispatch one extraction. Raises; `extracted_text` wraps.

    The routing answer comes from `tools.rag.readers.medium_for` (by
    dotted path -- the same seam Task 4's upload gate resolves), so the
    supported-type taxonomy lives in exactly one place
    (tools/rag/readers.py:22-36) and this dispatch can never disagree
    with the gate that admitted the file."""
    path = Path(row.path)
    medium = resolve_dotted_path("tools.rag.readers.medium_for")(path.suffix.lower())
    if medium == "prose":
        read = resolve_dotted_path("tools.rag.readers.read_prose_documents")
        return "\n\n".join(d.text for d in read(path) if getattr(d, "text", ""))
    if medium == "tabular":
        read = resolve_dotted_path("tools.rag.readers.read_tabular_dataframe")
        frame = read(path)
        return f"First rows of {row.original_name}:\n{frame.head(30).to_string()}"
    if medium == "image":
        extract = resolve_dotted_path("tools.rag.extract.extract_image_text")
        return extract(path)
    if medium in ("audio", "video"):
        return _transcribe(row, path, job_ctx)
    raise ValueError(f"no extractor for medium {medium!r}")


def _transcribe(row, path: Path, job_ctx) -> str:
    """extract_audio -> slice -> transcribe each slice -> joined text.

    A deliberately MINIMAL sibling of tools/rag/media.py::
    transcribe_to_sidecar -- no checkpoint/resume (the sidecar one level
    up is the cache; a failed turn simply re-runs), progress reported
    per slice when a job context is present. The work directory lives
    beside the file and is removed on the way out either way."""
    import shutil
    from models.contracts.bindings import resolve
    from models.contracts.gateway import get_transcriber_for
    from models.contracts.roles import RAG_TRANSCRIBE_ROLE

    extract_audio = resolve_dotted_path("tools.rag.transcode.extract_audio")
    slice_audio = resolve_dotted_path("tools.rag.transcode.slice_audio")
    transcriber = get_transcriber_for(resolve(RAG_TRANSCRIBE_ROLE))
    work = path.parent / "work"
    work.mkdir(exist_ok=True)
    try:
        wav = extract_audio(path, work / "audio.wav")
        slices = slice_audio(wav, work)
        pieces = []
        for index, part in enumerate(slices):
            if job_ctx is not None:
                job_ctx.report_progress(index + 1, len(slices), unit="items",
                                        label=f"reading {row.original_name}")
            result = transcriber.transcribe(part)
            pieces.append(getattr(result, "text", None) or str(result))
        return " ".join(piece.strip() for piece in pieces if piece)
    finally:
        shutil.rmtree(work, ignore_errors=True)
```

**NOTE for the implementer on `_transcribe`:** before writing it, read `tools/rag/media.py:700-760` for the transcriber call's REAL return shape and adjust the `pieces.append` line to match exactly what `transcribe()` returns there (segments vs text) — copy the reading, cite the line. Also verify `RAG_TRANSCRIBE_ROLE`'s real name in `models/contracts/roles.py` (`grep TRANSCRIBE models/contracts/roles.py`).

```python
def extracted_text(row, job_ctx=None) -> str:
    """The capped excerpt for one attachment -- cached, else extracted,
    else an honest sentence. NEVER raises: a broken attachment must
    degrade the prompt, not fail the turn (spec section 3)."""
    cached = _read_cached(row)
    if cached is None:
        try:
            cached = _extract(row, job_ctx)
        except Exception as exc:  # noqa: BLE001 -- honest sentence, never a failed turn
            logger.warning("attachments: extraction failed for %s: %s", row.pk, exc)
            return _UNREADABLE.format(name=row.original_name, why=exc)
        _write_sidecar(row, cached)
    cap = settings.CHAT_ATTACHMENT_EXCERPT_CHARS
    if len(cached) > cap:
        return cached[:cap] + _TRUNCATED.format(cap=cap)
    return cached


def native_media_types(resolved) -> frozenset[str]:
    """What the bound chat model accepts natively -- the END-STATE gate
    (spec section 3): today `("image",)` on two catalog entries; an
    audio-native model later is a catalog stamp, not a design change.
    Unknown model -> empty set -> extraction fallback."""
    if resolved is None:
        return frozenset()
    from models.contracts import catalog
    entry = catalog.find(resolved.model_id)
    return frozenset(getattr(entry, "accepts", ()) or ())
```

- [ ] **Step 5: Wire the prompt.** In `agents/runtime/prompt.py::history_messages`, the user-role branch becomes attachment-aware FOR THE ANSWERED TURN ONLY (spec section 3: "the user turn being answered" — older turns replay as plain text so images and excerpts are not re-sent on every subsequent turn). `history_messages` gains keywords `resolved=None, attachments_for_index=None`; `build_messages` gains `resolved=None` and passes `attachments_for_index=(before_index - 1) if before_index is not None else None` (the answered USER turn sits at exactly `placeholder.index - 1`: `agents/chat/service.py:173-180` allocates them back-to-back via `Turn.next_index`). `agents/runtime/loop.py::_run_turn` passes `resolved=resolved` at its `build_messages` call (line ~264):

```python
        else:
            content = turn.text
            blocks = None
            if turn.role == "user" and turn.index == attachments_for_index and turn.artifacts:
                from agents.runtime.attachments import (
                    extracted_text, file_rows_for, native_media_types)
                rows = file_rows_for(turn)
                if rows:
                    native = native_media_types(resolved)
                    image_paths, texts = [], []
                    for row in rows:
                        is_image = (row.media_type or "").startswith("image/")
                        if is_image and "image" in native:
                            image_paths.append(row.path)
                        else:
                            texts.append(
                                f"[Attached file {row.original_name!r}]\n"
                                f"{extracted_text(row)}")
                    if texts:
                        content = "\n\n".join([turn.text, *texts]) if turn.text else "\n\n".join(texts)
                    if image_paths:
                        from llama_index.core.llms import ImageBlock, TextBlock
                        blocks = [TextBlock(text=content)] + [
                            ImageBlock(path=p) for p in image_paths]
            if blocks is not None:
                messages.append(ChatMessage(role=_ROLE_TO_MESSAGE_ROLE[turn.role],
                                            blocks=blocks))
            else:
                messages.append(ChatMessage(
                    role=_ROLE_TO_MESSAGE_ROLE[turn.role], content=content,
                ))
```

Add a module-docstring paragraph explaining the seam (native blocks when the catalog gates open; extraction text otherwise — spec section 3), and append prompt tests: a user turn with a `file:` ref renders its extracted text into the message content (monkeypatch `agents.runtime.attachments.extracted_text`); with a native-gating resolved model and an image row, the message carries an `ImageBlock` (assert via `message.blocks`); and an OLDER user turn's `file:` ref (index != attachments_for_index) contributes no ImageBlock and no extracted text — the answered-turn gate.

- [ ] **Step 6: plan_turn roles.** In `agents/runtime/jobs.py::plan_turn`, after the tool-roles walk: look up the turn's PRECEDING user turn's attachments and append extraction roles with the SAME tolerant-drop shape (footprint honesty — spec open question 2's answer):

```python
    # Attachment extraction models (chat-attachments spec section 3):
    # declared at enqueue so the queue's admission snapshot is honest
    # about what this turn may load -- image extraction resolves
    # RAG_EXTRACT_ROLE, audio/video RAG_TRANSCRIBE_ROLE. Tolerant drops,
    # the exact shape the tool-role walk above uses: an unresolvable
    # role means that extraction will fail with an honest sentence at
    # run time (attachments.extracted_text), never a queued job the
    # scheduler under-budgeted.
    user_turn = turn.conversation.turns.filter(
        index__lt=turn.index, role="user").order_by("-index").first()
    for role in _attachment_roles(user_turn):
        if role in seen_roles:
            continue
        try:
            refs.append(_ref(role, resolve(role)))
            seen_roles.add(role)
        except Exception:  # noqa: BLE001 -- tolerant drop, see docstring
            logger.info("agent.turn: attachment role %r does not resolve", role)
```

with `_attachment_roles(user_turn)` a small helper in `jobs.py` that walks `attachments.file_rows_for(user_turn)` and asks the SAME seam `attachments._extract` does — `resolve_dotted_path("tools.rag.readers.medium_for")(Path(row.path).suffix.lower())` — mapping `"image"` → `RAG_EXTRACT_ROLE` and `"audio"`/`"video"` → `RAG_TRANSCRIBE_ROLE`, and returning `set()` for a `user_turn` of `None` or a `medium_for` that raises (an unrecognized extension cannot have got past Task 4's upload gate, so a raise here means data drift, not a plan failure — drop it tolerantly, matching the surrounding walk). NO extension literals in `jobs.py`: the taxonomy lives in `tools/rag/readers.py:22-36` only, the same rule `_extract`'s own docstring states. Plus tests in `agents/runtime/tests/` pinning that an A/V attachment adds the transcribe role to the plan, a plain-text one adds nothing, and that the helper resolves `medium_for` rather than matching suffixes itself (monkeypatch the resolver and assert the dotted name it asked for).

- [ ] **Step 7: Run** `pytest agents/runtime models/contracts -q` → PASS.

- [ ] **Step 8: Commit** — `git commit -m "feat(agents): attachment extraction + capability-gated multimodal prompt"`.

---

### Task 7: `file:` → `input:` rewrite at the vision boundary

**Files:**
- Modify: `agents/runtime/loop.py` (before the `invoke_tool` call, line ~391)
- Test: `agents/runtime/tests/test_loop.py` (append; find the real module name with `ls agents/runtime/tests/`)

**Interfaces:**
- Consumes: Task 2 model; vision's `stage_upload(param_key, uploaded) -> "input:<id>"` (tools/vision/services.py:714).
- Produces: any string arg of a `vision.generate` call that parses as `file:<pk>` belonging to THIS conversation is replaced by a staged `input:<id>` before the tool sees it; vision is untouched.

- [ ] **Step 1: Failing test** (in the loop test module, following its existing fake-LLM machinery — read the module first and reuse its helpers)

```python
def test_a_vision_call_gets_file_refs_rewritten_to_staged_inputs(
    self, tmp_path, settings, monkeypatch
):
    """The boundary rewrite (spec section 4): vision never learns the
    agents vocabulary; the runtime hands it its own."""
    settings.CONVERSATION_FILES_DIR = tmp_path
    from agents.runtime import loop as loop_module
    staged = []
    def fake_resolver(dotted):
        assert dotted == "tools.vision.services.stage_upload"
        def stage(param_key, uploaded):
            staged.append((param_key, uploaded.name))
            return "input:77"
        return stage
    monkeypatch.setattr(loop_module, "resolve_dotted_path", fake_resolver)
    conversation = make_conversation()
    row = ...  # a ConversationFile with real bytes, the Task 6 _row shape
    args = loop_module._rewrite_file_refs(
        {"image": f"file:{row.pk}", "prompt": "paint it"}, conversation)
    assert args["image"] == "input:77"
    assert args["prompt"] == "paint it"
    assert staged == [("image", row.original_name)]

def test_anothers_conversations_file_ref_is_left_for_vision_to_refuse(self, ...):
    # same shape; a row on a DIFFERENT conversation stays "file:<pk>" verbatim
```

- [ ] **Step 2: Run to verify failure.**

- [ ] **Step 3: Implement.** In `loop.py`, beside the `_VISION_GENERATE_KEY` machinery:

```python
def _rewrite_file_refs(raw_args: dict, conversation) -> dict:
    """`file:<pk>` -> a freshly staged `input:<id>`, for vision only
    (spec section 4). SAME-CONVERSATION rows only; anything else is left
    verbatim for vision's own parser to refuse by name -- an honest
    refusal downstream beats a silent drop here. `stage_upload` is
    reached by dotted path (import-law rule 3, the narrowing precedent
    directly below this function's caller); `django.core.files.File`
    supplies the `.chunks()` shape it reads off a browser upload."""
    from agents.contracts.artifacts import parse_artifact
    from agents.models import ConversationFile
    out = dict(raw_args)
    for key, value in raw_args.items():
        if not isinstance(value, str):
            continue
        try:
            kind, pk = parse_artifact(value)
        except ValueError:
            continue
        if kind != "file":
            continue
        row = ConversationFile.objects.filter(
            pk=pk, conversation_id=conversation.id).first()
        if row is None:
            logger.warning("loop: vision arg %r names %r outside conversation %s; "
                           "left for the tool to refuse", key, value, conversation.id)
            continue
        try:
            from django.core.files import File
            stage = resolve_dotted_path("tools.vision.services.stage_upload")
            with open(row.path, "rb") as handle:
                wrapped = File(handle, name=row.original_name)
                wrapped.content_type = row.media_type
                out[key] = stage(key, wrapped)
        except Exception:  # noqa: BLE001 -- leave verbatim; vision refuses honestly
            logger.warning("loop: staging %r for vision failed; ref left verbatim",
                           value, exc_info=True)
    return out
```

and at the call site (line ~390): `if key == _VISION_GENERATE_KEY: raw_args = _rewrite_file_refs(raw_args, conversation)` immediately before the `invoke_tool` dispatch.

- [ ] **Step 4: Run** the loop tests + `pytest agents/runtime -q` → PASS.

- [ ] **Step 5: Commit** — `git commit -m "feat(agents): rewrite file: refs to staged vision inputs at the tool boundary"`.

---

### Task 8: CUT — ingest-from-attachment is blocked on the mutating-tools policy

**Do not implement.** The spec's "add this to the library" path cannot ship in this plan:
`RAG_INGEST` is `mutates=True` (tools/rag/tools.py:109), and the platform deliberately makes a
mutating tool ungrantable until Identity & Auth's mutating-tools ruling lands —
`agents/contracts/tools.py:424` drops it from `granted_tools`, `agents/models.py:176` refuses an
Agent row listing it, and `agents/defaults.py:329-331` records the ADR 0010:266-276 rationale by
name. A `file` param added today would be a param no caller can ever exercise.

Recorded instead as a named future seam in the spec (amended alongside this cut): when the owner
rules on granting mutating tools, the implementation is exactly the two pieces this task
originally specified — `agents.runtime.attachments.ingest_source_path(reference,
conversation_id)` (conversation-checked path accessor, reached by dotted path from
`run_ingest`) and an optional `file` param on `RAG_INGEST` with "exactly one of
document_id/file". The design needs no other change; only the policy gate does. Owner decision
required before any of it is built.

---

### Task 9: Docs, ADR amendment, structural guards, full gates

**Files:**
- Modify: `agents/README.md`, `agents/chat/README.md`, `docs/adr/0015-agent-layer-and-tool-contract.md` (dated amendment section), `docs/ARCHITECTURE.md` (one paragraph, if it names the turn contract)
- Test: whole suite, both orders

- [ ] **Step 1: Write the docs.** Each README gains an "Attachments" section: the store shape, the `file:` grammar, the sidecar contract, the boundary rewrite, the capability gate — three to six sentences each, citing files. ADR 0015 gains a dated amendment: "2026-09-03 — chat attachments: the turn contract carries `file:<id>` artifact references; conversation-scoped storage under `CONVERSATION_FILES_DIR`; extraction inline in `agent.turn`; tools never learn the `file:` vocabulary (boundary rewrite)."
- [ ] **Step 2: Full gates.** `pytest -q` then `pytest -q scripts foundation models tools` — both green, with pass-count arithmetic stated against the branch-base baseline (run the base count first if unknown).
- [ ] **Step 3: Commit** — `git commit -m "docs(agents): chat attachments — READMEs, ADR 0015 amendment"`.

---

## Execution notes (orchestrator)

- Tasks 1→2→3 are strictly sequential; 4 and 5 both depend on 2+3 and are independent of each other; 6 depends on 2+3; 7 depends on 2; Task 8 is CUT (see its blocker note); 9 last.
- After all tasks: whole-branch review (two implementers minimum will have touched shared files — re-verify citations, the PR #67 lesson), then the owner verification ladder (`verify-deployed-work`): preview stack screenshots in the attached-file states, PR, owner merge word, live deploy, fresh :8000 pixels.
- Peer announcement before merge: `models/contracts/catalog.py` field (Task 6).


## Plan review

Adversarial hygiene review (opus reviewer, read-only, verified against the worktree code), 2026-09-03:

- Round 1: AMEND, 12 findings — most severe: `rag.ingest` is `mutates=True` and ungrantable (ADR 0010:266-276), so Task 8 was CUT and the spec amended; also store module moved to the column root (`agents/store.py`), Task 4 transaction ordering fixed, route-matrix and owned-rows exact-set gates added, answered-turn-only prompt gating, textarea `required` removal, never-500 driver signatures, `medium_for` single-sourcing, `basename` made public. Applied in dddce86.
- Round 2 (scoped): AMEND, 4 findings — `_attachment_roles` re-introduced an extension taxonomy (now resolves `medium_for` by dotted path); three stale Task-8 references (Goal line, Global Constraints, spec future-seam). Applied in beb4a0e.
- Round 3 (scoped, final): AMEND, 1 residual + 1 optional — spec §6's checklist still named the cut ingest test; Non-goals wording. **Orchestrator adjudication (3-round cap reached): both ACCEPTED** — same contradiction class as the Task 8 cut, mechanically correct; applied in this commit. All other spots verified coherent and mutually consistent by the reviewer. Plan is cleared for execution.
