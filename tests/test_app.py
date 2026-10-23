"""End-to-end behaviour of the Flask app against the problem statement's edge cases."""
import time
import unittest

from helpers import HUMAN, AppTestCase


def codes(resp):
    return {i["code"] for i in resp.get_json()["errors"]}


def warn_codes(resp):
    return {i["code"] for i in resp.get_json()["warnings"]}


class WizardValidationTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.login(self.user_id)

    def validate(self, destination, alias=None):
        return self.client.post("/api/links/validate", json={"destination": destination, "alias": alias})

    def test_bad_urls_are_errors(self):
        cases = {
            "": "url_required",
            "javascript:alert(1)": "url_scheme",
            "ftp://example.com/file": "url_scheme",
            "http://192.168.1.10/admin": "url_private_ip",
            "http://localhost:5000/": "url_private_host",
            "https://user@example.com/": "url_credentials",
            "https://exa mple.com": "url_spaces",
            "https://nodot/": "url_bad_host",
            "http://2130706433/": "url_bad_host",
        }
        for url, code in cases.items():
            with self.subTest(url=url):
                self.assertIn(code, codes(self.validate(url)))

    def test_missing_scheme_is_fixed_with_warning(self):
        data = self.validate("example.com/page").get_json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["destination"], "https://example.com/page")
        self.assertIn("url_scheme_added", {w["code"] for w in data["warnings"]})

    def test_redirect_loop_and_blocklist(self):
        self.app.config["BASE_URL"] = "https://lp.example"
        self.assertIn("url_self", codes(self.validate("https://lp.example/abc")))
        conn = self.db()
        conn.execute("INSERT INTO blocklist (domain, reason, created_at) VALUES ('evil.example', 'test', 0)")
        conn.close()
        self.assertIn("url_blocklisted", codes(self.validate("https://login.evil.example/x")))

    def test_shortener_and_risk_warnings(self):
        self.assertIn("url_shortener", warn_codes(self.validate("https://bit.ly/abc")))
        resp = self.validate("http://paytm-secure-update.xyz/login/verify")
        self.assertIn("url_risk_high", warn_codes(resp))
        self.assertEqual(resp.get_json()["risk"]["level"], "high")

    def test_alias_problems_before_publishing(self):
        self.assertIn("alias_reserved", codes(self.validate("https://example.com", "admin")))
        self.assertIn("alias_reserved", codes(self.validate("https://example.com", "Dashboard")))
        self.assertIn("alias_bad_chars", codes(self.validate("https://example.com", "my link")))
        self.assertIn("alias_too_short", codes(self.validate("https://example.com", "ab")))
        self.assertEqual(self.create_link(alias="taken1").status_code, 201)
        self.assertIn("alias_taken", codes(self.validate("https://example.com", "taken1")))

    def test_options_validated_on_publish(self):
        resp = self.create_link(expires_at=int(time.time()) - 10, max_clicks=0, password="ab")
        self.assertEqual(resp.status_code, 422)
        self.assertTrue({"expires_in_past", "max_clicks_invalid", "password_length"} <= codes(resp))

    def test_high_risk_needs_acknowledgement_and_is_flagged(self):
        url = "http://paytm-secure-update.xyz/login/verify"
        self.assertIn("risk_unacknowledged", codes(self.create_link(destination=url)))
        resp = self.create_link(destination=url, acknowledge_risk=True)
        self.assertEqual(resp.status_code, 201)
        self.assertTrue(resp.get_json()["link"]["flagged"])

    def test_create_with_campaign_and_tags(self):
        cid = self.client.post("/api/campaigns", json={"name": "Fest"}).get_json()["id"]
        link = self.create_link(alias="fest-reg", campaign_id=cid, tags="Event; students, event").get_json()["link"]
        self.assertEqual(link["campaign_name"], "Fest")
        self.assertEqual(sorted(link["tags"]), ["event", "students"])
        self.assertTrue(link["short_url"].endswith("/fest-reg"))


class RedirectTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.login(self.user_id)
        self.visitor = self.app.test_client()

    def test_redirect_logs_click_without_waiting(self):
        link = self.create_link(alias="hello").get_json()["link"]
        resp = self.visitor.get("/hello", headers=HUMAN)
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.headers["Location"], "https://example.com/page")
        self.assertIn("no-store", resp.headers["Cache-Control"])
        self.flush()
        row = self.one("SELECT is_bot, device, browser FROM click_events WHERE link_id = ?", (link["id"],))
        self.assertEqual(row["is_bot"], 0)
        self.assertEqual(row["browser"], "Chrome")
        self.assertEqual(self.one("SELECT click_count FROM links WHERE id = ?", (link["id"],))[0], 1)

    def test_unknown_code_and_case_sensitivity(self):
        self.create_link(alias="CaseSensitive")
        self.assertEqual(self.visitor.get("/casesensitive").status_code, 404)
        self.assertEqual(self.visitor.get("/CaseSensitive", headers=HUMAN).status_code, 302)

    def test_qr_scans_are_tagged(self):
        link = self.create_link(alias="qr-link").get_json()["link"]
        self.visitor.get("/qr-link?src=qr", headers=HUMAN)
        self.flush()
        self.assertEqual(self.one("SELECT source FROM click_events WHERE link_id = ?", (link["id"],))[0], "qr")

    def test_expired_link_stops_but_keeps_stats(self):
        link = self.create_link(alias="soon", expires_at=int(time.time()) + 3600).get_json()["link"]
        self.visitor.get("/soon", headers=HUMAN)
        self.flush()
        conn = self.db()
        conn.execute("UPDATE rules SET expires_at = ? WHERE link_id = ?", (int(time.time()) - 1, link["id"]))
        conn.close()
        resp = self.visitor.get("/soon", headers=HUMAN)
        self.assertEqual(resp.status_code, 410)
        self.assertIn(b"expired", resp.data)
        report = self.client.get(f"/api/analytics/link/{link['id']}?period=24h").get_json()
        self.assertEqual(report["clicks"]["human"], 1)
        self.assertEqual(self.client.get(f"/api/links/{link['id']}").get_json()["link"]["effective_status"], "expired")

    def test_password_link(self):
        self.create_link(alias="secret", password="opensesame")
        self.assertEqual(self.visitor.get("/secret").status_code, 200)
        wrong = self.visitor.post("/secret", data={"password": "nope"}, headers=HUMAN)
        self.assertEqual(wrong.status_code, 401)
        self.assertNotIn("Location", wrong.headers)
        right = self.visitor.post("/secret", data={"password": "opensesame"}, headers=HUMAN)
        self.assertEqual(right.status_code, 303)
        self.assertEqual(right.headers["Location"], "https://example.com/page")

    def test_password_guessing_is_rate_limited(self):
        self.create_link(alias="guessme", password="opensesame")
        statuses = [self.visitor.post("/guessme", data={"password": f"x{i}"}).status_code for i in range(7)]
        self.assertEqual(statuses[:5], [401] * 5)
        self.assertEqual(statuses[5], 429)

    def test_dead_link_never_asks_for_password(self):
        link = self.create_link(alias="deadpw", password="opensesame", max_clicks=1).get_json()["link"]
        self.visitor.post("/deadpw", data={"password": "opensesame"}, headers=HUMAN)
        self.assertEqual(self.visitor.get("/deadpw").status_code, 410)
        self.assertEqual(self.one("SELECT click_count FROM links WHERE id = ?", (link["id"],))[0], 1)

    def test_click_limit_closes_at_exactly_n(self):
        self.create_link(alias="three", max_clicks=3)
        statuses = [self.visitor.get("/three", headers=HUMAN).status_code for _ in range(5)]
        self.assertEqual(statuses, [302, 302, 302, 410, 410])

    def test_declared_bot_on_uncapped_link(self):
        link = self.create_link(alias="unfurl").get_json()["link"]
        resp = self.visitor.get("/unfurl", headers={"User-Agent": "WhatsApp/2.24.20.80 A"})
        self.assertEqual(resp.status_code, 302)
        self.flush()
        row = self.one("SELECT is_bot, bot_reason FROM click_events WHERE link_id = ?", (link["id"],))
        self.assertEqual(row["is_bot"], 1)
        self.assertEqual(self.one("SELECT click_count FROM links WHERE id = ?", (link["id"],))[0], 0)

    def test_declared_bot_cannot_use_up_or_bypass_a_capped_link(self):
        link = self.create_link(alias="capped", max_clicks=1).get_json()["link"]
        for ua in ("Slackbot-LinkExpanding 1.0", "facebookexternalhit/1.1"):
            resp = self.visitor.get("/capped", headers={"User-Agent": ua})
            self.assertEqual(resp.status_code, 200)
            self.assertNotIn("Location", resp.headers)
        self.assertEqual(self.visitor.head("/capped").status_code, 200)
        self.assertEqual(self.visitor.get("/capped", headers=HUMAN).status_code, 302)
        self.assertEqual(self.one("SELECT click_count FROM links WHERE id = ?", (link["id"],))[0], 1)

    def test_scripted_burst_is_filtered_from_human_counts(self):
        link = self.create_link(alias="burst").get_json()["link"]
        for _ in range(30):
            self.visitor.get("/burst", headers={"User-Agent": "python-requests/2.32.3"})
        self.flush()
        human, bots = self.one("SELECT SUM(is_bot = 0), SUM(is_bot = 1) FROM click_events WHERE link_id = ?",
                               (link["id"],))
        self.assertGreaterEqual(bots, 28)
        report = self.client.get(f"/api/analytics/link/{link['id']}?period=24h").get_json()
        self.assertEqual(report["clicks"]["bots"], bots)
        self.assertEqual(report["clicks"]["human"], human)


