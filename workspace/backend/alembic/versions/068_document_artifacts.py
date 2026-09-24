"""Derived parse/processing state for student documents (PDF/DOCX).

Nothing canonical lives in this table: every column is rebuildable from the
raw file in workspace storage, so a downgrade costs a reprocess and never a
student fact.

Revision ID: 068
Revises: 067
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "068"
down_revision = "067"
branch_labels = None
depends_on = None

TABLE = "pai_document_artifacts"


def _has_table(inspector, name):
    return name in inspector.get_table_names()


def upgrade():
    inspector = sa.inspect(op.get_bind())
    if _has_table(inspector, TABLE):
        return

    op.create_table(
        TABLE,
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("workspace_id", sa.dialects.postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("file_id", sa.Text(),
                  sa.ForeignKey("files.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'queued'")),
        sa.Column("document_type", sa.Text(), nullable=True),
        sa.Column("detected_content_type", sa.Text(), nullable=True),
        sa.Column("content_sha256", sa.Text(), nullable=True),
        sa.Column("parser", sa.Text(), nullable=True),
        sa.Column("parser_version", sa.Text(), nullable=True),
        sa.Column("ocr_used", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("ocr_provider", sa.Text(), nullable=True),
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.Column("char_count", sa.Integer(), nullable=True),
        sa.Column("content", JSONB(), nullable=True),
        sa.Column("classification", sa.Text(), nullable=True),
        sa.Column("classification_confidence", sa.Float(), nullable=True),
        sa.Column("authority", sa.Text(), nullable=True),
        sa.Column("extractor_version", sa.Text(), nullable=True),
        sa.Column("extraction_summary", JSONB(), nullable=True),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
        sa.UniqueConstraint("file_id", name="uq_document_artifact_file"),
        sa.CheckConstraint(
            "status IN ('queued', 'processing', 'ready', 'partial', 'failed', 'unsupported')",
            name="ck_document_artifact_status",
        ),
    )
    op.create_index("idx_document_artifacts_ws_status", TABLE, ["workspace_id", "status"])
    op.create_index("idx_document_artifacts_ws_hash", TABLE, ["workspace_id", "content_sha256"])


def downgrade():
    inspector = sa.inspect(op.get_bind())
    if _has_table(inspector, TABLE):
        op.drop_index("idx_document_artifacts_ws_hash", table_name=TABLE)
        op.drop_index("idx_document_artifacts_ws_status", table_name=TABLE)
        op.drop_table(TABLE)
