# Enables the pgvector extension so LlamaIndex's PGVectorStore can create and
# use its `rag_chunks` table (vector column type). This must run before
# ingestion touches the vector store; it's independent of the Django model
# migrations above, which is why it's a separate, dependency-only migration.
from django.contrib.postgres.operations import CreateExtension
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("rag", "0001_initial"),
    ]

    operations = [
        CreateExtension("vector"),
    ]
