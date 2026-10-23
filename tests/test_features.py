"""App switching, engagement beacon, visitor IPs, CSV export, QR studio, migrations, smart paste."""
import json
import shutil
import sqlite3
import subprocess
import sys
import time
import types
import unittest
import xml.etree.ElementTree as ET
from io import BytesIO
from pathlib import Path

from helpers import HUMAN, ROOT, AppTestCase

from app import deeplink
from app.clicklog import purge_ips
from app.db import init_schema
from app.redirect import add_query_param
from app.security import mask_ip

try:
    import cv2
    import numpy as np
except ImportError:  # QR scannability tests need OpenCV
    cv2 = None

ANDROID = {"User-Agent": "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) "
                         "Chrome/129.0.0.0 Mobile Safari/537.36", "Accept-Language": "en-IN"}
IPHONE = {"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_6 like Mac OS X) AppleWebKit/605.1.15 "
                        "(KHTML, like Gecko) Version/17.6 Mobile/15E148 Safari/604.1", "Accept-Language": "en-IN"}


class DeepLinkTests(unittest.TestCase):
    def test_detects_supported_apps(self):
        cases = {
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=5": ("youtube", "vnd.youtube://dQw4w9WgXcQ"),
            "https://youtu.be/dQw4w9WgXcQ": ("youtube", "vnd.youtube://dQw4w9WgXcQ"),
            "https://m.youtube.com/shorts/dQw4w9WgXcQ": ("youtube", "vnd.youtube://dQw4w9WgXcQ"),
            "https://www.youtube.com/@GoogleDevelopers": ("youtube", "youtube://www.youtube.com/@GoogleDevelopers"),
            "https://open.spotify.com/intl-en/track/4uLU6hMCjMI75M1A2tKUQC?si=x": ("spotify", "spotify:track:4uLU6hMCjMI75M1A2tKUQC"),
            "https://www.instagram.com/natgeo/": ("instagram", "instagram://user?username=natgeo"),
            "https://www.instagram.com/p/Cxyz123/": ("instagram", None),
        }
        for url, (platform, ios) in cases.items():
            with self.subTest(url=url):
                t = deeplink.detect(url)
                self.assertEqual((t.platform, t.ios), (platform, ios))
                self.assertIn("S.browser_fallback_url=", t.android)
        for url in ("https://example.com/watch?v=dQw4w9WgXcQ", "https://open.spotify.com/", "https://youtu.be/"):
            self.assertIsNone(deeplink.detect(url))


class AppSwitchRedirectTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.login(self.user_id)
        self.visitor = self.app.test_client()

    def test_phones_get_the_app_desktops_get_a_redirect(self):
        self.create_link(alias="talk", destination="https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        android = self.visitor.get("/talk", headers=ANDROID)
        self.assertEqual(android.status_code, 200)
        self.assertIn(b"intent://www.youtube.com/watch?v=dQw4w9WgXcQ", android.data)
        self.assertIn(b"package=com.google.android.youtube", android.data)
        self.assertIn("no-store", android.headers["Cache-Control"])
        self.assertIn(b"vnd.youtube://dQw4w9WgXcQ", self.visitor.get("/talk", headers=IPHONE).data)
        self.assertEqual(self.visitor.get("/talk", headers=HUMAN).status_code, 302)
        self.flush()
        self.assertEqual(self.one("SELECT SUM(app_switch), COUNT(*) FROM click_events")[:], (2, 3))

    def test_switch_can_be_turned_off_and_skips_bots(self):
        link = self.create_link(alias="noapp", destination="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                                open_in_app=False).get_json()["link"]
        self.assertFalse(link["open_in_app"])
        self.assertEqual(self.visitor.get("/noapp", headers=ANDROID).status_code, 302)
        self.create_link(alias="yesapp", destination="https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        bot = {"User-Agent": ANDROID["User-Agent"] + " Googlebot/2.1"}
        self.assertEqual(self.visitor.get("/yesapp", headers=bot).status_code, 302)

    def test_instagram_posts_redirect_normally_on_iphone(self):
        self.create_link(alias="igpost", destination="https://www.instagram.com/p/Cxyz123/")
        self.assertEqual(self.visitor.get("/igpost", headers=IPHONE).status_code, 302)
        self.assertEqual(self.visitor.get("/igpost", headers=ANDROID).status_code, 200)

    def test_wizard_validation_reports_the_app(self):
        data = self.client.post("/api/links/validate", json={"destination": "https://open.spotify.com/track/4uLU6hMCjMI75M1A2tKUQC"}).get_json()
        self.assertEqual(data["app"], {"platform": "spotify", "label": "Spotify"})


class EngagementTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.login(self.user_id)
        self.visitor = self.app.test_client()

    def beacon(self, body):
        return self.visitor.post("/api/engage", data=json.dumps(body) if not isinstance(body, str) else body,
                                 content_type="text/plain;charset=UTF-8")

    def test_query_param_helper(self):
        self.assertEqual(add_query_param("https://a.example/p?x=1#top", "lp_cid", "abc"), "https://a.example/p?x=1&lp_cid=abc#top")
        self.assertEqual(add_query_param("https://a.example/p?lp_cid=old", "lp_cid", "new"), "https://a.example/p?lp_cid=new")

    def test_beacon_records_time_and_scroll(self):
        link = self.create_link(alias="mysite", destination="https://example.com/landing?ref=x", track_engagement=True).get_json()["link"]
        self.assertTrue(link["track_engagement"])
        loc = self.visitor.get("/mysite", headers=HUMAN).headers["Location"]
        self.assertRegex(loc, r"^https://example\.com/landing\?ref=x&lp_cid=[A-Za-z0-9_-]{16}$")
        cid = loc.rsplit("=", 1)[1]
        self.flush()
        resp = self.beacon({"cid": cid, "ms": 42000, "scroll": 80})
        self.assertEqual(resp.status_code, 204)
        self.assertEqual(resp.headers["Access-Control-Allow-Origin"], "*")
        self.beacon({"cid": cid, "ms": 1000, "scroll": 10})  # a later, smaller report never lowers the values
        self.assertEqual(tuple(self.one("SELECT engaged_ms, scroll_pct FROM click_events WHERE click_uid = ?", (cid,))), (42000, 80))
        eng = self.client.get(f"/api/analytics/link/{link['id']}?period=24h").get_json()["engagement"]
        self.assertEqual((eng["visits"], eng["median_seconds"], eng["avg_scroll"], eng["reached_75_pct"]), (1, 42.0, 80, 100))
        self.assertEqual(eng["funnel"], [{"depth": 25, "pct": 100}, {"depth": 50, "pct": 100},
                                         {"depth": 75, "pct": 100}, {"depth": 100, "pct": 0}])
        self.assertEqual(eng["reading_now"], 1)  # beacon arrived seconds ago

    def test_beacon_rejects_junk_and_untracked_links_get_no_id(self):
        self.create_link(alias="plain")
        self.assertNotIn("lp_cid", self.visitor.get("/plain", headers=HUMAN).headers["Location"])
        for bad in ('{"cid": "short", "ms": 1, "scroll": 1}', '{"cid": "AAAAAAAAAAAAAAAA", "ms": -5, "scroll": 1}',
                    '{"cid": "AAAAAAAAAAAAAAAA", "ms": 1, "scroll": 101}', "not json"):
            self.assertEqual(self.beacon(bad).status_code, 400, bad)
        self.assertEqual(self.visitor.options("/api/engage").status_code, 204)


class LandingPageTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.login(self.user_id)
        self.visitor = self.app.test_client()

    def test_landing_page_measures_every_visit(self):
        link = self.create_link(alias="festinfo", destination="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                                landing={"enabled": True, "heading": "Tech Fest <2026>", "message": "Line one\n\nLine two"}).get_json()["link"]
        self.assertTrue(link["landing_enabled"])
        page = self.visitor.get("/festinfo", headers=HUMAN)
        self.assertEqual(page.status_code, 200)
        html = page.get_data(as_text=True)
        self.assertIn("Tech Fest &lt;2026&gt;", html)                 # escaped
        self.assertIn('data-lp-out="Continue"', html)
        self.assertIn("https://www.youtube.com/watch?v=dQw4w9WgXcQ", html)
        self.assertEqual(html.count("<p>"), 2)
        cid = html.split('data-cid="')[1].split('"')[0]
        # landing page wins over app switching on phones
        from test_features import ANDROID
        self.assertNotIn("intent://", self.visitor.get("/festinfo", headers=ANDROID).get_data(as_text=True))
        self.flush()
        self.visitor.post("/api/engage", data=json.dumps({"cid": cid, "ms": 20000, "scroll": 100, "out": "Continue"}),
                          content_type="text/plain")
        eng = self.client.get(f"/api/analytics/link/{link['id']}?period=24h").get_json()["engagement"]
        self.assertEqual((eng["landing_views"], eng["continued_pct"], eng["reading_now"]), (2, 50, 1))
        clicks = self.client.get(f"/api/links/{link['id']}/clicks").get_json()["clicks"]
        self.assertIn("Continue", [c["outbound"] for c in clicks])

    def test_landing_page_editor_validates_buttons(self):
        link = self.create_link(alias="bio").get_json()["link"]
        url = f"/api/links/{link['id']}/landing"
        self.assertFalse(self.client.get(url).get_json()["page"]["enabled"])
        bad = self.client.put(url, json={"enabled": True, "links": [{"label": "Bad", "url": "javascript:alert(1)"},
                                                                     {"label": "", "url": "https://example.com"}]})
        self.assertEqual(bad.status_code, 422)
        self.assertEqual({e["code"] for e in bad.get_json()["errors"]}, {"link_url", "link_label"})
        ok = self.client.put(url, json={"enabled": True, "theme": "mint", "button_label": "Go",
                                        "links": [{"label": "Docs", "url": "docs.python.org"}]})
        self.assertEqual(ok.get_json()["page"]["links"], [{"label": "Docs", "url": "https://docs.python.org"}])
        html = self.visitor.get("/bio", headers=HUMAN).get_data(as_text=True)
        self.assertIn("theme-mint", html)
        self.assertIn('data-lp-out="Docs"', html)
        self.client.put(url, json={"enabled": False})
        self.assertEqual(self.visitor.get("/bio", headers=HUMAN).status_code, 302)
        other = self.login(self.other_id, self.app.test_client())
        self.assertEqual(other.get(url).status_code, 404)


class VisitorIPTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.login(self.user_id)
        self.link = self.create_link(alias="ips").get_json()["link"]
        self.app.test_client().get("/ips", headers={**HUMAN, "User-Agent": '=HYPERLINK("http://evil.example")'})
        self.flush()

    def test_owner_sees_masked_admin_sees_full(self):
        self.assertEqual(mask_ip("103.21.45.67"), "103.21.45.x")
        self.assertEqual(mask_ip("2401:4900:1c3a:5:0:0:0:1"), "2401:4900:1c3a::/48")
        self.assertEqual(mask_ip("not an ip"), "")
        owner = self.client.get(f"/api/links/{self.link['id']}/clicks").get_json()
        self.assertEqual((owner["full_ip"], owner["clicks"][0]["ip"]), (False, "127.0.0.x"))
        admin = self.login(self.admin_id, self.app.test_client())
        data = admin.get(f"/api/links/{self.link['id']}/clicks").get_json()
        self.assertEqual((data["full_ip"], data["clicks"][0]["ip"]), (True, "127.0.0.1"))

    def test_owners_can_be_shown_full_ips(self):
        self.assertIn("shortened IP addresses", self.client.get("/privacy").get_data(as_text=True))
        self.app.config["OWNER_SEES_FULL_IP"] = True
        data = self.client.get(f"/api/links/{self.link['id']}/clicks").get_json()
        self.assertEqual((data["full_ip"], data["clicks"][0]["ip"]), (True, "127.0.0.1"))
        self.assertIn("full IP address", self.client.get("/privacy").get_data(as_text=True))

    def test_full_ips_are_erased_after_retention(self):
        conn = self.db()
        conn.execute("UPDATE click_events SET ts = ts - 31 * 86400")
        self.assertEqual(purge_ips(conn, 30), 1)
        self.assertEqual(tuple(conn.execute("SELECT ip_address, ip_masked FROM click_events").fetchone()), (None, "127.0.0.x"))
        conn.close()

    def test_full_ip_storage_can_be_turned_off(self):
        self.app.extensions["clicklog"].store_full_ip = False
        self.app.test_client().get("/ips", headers=HUMAN)
        self.flush()
        self.assertEqual(self.one("SELECT COUNT(*) FROM click_events WHERE ip_address IS NULL")[0], 1)

    def test_csv_export_is_masked_and_formula_safe(self):
        resp = self.client.get(f"/api/links/{self.link['id']}/clicks.csv")
        self.assertEqual(resp.mimetype, "text/csv")
        body = resp.get_data(as_text=True)
        self.assertIn("127.0.0.x", body)
        self.assertNotIn("127.0.0.1", body)
        self.assertIn("'=HYPERLINK", body)
        other = self.login(self.other_id, self.app.test_client())
        self.assertEqual(other.get(f"/api/links/{self.link['id']}/clicks.csv").status_code, 404)


def cv2_matrix(text: str, ec: str):
    """Module matrix from OpenCV's encoder: an independent implementation used as the test oracle."""
    params = cv2.QRCodeEncoder_Params()
    params.correction_level = {"L": cv2.QRCodeEncoder_CORRECT_LEVEL_L, "M": cv2.QRCodeEncoder_CORRECT_LEVEL_M,
                               "Q": cv2.QRCodeEncoder_CORRECT_LEVEL_Q, "H": cv2.QRCodeEncoder_CORRECT_LEVEL_H}[ec]
    dark = cv2.QRCodeEncoder.create(params).encode(text) < 128
    ys, xs = np.where(dark)
    crop = dark[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    run = 0
    while crop[0, run]:
        run += 1
    m = run // 7
    n = crop.shape[0] // m
    return tuple(tuple(bool(crop[r * m + m // 2, c * m + m // 2]) for c in range(n)) for r in range(n))


def decode_png(data: bytes) -> str:
    img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    text, _, _ = cv2.QRCodeDetector().detectAndDecode(img)
    return text


def fake_qrcode_module():
    mod = types.ModuleType("qrcode")
    mod.constants = types.SimpleNamespace(ERROR_CORRECT_L="L", ERROR_CORRECT_M="M", ERROR_CORRECT_Q="Q", ERROR_CORRECT_H="H")

    class QRCode:
        def __init__(self, error_correction="M", border=4, box_size=10):
            self.ec, self.border = error_correction, border

        def add_data(self, data):
            self.data = data

        def make(self, fit=True):
            pass

        def get_matrix(self):
            return [list(row) for row in cv2_matrix(self.data, self.ec)]

    mod.QRCode = QRCode
    return mod


def png_logo() -> bytes:
    from PIL import Image, ImageDraw
    im = Image.new("RGBA", (200, 200), (255, 255, 255, 0))
    ImageDraw.Draw(im).ellipse([10, 10, 190, 190], fill=(214, 56, 61, 255))
    buf = BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


@unittest.skipIf(cv2 is None, "OpenCV not installed")
class QRStudioTests(AppTestCase):
    def setUp(self):
        super().setUp()
        from app import qrstudio
        self.qs = qrstudio
        qrstudio.build_matrix.cache_clear()
        sys.modules["qrcode"] = fake_qrcode_module()
        self.login(self.user_id)
        self.link = self.create_link(alias="poster").get_json()["link"]

    def tearDown(self):
        sys.modules.pop("qrcode", None)
        self.qs.build_matrix.cache_clear()
        super().tearDown()

    def test_options_are_validated(self):
        clean = self.qs.clean_options
        self.assertTrue(clean({"fg": "#FFFFFF", "bg": "#16243A"})[1])         # light on dark
        self.assertTrue(clean({"fg": "#777777", "bg": "#888888"})[1])         # too similar
        self.assertTrue(clean({"fg": "red"})[1])
        self.assertTrue(clean({"caption": "x" * 41})[1])
        opts, errors = clean({"fg": "#0a6fb8", "style": "dots", "margin": 99})
        self.assertEqual((errors, opts["fg"], opts["margin"]), ([], "#0A6FB8", 8))

    def test_every_style_still_scans(self):
        # OpenCV's detector is stricter than phone cameras, so passing here is a good sign, not a guarantee.
        texts = ("https://lp.example/poster?src=qr", "https://shortpulse-rkom.onrender.com/fest26?src=qr")
        for style in ("square", "rounded", "dots"):
            for eyes in ("square", "rounded"):
                for logo, ec in ((None, "M"), (png_logo(), "H")):
                    for size in (600, 800, 1200):
                        for text in texts:
                            with self.subTest(style=style, eyes=eyes, logo=bool(logo), size=size, text=text):
                                opts, _ = self.qs.clean_options({"style": style, "eyes": eyes, "fg": "#0A6FB8",
                                                                 "caption": "Scan to register"})
                                png = self.qs.render_png(cv2_matrix(text, ec), opts, size, logo)
                                self.assertEqual(decode_png(png), text)

    def test_svg_is_valid_and_caption_is_escaped(self):
        opts, _ = self.qs.clean_options({"caption": "<script>alert(1)</script>", "style": "rounded", "eyes": "rounded"})
        svg = self.qs.render_svg(cv2_matrix("https://lp.example/x", "H"), opts, png_logo())
        root = ET.fromstring(svg)
        self.assertFalse([el for el in root.iter() if el.tag.endswith("script")])
        self.assertIn("&lt;script&gt;", svg)

    def test_studio_endpoints(self):
        lid = self.link["id"]
        design = self.client.get(f"/api/links/{lid}/qr-design").get_json()
        self.assertEqual((design["options"]["style"], design["has_logo"]), ("square", False))
        self.assertEqual(self.client.put(f"/api/links/{lid}/qr-design", json={"fg": "#EEEEEE"}).status_code, 422)
        self.assertEqual(self.client.put(f"/api/links/{lid}/qr-design", json={"style": "dots", "fg": "#1F5E4B"}).status_code, 200)
        self.assertEqual(self.client.get(f"/api/links/{lid}/qr-design").get_json()["options"]["style"], "dots")

        png = self.client.get(f"/api/links/{lid}/qr.png?size=600")
        self.assertEqual(png.mimetype, "image/png")
        self.assertTrue(decode_png(png.data).endswith("/poster?src=qr"))
        self.assertEqual(self.client.get(f"/api/links/{lid}/qr.png?fg=%23FFFFFF&bg=%23000000").status_code, 422)

        bad = self.client.post(f"/api/links/{lid}/qr-logo", data={"file": (BytesIO(b"not an image"), "x.png")},
                               content_type="multipart/form-data")
        self.assertEqual(bad.status_code, 422)
        ok = self.client.post(f"/api/links/{lid}/qr-logo", data={"file": (BytesIO(png_logo()), "logo.png")},
                              content_type="multipart/form-data")
        self.assertEqual(ok.status_code, 200)
        self.assertTrue(self.client.get(f"/api/links/{lid}/qr-design").get_json()["has_logo"])
        with_logo = self.client.get(f"/api/links/{lid}/qr.png?size=800")
        self.assertTrue(decode_png(with_logo.data).endswith("/poster?src=qr"))

        svg = self.client.get(f"/api/links/{lid}/qr.svg?download=1")
        self.assertEqual(svg.mimetype, "image/svg+xml")
        self.assertIn("default-src 'none'", svg.headers["Content-Security-Policy"])
        self.assertIn("attachment", svg.headers["Content-Disposition"])

        other = self.login(self.other_id, self.app.test_client())
        self.assertEqual(other.get(f"/api/links/{lid}/qr.png").status_code, 404)
        self.assertEqual(self.client.get(f"/links/{lid}/studio").status_code, 200)
        self.assertEqual(self.client.get(f"/links/{lid}/poster").status_code, 200)


class GoogleAnalyticsTests(AppTestCase):
    def test_only_on_shortpulse_pages_never_on_visitor_pages(self):
        self.app.config["GA_MEASUREMENT_ID"] = "G-4C6XT5QZFH"
        home = self.app.test_client().get("/").get_data(as_text=True)
        self.assertIn("googletagmanager.com/gtag/js?id=G-4C6XT5QZFH", home)
        self.assertIn("location.origin + location.pathname", home)   # query strings are not sent
        self.assertIn("Google Analytics", self.app.test_client().get("/privacy").get_data(as_text=True))
        self.login(self.user_id)
        self.assertIn("googletagmanager", self.client.get("/dashboard").get_data(as_text=True))
        self.create_link(alias="ga-landing", landing={"enabled": True, "heading": "Hi"})
        self.create_link(alias="ga-pw", password="secret1")
        visitor = self.app.test_client()
        for path in ("/ga-landing", "/ga-pw", "/does-not-exist"):
            self.assertNotIn("googletagmanager", visitor.get(path, headers=HUMAN).get_data(as_text=True), path)

    def test_missing_or_malformed_id_adds_nothing(self):
        for value in ("", "<script>alert(1)</script>", "UA-12345-1"):
            self.app.config["GA_MEASUREMENT_ID"] = value
            self.assertNotIn("googletagmanager", self.app.test_client().get("/").get_data(as_text=True))
        self.assertNotIn("Google Analytics", self.app.test_client().get("/privacy").get_data(as_text=True))


class InstallableAppTests(AppTestCase):
    def test_manifest_service_worker_and_icons(self):
        c = self.app.test_client()
        m = c.get("/manifest.webmanifest")
        self.assertEqual(m.mimetype, "application/manifest+json")
        data = m.get_json()
        self.assertEqual((data["name"], data["display"], data["scope"]), ("ShortPulse", "standalone", "/"))
        for icon in data["icons"]:
            self.assertEqual(c.get(icon["src"]).status_code, 200, icon["src"])
        sw = c.get("/sw.js")
        self.assertEqual((sw.mimetype, sw.headers["Service-Worker-Allowed"]), ("application/javascript", "/"))
        self.assertIn("NEVER caches short links", sw.get_data(as_text=True))
        self.assertEqual(c.get("/static/offline.html").status_code, 200)
        self.assertIn('rel="manifest"', c.get("/").get_data(as_text=True))

    def test_asset_links_only_when_configured(self):
        c = self.app.test_client()
        self.assertEqual(c.get("/.well-known/assetlinks.json").status_code, 404)
        self.app.config["TWA_PACKAGE_NAME"] = "xyz.jainovation.shortpulse"
        self.app.config["TWA_SHA256_FINGERPRINTS"] = "aa:bb:cc, DD:EE:FF"
        data = c.get("/.well-known/assetlinks.json").get_json()
        self.assertEqual(data[0]["target"]["package_name"], "xyz.jainovation.shortpulse")
        self.assertEqual(data[0]["target"]["sha256_cert_fingerprints"], ["AA:BB:CC", "DD:EE:FF"])

    def test_short_links_still_redirect_normally(self):
        self.login(self.user_id)
        self.create_link(alias="app-test")
        self.assertEqual(self.app.test_client().get("/app-test", headers=HUMAN).status_code, 302)


class MigrationAndSeedTests(AppTestCase):
    def test_old_database_gets_new_columns(self):
        path = str(Path(self.tmp.name) / "old.db")
        conn = sqlite3.connect(path)
        conn.executescript("""
            CREATE TABLE rules (link_id INTEGER PRIMARY KEY, expires_at INTEGER, max_clicks INTEGER, password_hash TEXT);
            CREATE TABLE click_events (id INTEGER PRIMARY KEY, link_id INTEGER NOT NULL, ts INTEGER NOT NULL,
              visitor_hash TEXT NOT NULL, source TEXT NOT NULL DEFAULT 'link', referrer_domain TEXT NOT NULL DEFAULT '',
              user_agent TEXT NOT NULL DEFAULT '', device TEXT NOT NULL DEFAULT 'Other', browser TEXT NOT NULL DEFAULT 'Other',
              os TEXT NOT NULL DEFAULT 'Other', country TEXT NOT NULL DEFAULT 'Unknown', city TEXT NOT NULL DEFAULT '',
              is_bot INTEGER NOT NULL DEFAULT 0, bot_score REAL, bot_reason TEXT NOT NULL DEFAULT '');""")
        conn.close()
        init_schema(path)
        init_schema(path)  # idempotent
        conn = sqlite3.connect(path)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(click_events)")}
        self.assertTrue({"click_uid", "ip_address", "ip_masked", "app_switch", "engaged_ms", "scroll_pct"} <= cols)
        self.assertIn("open_in_app", {r[1] for r in conn.execute("PRAGMA table_info(rules)")})
        conn.close()

    def test_seed_and_admin_password_from_environment(self):
        from app.seed import ensure_admin, seed_demo
        creds = seed_demo(self.db_path, self.app.extensions["engine"])
        conn = self.db()
        yt = conn.execute("SELECT r.open_in_app FROM links l JOIN rules r ON r.link_id = l.id WHERE l.code = 'yt-talk'").fetchone()
        self.assertEqual(yt[0], 1)
        self.assertGreater(conn.execute("SELECT COUNT(*) FROM click_events WHERE engaged_ms IS NOT NULL").fetchone()[0], 10)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM click_events WHERE ip_masked NOT LIKE '%.x'").fetchone()[0], 0)
        conn.close()
        ensure_admin(self.db_path, creds["admin_email"], "a-new-password-123")
        resp = self.app.test_client().post("/login", data={"email": creds["admin_email"], "password": "a-new-password-123"})
        self.assertEqual(resp.status_code, 302)


@unittest.skipIf(shutil.which("node") is None, "Node.js not installed")
class SmartPasteJSTests(unittest.TestCase):
    def test_url_extraction_and_cleaning(self):
        out = subprocess.run(["node", str(ROOT / "tests" / "smartpaste.test.js")], capture_output=True, text=True, cwd=ROOT / "tests")
        self.assertEqual(out.returncode, 0, out.stderr)


if __name__ == "__main__":
    unittest.main()
