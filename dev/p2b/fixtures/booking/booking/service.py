from booking.models import Booking
from booking.notifications import NoticeSink, format_confirmation


def confirm_booking(booking: Booking, sink: NoticeSink) -> str:
    booking.status = "confirmed"
    sink.send(format_confirmation(booking.booking_id))
    return booking.status


def cancel_booking(booking: Booking, sink: NoticeSink) -> str:
    if booking.status == "cancelled":
        return booking.status
    booking.status = "cancelled"
    return booking.status
