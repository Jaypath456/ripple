from users.models import User
from users.service import delete_user, list_users


class FakeSession:
    def __init__(self, users):
        self.users = {user.id: user for user in users}

    def get(self, model, key):
        return self.users.get(key)

    def delete(self, user):
        self.users.pop(user.id)

    def commit(self):
        pass


def test_delete_user_removes_account():
    session = FakeSession([User(id=1, email="a@example.com", name="A")])
    delete_user(session, 1)
    assert session.get(User, 1) is None


def test_list_users_is_callable():
    assert callable(list_users)
