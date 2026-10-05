from dataclasses import dataclass


@dataclass
class Order:
    id: int
    user_id: int
    total_cents: int
    status: str = "open"


def create_order(user_id: int, total_cents: int) -> Order:
    return Order(id=0, user_id=user_id, total_cents=total_cents)


def cancel_order(order: Order) -> Order:
    order.status = "cancelled"
    return order
