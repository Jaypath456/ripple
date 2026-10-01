from app.models import User
from app.service import delete_user


def test_delete_user() -> None:
    delete_user(User())
