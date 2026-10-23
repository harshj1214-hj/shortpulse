"""Configuration. Everything deploy-specific comes from environment variables."""
from __future__ import annotations

import logging
import os
import secrets
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
log = logging.getLogger("shortpulse")


def _bool(name: str, default: bool = False) -> bool:
    return os.environ.get(name, str(int(default))).lower() in ("1", "true", "yes", "on")


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY") or ""
    DATABASE = os.environ.get("DATABASE_PATH", str(ROOT / "instance" / "shortpulse.db"))
    BASE_URL = os.environ.get("BASE_URL", "").rstrip("/")  # e.g. https://shortpulse.onrender.com
    IP_HASH_SALT = os.environ.get("IP_HASH_SALT") or ""
    MODEL_DIR = os.environ.get("MODEL_DIR", str(ROOT / "ml" / "models"))
    GEOLITE2_PATH = os.environ.get("GEOLITE2_PATH", "")  # path to GeoLite2-City.mmdb (optional)
    # Google Analytics 4 for ShortPulse's own pages (never on short links or landing pages). Empty = off.
    GA_MEASUREMENT_ID = os.environ.get("GA_MEASUREMENT_ID", "").strip()
    # Android app (Trusted Web Activity): package name and signing-key SHA-256 fingerprint(s)
    # from PWABuilder. Served at /.well-known/assetlinks.json so Android trusts the app.
    TWA_PACKAGE_NAME = os.environ.get("TWA_PACKAGE_NAME", "").strip()
    TWA_SHA256_FINGERPRINTS = os.environ.get("TWA_SHA256_FINGERPRINTS", "").strip()
    # Number of reverse proxies in front of the app (Render: 1). Keep 0 when clients
    # connect directly, otherwise anyone can spoof X-Forwarded-For and dodge rate limits.
    TRUSTED_PROXIES = int(os.environ.get("TRUSTED_PROXIES", "0"))
    SEED_ON_START = _bool("SEED_ON_START")
    ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "")
    ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
    DEMO_PASSWORD = os.environ.get("DEMO_PASSWORD", "")

    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = _bool("SESSION_COOKIE_SECURE")
    MAX_CONTENT_LENGTH = 1 * 1024 * 1024  # 1 MB uploads (CSV import)

    CODE_LENGTH = 7  # 62^7 ≈ 3.5 trillion codes

    # Token buckets: (capacity, refill tokens per second)
    RATE_CREATE = (10, 10 / 60)     # 10 links burst, then 10 per minute per user
    RATE_IMPORT = (3, 3 / 300)      # 3 CSV commits per 5 minutes per user
    RATE_LOGIN = (5, 1 / 12)        # 5 attempts burst, then 5 per minute per IP
    RATE_PASSWORD = (5, 1 / 12)     # per IP + link, slows password guessing

    RATE_BEACON = (60, 2)           # engagement beacons per visitor IP

    # Visitor IPs: the full address is kept for abuse investigation, then erased.
    STORE_FULL_IP = _bool("STORE_FULL_IP", True)
    IP_RETENTION_DAYS = int(os.environ.get("IP_RETENTION_DAYS", "30"))
    OWNER_SEES_FULL_IP = _bool("OWNER_SEES_FULL_IP", False)  # admins always do

    URL_HIGH_RISK = 0.8             # needs explicit acknowledgement + admin review
    IMPORT_MAX_ROWS = 500
    CLICK_QUEUE_SIZE = 10_000

    TESTING = False

    @classmethod
    def finalize(cls, cfg: dict) -> None:
        if not cfg.get("SECRET_KEY"):
            cfg["SECRET_KEY"] = secrets.token_hex(32)
            log.warning("SECRET_KEY not set: sessions reset on every restart and break "
                        "across multiple workers. Set SECRET_KEY in production.")
        if not cfg.get("IP_HASH_SALT"):
            cfg["IP_HASH_SALT"] = cfg["SECRET_KEY"]
