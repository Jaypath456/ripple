from orders.service import create_order


def test_create_order():
    assert create_order(user_id=1, total_cents=500).total_cents == 500
