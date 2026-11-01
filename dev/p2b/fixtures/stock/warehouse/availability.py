def free_units(on_hand: int, reserved: int) -> int:
    """Return unreserved units without changing inventory."""
    return max(0, on_hand - reserved)
