from booking.models import Booking
from booking.notifications import NoticeSink
from booking.service import cancel_booking


def cancel_endpoint(booking: Booking, sink: NoticeSink) -> dict[str, str]:
    return {"status": cancel_booking(booking, sink)}
