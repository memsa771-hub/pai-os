"""Remove the launcher device-node pairing subsystem.

Revision ID: 050
Revises: 049
Create Date: 2026-09-14
"""

import sqlalchemy as sa
from alembic import op

revision = "050"
down_revision = "049"
branch_labels = None
depends_on = None


def _tables(inspector):
    return set(inspector.get_table_names())


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = _tables(inspector)

    if "workspace_members" in tables:
        columns = {column["name"] for column in inspector.get_columns("workspace_members")}
        if "node_id" in columns:
            for fk in inspector.get_foreign_keys("workspace_members"):
                if "node_id" in (fk.get("constrained_columns") or []) and fk.get("name"):
                    op.drop_constraint(fk["name"], "workspace_members", type_="foreignkey")
            op.drop_column("workspace_members", "node_id")

    # Commands depend on nodes; pairing codes and nodes depend on workspaces.
    for table in ("node_commands", "node_pairing_codes", "nodes"):
        if table in tables:
            op.drop_table(table)


def downgrade() -> None:
    # This cleanup intentionally does not recreate deleted device credentials,
    # pairing codes, commands, or associations. Historical migrations 030,
    # 031, and 045 retain the original schema definition when reconstruction
    # is required for forensic or archival purposes.
    pass