class GovernanceTests(AppTestCase):
    def test_admin_takedown_is_immediate_and_keeps_stats(self):
        self.login(self.user_id)
        link = self.create_link(alias="victim").get_json()["link"]
        visitor = self.app.test_client()
        self.assertEqual(visitor.get("/victim", headers=HUMAN).status_code, 302)
        self.flush()

        admin = self.login(self.admin_id, self.app.test_client())
        self.assertEqual(admin.post(f"/api/admin/links/{link['id']}/disable", json={"reason": "Phishing"}).status_code, 200)
        resp = visitor.get("/victim", headers=HUMAN)
        self.assertEqual(resp.status_code, 410)
        self.assertIn(b"disabled", resp.data)

        # the owner still sees the history but cannot switch the link back on
        report = self.client.get(f"/api/analytics/link/{link['id']}?period=24h").get_json()
        self.assertEqual(report["clicks"]["human"], 1)
        self.assertIn("disabled", {a["kind"] for a in report["alerts"]})
        resp = self.client.patch(f"/api/links/{link['id']}", json={"status": "active"})
        self.assertIn("status_locked", codes(resp))

    def test_blocklist_can_disable_existing_links(self):
        self.login(self.user_id)
        link = self.create_link(destination="https://shop.badsite.example/deal").get_json()["link"]
        admin = self.login(self.admin_id, self.app.test_client())
        resp = admin.post("/api/admin/blocklist", json={"domain": "badsite.example", "disable_existing": True})
        self.assertEqual(resp.get_json()["links_disabled"], 1)
        self.assertEqual(self.app.test_client().get(f"/{link['code']}").status_code, 410)
        self.assertIn("url_blocklisted", codes(self.client.post(
            "/api/links/validate", json={"destination": "https://badsite.example"})))

    def test_owner_can_pause_and_resume(self):
        self.login(self.user_id)
        link = self.create_link(alias="pausable").get_json()["link"]
        self.client.patch(f"/api/links/{link['id']}", json={"status": "paused"})
        self.assertEqual(self.app.test_client().get("/pausable").status_code, 403)
        self.client.patch(f"/api/links/{link['id']}", json={"status": "active"})
        self.assertEqual(self.app.test_client().get("/pausable", headers=HUMAN).status_code, 302)

    def test_users_only_see_their_own_links(self):
        self.login(self.user_id)
        link = self.create_link(alias="mine").get_json()["link"]
        other = self.login(self.other_id, self.app.test_client())
        self.assertEqual(other.get(f"/api/links/{link['id']}").status_code, 404)
        self.assertEqual(other.get(f"/api/analytics/link/{link['id']}").status_code, 404)
        self.assertEqual(other.patch(f"/api/links/{link['id']}", json={"title": "x"}).status_code, 404)
        self.assertEqual(other.get("/api/links").get_json()["links"], [])
        self.assertEqual(other.get("/api/links?scope=all").get_json()["links"], [])  # scope=all is admin-only
        self.assertEqual(other.post(f"/api/admin/links/{link['id']}/disable").status_code, 403)
        admin = self.login(self.admin_id, self.app.test_client())
        self.assertEqual(len(admin.get("/api/links?scope=all").get_json()["links"]), 1)
        self.assertEqual(admin.get(f"/api/links/{link['id']}").status_code, 200)

    def test_anonymous_visitors_have_no_management_access(self):
        anon = self.app.test_client()
        self.assertEqual(anon.get("/api/links").status_code, 401)
        self.assertEqual(anon.get("/dashboard").status_code, 302)
        self.assertEqual(anon.get("/admin").status_code, 302)

    def test_link_creation_is_rate_limited(self):
        self.login(self.user_id)
        statuses = [self.create_link().status_code for _ in range(12)]
        self.assertEqual(statuses[:10], [201] * 10)
        self.assertEqual(statuses[10], 429)
        resp = self.create_link()
        self.assertIn("Retry-After", resp.headers)

    def test_cannot_remove_last_admin(self):
        admin = self.login(self.admin_id)
        resp = admin.post(f"/api/admin/users/{self.admin_id}/role", json={"role": "user"})
        self.assertEqual(resp.status_code, 422)


