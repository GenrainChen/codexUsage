"""Speed variants must remain distinct while sharing their base model's rates."""

import unittest

from usage_note.models import base_model, model_key, model_label, model_service_tier


class ModelTests(unittest.TestCase):
    def test_speed_tiers_round_trip_to_base_model_and_label(self):
        for raw, tier, suffix in (("fast", "fast", "FAST"),
                                  ("priority", "fast", "FAST"),
                                  ("ultrafast", "ultrafast", "ULTRAFAST"),
                                  (" ULTRAFAST ", "ultrafast", "ULTRAFAST")):
            with self.subTest(service_tier=raw):
                key = model_key("gpt-6.1-sol", raw)
                self.assertEqual(key, f"gpt-6.1-sol [{suffix}]")
                self.assertEqual(base_model(key), "gpt-6.1-sol")
                self.assertEqual(model_label(key), f"gpt-6.1-sol · {suffix}")
                self.assertEqual(model_service_tier(key), tier)

    def test_standard_and_unknown_tiers_preserve_existing_identity(self):
        for tier in (None, "", "default", "standard", "flex", "future-tier"):
            with self.subTest(service_tier=tier):
                key = model_key("example", tier)
                self.assertEqual(key, "example")
                self.assertEqual(base_model(key), "example")
                self.assertEqual(model_label(key), "example")
                self.assertIsNone(model_service_tier(key))


if __name__ == "__main__":
    unittest.main()
