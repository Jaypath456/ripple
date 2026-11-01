from warehouse.availability import free_units


def stock_status(on_hand: int, reserved: int) -> str:
    available = free_units(on_hand, reserved)
    if available == 0:
        return "out"
    return "low" if available < 5 else "available"
