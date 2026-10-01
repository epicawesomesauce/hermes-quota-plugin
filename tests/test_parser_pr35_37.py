"""Focused parser regressions for explicit zero and subscription vetoes."""
import importlib
import sys
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


class KimiExplicitZeroTests(unittest.TestCase):
    def setUp(self):
        self.mod = importlib.import_module("quota_providers.kimi")

    def test_zero_used_does_not_fall_back_to_nested_used(self):
        window = self.mod._parse_block({
            "limit": 100,
            "used": 0,
            "detail": {"used": 50},
        })
        self.assertEqual(window.used_percent, 0.0)

    def test_zero_limit_does_not_fall_back_to_nested_limit(self):
        window = self.mod._parse_block({
            "limit": 0,
            "detail": {"limit": 100, "used": 50},
        })
        self.assertIsNone(window.used_percent)

    def test_zero_remaining_does_not_fall_back_to_nested_remaining(self):
        window = self.mod._parse_block({
            "limit": 100,
            "remaining": 0,
            "detail": {"remaining": 50},
        })
        self.assertEqual(window.used_percent, 100.0)


class ZaiSubscriptionValidityTests(unittest.TestCase):
    def setUp(self):
        self.mod = importlib.import_module("quota_providers.zai")

    def test_explicit_valid_false_vetoes_active_status(self):
        self.assertEqual(
            self.mod._subscription_plan({"data": [{
                "productName": "Pro",
                "valid": False,
                "status": "ACTIVE",
            }]}),
            (None, None),
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
