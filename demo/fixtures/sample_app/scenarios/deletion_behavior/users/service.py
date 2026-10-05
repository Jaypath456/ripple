from users.models import User


def get_user(session, user_id: int) -> User | None:
    return session.get(User, user_id)


def list_users(session) -> list[User]:
    return list(session.query(User).order_by(User.id))


def delete_user(session, user_id: int) -> None:
    """Deactivate a user account instead of removing the row."""
    user = get_user(session, user_id)
    if user is not None:
        user.email = f"deactivated+{user.id}@example.invalid"
        user.name = "Deactivated user"
        session.commit()
