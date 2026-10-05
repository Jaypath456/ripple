"""Add users.deleted_at for soft deletion."""

revision = "0002"


def upgrade(op) -> None:
    op.add_column("users", "deleted_at")