class AnalyticsTests(AppTestCase):
    def test_dashboard_aggregates_by_period_and_campaign(self):
        self.login(self.user_id)
        cid = self.client.post("/api/campaigns", json={"name": "Launch"}).get_json()["id"]
        self.create_link(alias="in-camp", campaign_id=cid)
        self.create_link(alias="no-camp")
        v = self.app.test_client()
        for _ in range(3):
            v.get("/in-camp", headers=HUMAN)
        v.get("/no-camp", headers=HUMAN)
        self.flush()
        r = self.client.get("/api/analytics/overview?period=7d&tz=330").get_json()
        self.assertEqual(r["clicks"]["human"], 4)
        self.assertEqual(r["clicks"]["unique_visitors"], 1)  # same IP + browser
        self.assertEqual(len(r["series"]), 8)  # 7 days + today, empty days filled
        self.assertEqual(sum(p["human"] for p in r["series"]), 4)
        camps = {c["name"]: c["clicks"] for c in r["campaigns"]}
        self.assertEqual(camps, {"Launch": 3, "No campaign": 1})
        self.assertEqual(r["top_links"][0]["code"], "in-camp")
        only = self.client.get(f"/api/analytics/overview?period=24h&campaign_id={cid}").get_json()
        self.assertEqual(only["clicks"]["human"], 3)
        self.assertEqual(len(only["series"]), 25)

    def test_spike_alert(self):
        from app.spikes import robust_z
        z, med = robust_z(40, [2, 3, 1, 2, 4, 3, 2, 2] * 3)
        self.assertGreater(z, 4)
        z, _ = robust_z(5, [2, 3, 1, 2, 4, 3, 2, 2] * 3)
        self.assertLess(z, 4)

    def test_seeded_demo_has_every_edge_case(self):
        from app.seed import seed_demo
        creds = seed_demo(self.db_path, self.app.extensions["engine"])
        demo_id = self.one("SELECT id FROM users WHERE email = ?", (creds["user_email"],))[0]
        self.login(demo_id)
        links = {l["code"]: l for l in self.client.get("/api/links").get_json()["links"]}
        self.assertEqual(links["early-bird"]["effective_status"], "expired")
        self.assertEqual(links["first50"]["effective_status"], "limit_reached")
        self.assertEqual(links["free-recharge"]["effective_status"], "disabled")
        self.assertTrue(links["vip-preview"]["has_password"])
        r = self.client.get("/api/analytics/overview?period=30d").get_json()
        kinds = {a["kind"] for a in r["alerts"]}
        self.assertTrue({"spike", "limit", "disabled", "flagged"} <= kinds, kinds)
        self.assertGreater(r["clicks"]["bots"], 0)


