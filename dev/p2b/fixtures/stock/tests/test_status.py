import unittest

from warehouse.api import inventory_badge
from warehouse.status import stock_status


class StockStatusTests(unittest.TestCase):
    def test_stock_status_subtracts_reservations(self):
        self.assertEqual(stock_status(8, 5), "low")
        self.assertEqual(stock_status(8, 8), "out")
        self.assertEqual(stock_status(8, 1), "available")

    def test_inventory_badge_preserves_default_status(self):
        self.assertEqual(inventory_badge(6, 2), {"status": "low"})
