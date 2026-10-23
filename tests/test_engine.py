"""C engine behaviour, exercised through the same ctypes wrapper Flask uses."""
import threading
import unittest

from helpers import ROOT  # noqa: F401  (sets sys.path)

from app import engine as E
from app.engine import Engine


class EngineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.e = Engine()

    def setUp(self):
        self.e.rate_reset()

    def test_base62_roundtrip(self):
        for n in (0, 1, 61, 62, 3843, 3844, 10**12, 2**64 - 1):
            self.assertEqual(self.e.base62_decode(self.e.base62_encode(n)), n)
        self.assertEqual(self.e.base62_encode(62), "10")
        with self.assertRaises(ValueError):
            self.e.base62_decode("no-dash")

    def test_generated_codes(self):
        codes = {self.e.generate_code(7) for _ in range(5000)}
        self.assertEqual(len(codes), 5000)
        self.assertTrue(all(len(c) == 7 and c.isalnum() for c in codes))

    def test_alias_validation(self):
        self.assertEqual(self.e.alias_validate("ab"), 1)
        self.assertEqual(self.e.alias_validate("x" * 33), 2)
        self.assertEqual(self.e.alias_validate("has space"), 3)
        self.assertEqual(self.e.alias_validate("Dashboard"), 4)
        self.assertEqual(self.e.alias_validate("py-test_alias"), 0)
        self.e.alias_add("py-test_alias")
        self.assertEqual(self.e.alias_validate("py-test_alias"), 5)
        self.e.alias_remove("py-test_alias")
        self.assertEqual(self.e.alias_validate("py-test_alias"), 0)

    def test_rule_order(self):
        now = 1_000_000
        check = self.e.check_rules
        self.assertEqual(check(status="disabled", expires_at=now - 1, now=now, clicks=9, max_clicks=5,
                               has_password=True), E.DENY_DISABLED)
        self.assertEqual(check(status="active", expires_at=now - 1, now=now, clicks=9, max_clicks=5,
                               has_password=True), E.DENY_EXPIRED)
        self.assertEqual(check(status="active", expires_at=None, now=now, clicks=5, max_clicks=5,
                               has_password=True), E.DENY_LIMIT)
        self.assertEqual(check(status="active", expires_at=None, now=now, clicks=4, max_clicks=5,
                               has_password=True), E.NEED_PASSWORD)
        self.assertEqual(check(status="active", expires_at=None, now=now, clicks=4, max_clicks=5,
                               has_password=True, password_state=E.PW_WRONG), E.BAD_PASSWORD)
        self.assertEqual(check(status="active", expires_at=None, now=now, clicks=4, max_clicks=5,
                               has_password=True, password_state=E.PW_CORRECT), E.ALLOW)
        self.assertEqual(check(status="weird", expires_at=None, now=now, clicks=0, max_clicks=None,
                               has_password=False), E.DENY_DISABLED)

    def test_rate_limiter(self):
        t = 50_000
        self.assertTrue(all(self.e.rate_take("k", 3, 1.0, t) == 0 for _ in range(3)))
        self.assertGreater(self.e.rate_take("k", 3, 1.0, t), 0)
        self.assertEqual(self.e.rate_take("k", 3, 1.0, t + 1000), 0)

    def test_rate_limiter_threads(self):
        granted = []

        def worker():
            n = sum(self.e.rate_take("shared", 50, 0.0, 1) == 0 for _ in range(200))
            granted.append(n)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for th in threads:
            th.start()
        for th in threads:
            th.join()
        self.assertEqual(sum(granted), 50)


if __name__ == "__main__":
    unittest.main()
