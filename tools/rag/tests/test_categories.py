"""Unit tests for tools/rag/models.py::Category and Document.category
(ADR 0008, ADR 0009).

Covers Category CRUD, __str__, ordering, the Document<->Category FK, the
SET_NULL-on-delete "reassign to Uncategorized" behavior, and that the
0004_seed_default_categories data migration produced the expected defaults.
"""
import pytest
from django.db import IntegrityError, transaction

from tools.rag.models import Category, Document


@pytest.mark.django_db
class TestCategoryCRUD:
    def test_create_category(self):
        category = Category.objects.create(name="Agriculture")
        assert category.pk is not None
        assert category.name == "Agriculture"
        assert category.created_at is not None

    def test_name_must_be_unique(self):
        Category.objects.create(name="Astronomy")
        with pytest.raises(Exception):
            Category.objects.create(name="Astronomy")

    def test_update_category(self):
        category = Category.objects.create(name="Old Name")
        category.name = "New Name"
        category.save()

        refreshed = Category.objects.get(pk=category.pk)
        assert refreshed.name == "New Name"

    def test_delete_category(self):
        category = Category.objects.create(name="Temporary")
        pk = category.pk
        category.delete()
        assert not Category.objects.filter(pk=pk).exists()

    def test_str_returns_name(self):
        category = Category.objects.create(name="Aviation")
        assert str(category) == "Aviation"

    def test_default_ordering_is_by_name(self):
        Category.objects.create(name="Zebra")
        Category.objects.create(name="Apple")
        Category.objects.create(name="Mango")

        names = list(Category.objects.values_list("name", flat=True))
        assert names == sorted(names)


@pytest.mark.django_db
class TestDocumentCategoryRelationship:
    def _make_document(self, **kwargs):
        defaults = dict(
            title="doc.txt",
            source_path="/data/documents/1/doc.txt",
            file_hash="a" * 64,
            doc_type=Document.DocType.PROSE,
        )
        defaults.update(kwargs)
        return Document.objects.create(**defaults)

    def test_document_can_be_assigned_a_category(self):
        category = Category.objects.create(name="Reference")
        doc = self._make_document(category=category)

        assert doc.category_id == category.id
        assert doc in category.documents.all()

    def test_document_category_defaults_to_null_uncategorized(self):
        doc = self._make_document()
        assert doc.category is None

    def test_deleting_category_sets_documents_category_to_null(self):
        category = Category.objects.create(name="Woodworking")
        doc = self._make_document(category=category)

        category.delete()

        doc.refresh_from_db()
        assert doc.category is None

    def test_deleting_category_does_not_delete_document(self):
        category = Category.objects.create(name="Woodworking")
        doc = self._make_document(category=category)

        category.delete()

        assert Document.objects.filter(pk=doc.pk).exists()

    def test_multiple_documents_in_one_category(self):
        category = Category.objects.create(name="Manuals")
        doc1 = self._make_document(title="a.txt", source_path="/a.txt", category=category)
        doc2 = self._make_document(title="b.txt", source_path="/b.txt", category=category)

        assert set(category.documents.all()) == {doc1, doc2}


@pytest.mark.django_db
class TestSeedDataMigration:
    """The 0004_seed_default_categories data migration should have created
    these on a fresh `migrate` (this test doesn't re-run the migration --
    pytest-django's test DB is built by running all migrations -- it just
    asserts the expected end state exists)."""

    def test_default_categories_exist(self):
        names = set(Category.objects.values_list("name", flat=True))
        expected = {"Medical", "Engineering", "Reference & Manuals", "Business"}
        assert expected.issubset(names)

    def test_uncategorized_is_not_seeded_as_a_row(self):
        assert not Category.objects.filter(name__iexact="Uncategorized").exists()


from tools.rag import categories as cats


class TestNormalizeCategoryName:
    def test_strips_and_collapses_whitespace(self):
        assert cats.normalize_category_name("  Field   Guide ") == "Field Guide"

    def test_blank_becomes_empty(self):
        assert cats.normalize_category_name("   ") == ""
        assert cats.normalize_category_name("") == ""


class _Stub:
    def __init__(self, name, created_at, pk):
        self.name = name
        self.created_at = created_at
        self.id = pk


class TestChooseCanonical:
    def test_prefers_seeded_name(self):
        members = [_Stub("medical", 2, 10), _Stub("Medical", 1, 5)]
        # 'Medical' is seeded even though 'medical' is older/earlier id.
        assert cats.choose_canonical(members, {"Medical"}).name == "Medical"

    def test_falls_back_to_earliest_when_none_seeded(self):
        members = [_Stub("hvac", 2, 10), _Stub("HVAC", 1, 5)]
        assert cats.choose_canonical(members, {"Medical"}).id == 5


@pytest.mark.django_db
class TestGetOrCreateCategory:
    def test_blank_returns_none(self):
        assert cats.get_or_create_category("  ") is None

    def test_reuses_case_insensitive_match(self):
        # "Medical" is seeded by the data migration, so fetch it
        seeded = Category.objects.get(name="Medical")
        got = cats.get_or_create_category("medical")
        assert got.id == seeded.id
        assert Category.objects.filter(name__iexact="medical").count() == 1

    def test_creates_with_given_casing_when_new(self):
        got = cats.get_or_create_category("  Field  Guide ")
        assert got.name == "Field Guide"


# C-60: the merge-function test that used to sit here was permanently
# skipped -- migration 0007's case-insensitive unique constraint makes the
# collision it constructed un-creatable, so it could never be un-skipped
# without undoing 0007. Its own skip reason named the real coverage, which
# is where it still lives: `choose_canonical`'s unit tests, plus the
# live-database migrate verification.


@pytest.mark.django_db
class TestCategoryCaseInsensitiveUniqueness:
    def test_rejects_case_variant_insert(self):
        Category.objects.create(name="Foo")
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                Category.objects.create(name="foo")
