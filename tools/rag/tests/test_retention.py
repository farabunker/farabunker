"""What `tools/rag` destroys for a deleted conversation."""
from __future__ import annotations

import uuid

import pytest
from django.conf import settings

from tools.rag.models import Document
from tools.rag.retention import purge_conversation_notes
from tools.rag.tests._helpers import make_document

pytestmark = pytest.mark.django_db


class TestTheStagingNote:
    def test_it_unlinks_the_note_file_and_deletes_the_note_document(self, tmp_path, settings):
        settings.NOTES_DIR = tmp_path
        conversation_id = uuid.uuid4()
        note = tmp_path / f"{conversation_id}.md"
        note.write_text("a consolidated stream", encoding="utf-8")
        document = make_document(notes_conversation_id=conversation_id)

        removed = purge_conversation_notes(str(conversation_id))

        assert not note.exists()
        assert not Document.objects.filter(pk=document.pk).exists()
        assert removed == 2

    def test_a_missing_file_is_not_an_error(self, tmp_path, settings):
        """A purge that failed because somebody had already cleaned up
        would be a purge nobody could finish."""
        settings.NOTES_DIR = tmp_path
        assert purge_conversation_notes(str(uuid.uuid4())) == 0

    def test_it_is_idempotent(self, tmp_path, settings):
        settings.NOTES_DIR = tmp_path
        conversation_id = uuid.uuid4()
        (tmp_path / f"{conversation_id}.md").write_text("x", encoding="utf-8")
        make_document(notes_conversation_id=conversation_id)
        purge_conversation_notes(str(conversation_id))
        assert purge_conversation_notes(str(conversation_id)) == 0

    def test_an_unparseable_key_removes_nothing(self, tmp_path, settings):
        settings.NOTES_DIR = tmp_path
        assert purge_conversation_notes("not-a-uuid") == 0

    def test_another_conversations_note_is_untouched(self, tmp_path, settings):
        settings.NOTES_DIR = tmp_path
        mine, theirs = uuid.uuid4(), uuid.uuid4()
        (tmp_path / f"{mine}.md").write_text("x", encoding="utf-8")
        (tmp_path / f"{theirs}.md").write_text("y", encoding="utf-8")
        purge_conversation_notes(str(mine))
        assert (tmp_path / f"{theirs}.md").exists()
