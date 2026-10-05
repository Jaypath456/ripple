from datetime import UTC, datetime

from users.models import User


def get_user(session, user_id: int) -> User | None:
    user = session.get(User, user_id)
    return None if user is None or user.deleted_at else user


def list_users(session) -> list[User]:
    query = session.query(User).filter(User.deleted_at.is_(None))
    return list(query.order_by(User.id))


def delete_user(session, user_id: int) -> None:
    """Soft-delete a user account by stamping deleted_at."""
    user = get_user(session, user_id)
    if user is not None:
        user.deleted_at = datetime.now(UTC)
        session.commit()
