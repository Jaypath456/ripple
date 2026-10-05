from users.models import User
from users.service import delete_user, get_user


class FakeSession:
    def __init__(self, users):
        self.users = {user.id: user for user in users}

    def get(self, model, key):
        return self.users.get(key)

    def commit(self):
        pass


def test_delete_user_marks_account_deleted():
    user = User(id=1, email="a@example.com", name="A", deleted_at=None)
    session = FakeSession([user])
    delete_user(session, 1)
    assert user.deleted_at is not None
    assert get_user(session, 1) is None
