from warehouse.status import stock_status


def inventory_badge(on_hand: int, reserved: int) -> dict[str, str]:
    return {"status": stock_status(on_hand, reserved)}
