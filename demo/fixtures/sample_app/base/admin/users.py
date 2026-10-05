from users.service import delete_user, list_users


def purge_test_accounts(session) -> int:
    """Remove accounts created by the QA team."""
    removed = 0
    for user in list_users(session):
        if user.email.endswith("@qa.example.com"):
            delete_user(session, user.id)
            removed += 1
    return removed
