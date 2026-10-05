from orders.service import cancel_order, create_order


def test_cancel_order():
    order = cancel_order(create_order(user_id=1, total_cents=500))
    assert order.status == "cancelled"
