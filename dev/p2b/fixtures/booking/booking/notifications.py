from typing import Protocol


class NoticeSink(Protocol):
    def send(self, message: str) -> None: ...


def format_confirmation(booking_id: str) -> str:
    return f"Booking {booking_id} confirmed"
