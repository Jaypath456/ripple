from users.models import User


def get_user(session, user_id: int) -> User | None:
    return session.get(User, user_id)


def list_users(session) -> list[User]:
    return list(session.query(User))


def delete_user(session, user_id: int) -> None:
    user = get_user(session, user_id)
    if user is not None:
        session.delete(user)
        session.commit()
