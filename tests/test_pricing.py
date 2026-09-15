import json
import tempfile
import unittest
from pathlib import Path

try:
    from usage_note.pricing import PriceBook
except ImportError:
    PriceBook = None


class PriceBookTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.price_path = self.root / 'prices.json'
        self.overrides = self.root / 'overrides.json'
        self.price_path.write_text(json.dumps({'models': {
            'example': {'input': 2, 'cached': 0.2, 'output': 8,
                        'source': 'https://example.com/pricing'}
        }}), encoding='utf-8')

    def book(self):
        self.assertIsNotNone(PriceBook, 'PriceBook has not been implemented')
        return PriceBook(self.price_path, self.overrides)

    def test_disjoint_token_categories_cost_6_40(self):
        quote = self.book().quote('example', {'input': 1_000_000, 'cached': 2_000_000,
                                             'output': 500_000})
        self.assertEqual(quote['input_usd'], 2)
        self.assertEqual(quote['cached_usd'], 0.4)
        self.assertEqual(quote['output_usd'], 4)
        self.assertEqual(quote['total_usd'], 6.4)

    def test_unknown_alias_is_not_assigned_a_similar_models_price(self):
        quote = self.book().quote('example-preview', {'input': 100, 'cached': 0, 'output': 10})
        self.assertIsNone(quote['total_usd'])
        self.assertIsNone(quote['rates'])

    def test_manual_rate_persists_and_replaces_official_rate(self):
        book = self.book()
        book.set_override('example', '3', '0.30', '9')
        quote = self.book().quote('example', {'input': 1_000_000, 'cached': 0, 'output': 0})
        self.assertEqual(quote['total_usd'], 3)
        self.assertEqual(quote['source'], '手动设置')

    def test_non_finite_or_negative_prices_do_not_overwrite_good_rates(self):
        book = self.book()
        for invalid in [-1, 'nan', 'inf', '-Infinity', 'abc']:
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                book.set_override('example', invalid, 0.2, 8)
        self.assertFalse(self.overrides.exists())

    def test_zero_is_a_valid_manual_price(self):
        book = self.book()
        book.set_override('free', 0, 0, 0)
        self.assertEqual(book.quote('free', {'input': 2_000})['total_usd'], 0)

    def test_corrupt_override_is_reported_without_hiding_builtin_prices(self):
        self.overrides.write_text('{broken', encoding='utf-8')
        book = self.book()
        self.assertTrue(book.warnings)
        self.assertEqual(book.quote('example', {'input': 1_000_000})['total_usd'], 2)

    def test_cache_write_replaces_ordinary_input_rate_without_double_counting(self):
        book = self.book()
        book.set_override('example', 2, 0.2, 8, cache_write=2.5)
        quote = book.quote('example', {'input': 1_000_000, 'cache_write': 200_000,
                                      'cached': 1_000_000, 'output': 0})
        self.assertEqual(quote['input_usd'], 2.1)
        self.assertEqual(quote['cache_write_usd'], 0.5)
        self.assertEqual(quote['total_usd'], 2.3)

    def test_missing_write_price_returns_partial_total(self):
        quote = self.book().quote('example', {'input': 1_000_000, 'cache_write': 200_000,
                                             'cached': 1_000_000})
        self.assertIsNone(quote['total_usd'])
        self.assertIsNone(quote['input_usd'])
        self.assertTrue(quote['partial'])
        self.assertEqual(quote['known_usd'], 1.8)


if __name__ == '__main__':
    unittest.main()
