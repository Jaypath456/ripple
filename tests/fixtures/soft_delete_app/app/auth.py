from app.models import User


def authenticate(user: User) -> bool:
    return bool(user.id)
