from dataclasses import dataclass


@dataclass
class Booking:
    booking_id: str
    status: str = "confirmed"
