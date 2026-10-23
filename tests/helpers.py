import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from werkzeug.security import generate_password_hash  # noqa: E402

from app import create_app  # noqa: E402
from app.db import connect  # noqa: E402
from app.engine import Engine  # noqa: E402

BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36")
HUMAN = {"User-Agent": BROWSER_UA, "Accept-Language": "en-IN,en;q=0.9"}


class AppTestCase(unittest.TestCase):
    csrf_disabled = True

    def setUp(self):
        # Windows can't delete files that are still open; don't fail a passing test over cleanup.
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db_path = os.path.join(self.tmp.name, "test.db")
        Engine().reset()  # the C library's state is shared by every app in this process
        self.app = create_app({"TESTING": True, "CSRF_DISABLED": self.csrf_disabled, "DATABASE": self.db_path,
                               "SECRET_KEY": "test", "SEED_ON_START": False, "BASE_URL": ""})
        self.app.extensions["engine"].rate_reset()
        self.client = self.app.test_client()
        self.user_id = self.make_user("user@example.com")
        self.other_id = self.make_user("other@example.com")
        self.admin_id = self.make_user("admin@example.com", role="admin")

    def tearDown(self):
        self.app.extensions["clicklog"].flush()
        self.app.extensions["clicklog"].stop()
        self.tmp.cleanup()

    # -- helpers ---------------------------------------------------------
    def db(self):
        return connect(self.db_path)

    def make_user(self, email, role="user"):
        conn = self.db()
        try:
            return conn.execute("INSERT INTO users (email, password_hash, role, created_at) VALUES (?, ?, ?, ?)",
                                (email, generate_password_hash("password123"), role, int(time.time()))).lastrowid
        finally:
            conn.close()

    def login(self, user_id, client=None):
        client = client or self.client
        with client.session_transaction() as s:
            s["user_id"] = user_id
        return client

    def create_link(self, client=None, **payload):
        client = client or self.client
        payload.setdefault("destination", "https://example.com/page")
        resp = client.post("/api/links", json=payload)
        return resp

    def flush(self):
        self.assertTrue(self.app.extensions["clicklog"].flush(), "click queue did not drain")

    def one(self, sql, params=()):
        conn = self.db()
        try:
            return conn.execute(sql, params).fetchone()
        finally:
            conn.close()
