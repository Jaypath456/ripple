import unittest

from units.convert import centimetres_to_metres, metres_to_centimetres
from units.display import metres_label


class UnitTests(unittest.TestCase):
    def test_conversion_round_trip(self):
        self.assertEqual(centimetres_to_metres(250), 2.5)
        self.assertEqual(metres_to_centimetres(2.5), 250)

    def test_display(self):
        self.assertEqual(metres_label(250), "2.50 m")
