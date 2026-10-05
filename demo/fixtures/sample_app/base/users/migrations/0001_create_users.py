"""Create the users table."""

revision = "0001"


def upgrade(op) -> None:
    op.create_table("users", "id", "email", "name")