class ImportTests(AppTestCase):
    CSV = (
        "destination,alias,title,campaign,tags,expires_at,max_clicks\n"
        "https://example.com/a,promo-a,Row A,Spring,offer;students,2030-01-31,\n"
        "https://example.com/b,promo-a,Duplicate alias,Spring,,,\n"
        "htp:/broken,,Bad URL,,,,\n"
        "https://example.com/c,admin,Reserved alias,,,,\n"
        "https://example.com/a,,Same destination as A,,,,10\n"
        "https://example.com/d,,Bad date,,,31/12/2030,\n"
    )

    def setUp(self):
        super().setUp()
        self.login(self.user_id)

    def preview(self):
        import io
        return self.client.post("/api/import/preview", data={"file": (io.BytesIO(self.CSV.encode("utf-8-sig")),
                                                                      "links.csv"), "tz": "330"},
                                content_type="multipart/form-data").get_json()

    def test_preview_finds_errors_and_duplicates(self):
        data = self.preview()
        by_row = {r["row"]: r for r in data["rows"]}
        self.assertEqual(by_row[2]["status"], "ok")
        self.assertIn("alias_duplicate_in_file", {e["code"] for e in by_row[3]["errors"]})
        self.assertEqual(by_row[4]["status"], "error")
        self.assertIn("alias_reserved", {e["code"] for e in by_row[5]["errors"]})
        self.assertIn("duplicate_destination", {w["code"] for w in by_row[6]["warnings"]})
        self.assertIn("expires_invalid", {e["code"] for e in by_row[7]["errors"]})
        self.assertEqual(data["summary"], {"ok": 1, "warning": 1, "error": 4, "total": 6})

    def test_fix_then_commit_imports_valid_rows_only(self):
        rows = self.preview()["rows"]
        rows[1]["alias"] = "promo-b"      # user fixes the duplicate alias in the table
        rows[5]["expires_at"] = "2030-12-31"
        fixed = self.client.post("/api/import/validate", json={"rows": rows, "tz": 330}).get_json()
        self.assertEqual(fixed["summary"]["error"], 2)
        result = self.client.post("/api/import/commit", json={"rows": rows, "tz": 330}).get_json()
        self.assertEqual(result["imported"], 4)
        self.assertEqual(result["failed"], 2)
        links = {l["code"]: l for l in self.client.get("/api/links").get_json()["links"]}
        self.assertEqual(links["promo-a"]["campaign_name"], "Spring")
        self.assertEqual(sorted(links["promo-a"]["tags"]), ["offer", "students"])
        self.assertIsNotNone(links["promo-a"]["expires_at"])


class QRAndCSRFTests(AppTestCase):
    csrf_disabled = False

    def test_csrf_required_for_writes(self):
        self.login(self.user_id)
        self.assertEqual(self.client.post("/api/links", json={"destination": "https://example.com"}).status_code, 403)
        self.client.get("/dashboard")  # renders the token into the session
        with self.client.session_transaction() as s:
            token = s["csrf"]
        resp = self.client.post("/api/links", json={"destination": "https://example.com"},
                                headers={"X-CSRF-Token": token})
        self.assertEqual(resp.status_code, 201)


if __name__ == "__main__":
    unittest.main()
