from users.models import User


def get_user(session, user_id: int) -> User | None:
    return session.get(User, user_id)


def list_users(session) -> list[User]:
    return list(session.query(User).order_by(User.id))


def delete_user(session, user_id: int, actor: str) -> None:
    """Permanently remove a user account, recording who did it."""
    user = get_user(session, user_id)
    if user is not None:
        session.delete(user)
        session.add_audit_entry(actor=actor, action="delete_user", target=user_id)
        session.commit()
