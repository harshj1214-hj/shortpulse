"""Admin user management: view, edit, suspend, password, delete, campaigns, audit log."""
import unittest

from helpers import HUMAN, AppTestCase


class AdminUserManagementTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.login(self.user_id)
        cid = self.client.post("/api/campaigns", json={"name": "Spring"}).get_json()["id"]
        self.link = self.create_link(alias="userlink", campaign_id=cid).get_json()["link"]
        self.campaign_id = cid
        self.app.test_client().get("/userlink", headers=HUMAN)
        self.flush()
        self.admin = self.login(self.admin_id, self.app.test_client())

    def actions(self):
        return [a["action"] for a in self.admin.get("/api/admin/audit").get_json()["actions"]]

    def test_admin_sees_everything_about_a_user(self):
        users = {u["email"]: u for u in self.admin.get("/api/admin/users").get_json()["users"]}
        self.assertEqual((users["user@example.com"]["links"], users["user@example.com"]["human_clicks"]), (1, 1))
        d = self.admin.get(f"/api/admin/users/{self.user_id}").get_json()
        self.assertEqual(d["user"]["email"], "user@example.com")
        self.assertEqual(d["links"][0]["code"], "userlink")
        self.assertEqual(d["campaigns"][0]["name"], "Spring")
        self.assertEqual(d["visits"][0]["ip"], "127.0.0.1")            # admins see full IPs
        self.assertEqual(d["clicks"]["human"], 1)
        self.assertNotIn("password_hash", d["user"])
        self.assertEqual(self.admin.get(f"/admin/users/{self.user_id}").status_code, 200)
        owned = self.admin.get(f"/api/links?owner_id={self.user_id}").get_json()["links"]
        self.assertEqual([l["code"] for l in owned], ["userlink"])
        # ordinary users get nothing
        self.assertEqual(self.client.get(f"/api/admin/users/{self.admin_id}").status_code, 403)
        self.assertEqual(self.client.get("/api/admin/audit").status_code, 403)

    def test_edit_email_role_and_password(self):
        r = self.admin.patch(f"/api/admin/users/{self.user_id}", json={"email": "Renamed@Example.com", "role": "admin"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.one("SELECT email, role FROM users WHERE id = ?", (self.user_id,))[:], ("renamed@example.com", "admin"))
        self.assertEqual(self.admin.patch(f"/api/admin/users/{self.user_id}", json={"email": "other@example.com"}).status_code, 422)
        self.assertEqual(self.admin.post(f"/api/admin/users/{self.user_id}/password", json={"password": "short"}).status_code, 422)
        self.assertEqual(self.admin.post(f"/api/admin/users/{self.user_id}/password", json={"password": "brand-new-pass"}).status_code, 200)
        login = self.app.test_client().post("/login", data={"email": "renamed@example.com", "password": "brand-new-pass"})
        self.assertEqual(login.status_code, 302)
        self.assertIsNotNone(self.one("SELECT last_login_at FROM users WHERE id = ?", (self.user_id,))[0])
        log = self.admin.get("/api/admin/audit").get_json()["actions"]
        self.assertIn("password_set", [a["action"] for a in log])
        self.assertTrue(all("brand-new-pass" not in a["detail"] for a in log))   # passwords never logged

    def test_suspend_signs_out_and_blocks_sign_in(self):
        self.assertEqual(self.client.get("/api/links").status_code, 200)
        r = self.admin.patch(f"/api/admin/users/{self.user_id}", json={"status": "suspended", "disable_links": True})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.client.get("/api/links").status_code, 401)        # existing session ended
        login = self.app.test_client().post("/login", data={"email": "user@example.com", "password": "password123"})
        self.assertEqual(login.status_code, 403)
        self.assertEqual(self.app.test_client().get("/userlink", headers=HUMAN).status_code, 410)
        self.admin.patch(f"/api/admin/users/{self.user_id}", json={"status": "active"})
        login = self.app.test_client().post("/login", data={"email": "user@example.com", "password": "password123"})
        self.assertEqual(login.status_code, 302)

    def test_delete_keeps_links_traceable_and_frees_email(self):
        r = self.admin.delete(f"/api/admin/users/{self.user_id}")
        self.assertEqual(r.get_json()["links_disabled"], 1)
        self.assertEqual(self.app.test_client().get("/userlink", headers=HUMAN).status_code, 410)
        self.assertEqual(self.one("SELECT COUNT(*) FROM click_events WHERE link_id = ?", (self.link["id"],))[0], 1)
        self.assertNotIn("user@example.com", [u["email"] for u in self.admin.get("/api/admin/users").get_json()["users"]])
        self.assertEqual(len(self.admin.get("/api/admin/users?deleted=1").get_json()["users"]), 3)
        reg = self.app.test_client().post("/register", data={"email": "user@example.com", "password": "password123"})
        self.assertEqual(reg.status_code, 302)
        self.assertIn("user_deleted", self.actions())

    def test_admins_cannot_lock_themselves_or_the_platform_out(self):
        me = f"/api/admin/users/{self.admin_id}"
        self.assertEqual(self.admin.patch(me, json={"role": "user"}).status_code, 422)
        self.assertEqual(self.admin.patch(me, json={"status": "suspended"}).status_code, 422)
        self.assertEqual(self.admin.delete(me).status_code, 422)
        d = self.admin.get(me).get_json()
        self.assertTrue(d["is_self"])

    def test_admin_manages_any_campaign_and_actions_are_logged(self):
        self.assertEqual(self.admin.patch(f"/api/admin/campaigns/{self.campaign_id}", json={"name": "Spring 2026"}).status_code, 200)
        self.assertEqual(self.one("SELECT name FROM campaigns WHERE id = ?", (self.campaign_id,))[0], "Spring 2026")
        self.assertEqual(self.admin.delete(f"/api/admin/campaigns/{self.campaign_id}").status_code, 200)
        self.assertIsNone(self.one("SELECT campaign_id FROM links WHERE id = ?", (self.link["id"],))[0])
        self.admin.post(f"/api/admin/links/{self.link['id']}/disable", json={"reason": "Spam"})
        self.admin.post("/api/admin/blocklist", json={"domain": "bad.example"})
        actions = self.actions()
        for expected in ("campaign_updated", "campaign_deleted", "link_disabled", "domain_blocked"):
            self.assertIn(expected, actions)


if __name__ == "__main__":
    unittest.main()
