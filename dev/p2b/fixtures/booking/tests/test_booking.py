import unittest

from booking.api import cancel_endpoint
from booking.models import Booking
from booking.notifications import format_confirmation
from booking.service import cancel_booking, confirm_booking


class Sink:
    def __init__(self):
        self.messages = []

    def send(self, message):
        self.messages.append(message)


class BookingTests(unittest.TestCase):
    def test_confirmation_formats_and_sends(self):
        booking, sink = Booking("sample-7"), Sink()
        self.assertEqual(confirm_booking(booking, sink), "confirmed")
        self.assertEqual(sink.messages, [format_confirmation("sample-7")])

    def test_existing_cancel_api_and_repeat_are_stable(self):
        booking, sink = Booking("sample-9"), Sink()
        self.assertEqual(cancel_endpoint(booking, sink), {"status": "cancelled"})
        self.assertEqual(cancel_booking(booking, sink), "cancelled")
        self.assertEqual(sink.messages, [])
