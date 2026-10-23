"""Demo data so the hosted demo isn't empty (Render's free tier wipes SQLite on
every restart, so SEED_ON_START=1 re-creates this each boot).

The click history generated here is SYNTHETIC. It exists to show the
dashboard; clicks you make during the demo are real and appear on top of it.
Every edge case from the problem statement has a seeded example.
Destinations for the "malicious" examples use the reserved .invalid TLD and
documentation-only IP ranges, so they can never resolve to a real site.
"""
from __future__ import annotations

import json
import logging
import random
import secrets

from werkzeug.security import generate_password_hash

from .db import connect, now, transaction

log = logging.getLogger("shortpulse.seed")

UAS = {
    "Mobile": [
        ("Mozilla/5.0 (Linux; Android 14; SM-A546E) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Mobile Safari/537.36", "Chrome Mobile", "Android"),
        ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.6 Mobile/15E148 Safari/604.1", "Mobile Safari", "iOS"),
        ("Mozilla/5.0 (Linux; Android 13; Redmi Note 12) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Mobile Safari/537.36", "Chrome Mobile", "Android"),
    ],
    "Desktop": [
        ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36", "Chrome", "Windows"),
        ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36 Edg/129.0.0.0", "Edge", "Windows"),
        ("Mozilla/5.0 (Macintosh; Intel Mac OS X 14_6) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.6 Safari/605.1.15", "Safari", "Mac OS X"),
        ("Mozilla/5.0 (X11; Linux x86_64; rv:131.0) Gecko/20100101 Firefox/131.0", "Firefox", "Linux"),
    ],
    "Tablet": [
        ("Mozilla/5.0 (iPad; CPU OS 17_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.6 Mobile/15E148 Safari/604.1", "Mobile Safari", "iOS"),
    ],
}
BOT_UAS = [
    ("WhatsApp/2.24.20.80 A", "declared crawler or link preview"),
    ("facebookexternalhit/1.1 (+http://www.facebook.com/externalhit_uatext.php)", "declared crawler or link preview"),
    ("python-requests/2.32.3", "scripted HTTP client"),
    ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
     "41 clicks from this visitor in 60s"),
]
PLACES = [("India", "Dehradun", 22), ("India", "Haldwani", 14), ("India", "Delhi", 16), ("India", "Bengaluru", 9),
          ("India", "Mumbai", 8), ("India", "Lucknow", 6), ("Nepal", "Kathmandu", 4), ("United States", "", 7),
          ("United Kingdom", "London", 4), ("United Arab Emirates", "Dubai", 4), ("Singapore", "", 3),
          ("Germany", "", 3)]
REFERRERS = [("", 40), ("instagram.com", 16), ("linkedin.com", 10), ("google.com", 10), ("t.co", 7),
             ("facebook.com", 7), ("youtube.com", 6), ("mail.google.com", 4)]


def _pick(rng: random.Random, weighted):
    return rng.choices([w[:-1] if len(w) > 2 else w[0] for w in weighted], [w[-1] for w in weighted])[0]


def _human_event(rng, link_id, ts, visitors, qr_share):
    device = rng.choices(["Mobile", "Desktop", "Tablet"], [62, 33, 5])[0]
    ua, browser, os_name = rng.choice(UAS[device])
    country, city = _pick(rng, PLACES)
    return (link_id, ts, rng.choice(visitors), "qr" if rng.random() < qr_share else "link",
            _pick(rng, REFERRERS), ua, device, browser, os_name, country, city, 0,
            round(rng.uniform(0.01, 0.2), 3), "")


def _bot_event(rng, link_id, ts):
    ua, reason = rng.choice(BOT_UAS)
    declared = reason.startswith("declared")
    return (link_id, ts, secrets.token_hex(16), "link", "", ua, "Bot" if declared else "Desktop",
            "Other" if declared else "Chrome", "Other" if declared else "Windows", "Unknown", "", 1,
            0.99 if declared else round(rng.uniform(0.7, 0.98), 3), reason)


HOUR_WEIGHTS_IST = [1, 1, 1, 1, 1, 2, 3, 4, 5, 6, 6, 6, 6, 6, 6, 7, 8, 9, 10, 11, 10, 8, 5, 2]


def _history(rng, link_id, ts_now, *, days, per_day, bot_share=0.06, qr_share=0.0, start_offset=0):
    """Clicks spread over `days` with an evening peak (Indian time) and a mild upward trend."""
    visitors = [secrets.token_hex(16) for _ in range(max(5, int(per_day * days * 0.6)))]
    end = ts_now - start_offset
    span = days * 86400
    target = int(per_day * days)
    peak = max(HOUR_WEIGHTS_IST)
    events = []
    while len(events) < target:
        t = end - rng.randint(1, span)
        hour_ist = ((t + 19800) // 3600) % 24
        trend = 0.55 + 0.45 * (1 - (end - t) / span)
        if rng.random() > HOUR_WEIGHTS_IST[hour_ist] / peak * trend:
            continue
        events.append(_bot_event(rng, link_id, t) if rng.random() < bot_share
                      else _human_event(rng, link_id, t, visitors, qr_share))
    return events


INSERT_EVENT = """INSERT INTO click_events (link_id, ts, visitor_hash, source, referrer_domain, user_agent,
                  device, browser, os, country, city, is_bot, bot_score, bot_reason,
                  ip_address, ip_masked, app_switch, engaged_ms, scroll_pct)
                  VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"""

# Documentation-only ranges (RFC 5737): never a real visitor's address.
DOC_NETS = ("192.0.2", "198.51.100", "203.0.113")


def _extra(rng, event, ts_now, *, engagement=False, app=False):
    net = rng.choice(DOC_NETS)
    ip = f"{net}.{rng.randint(1, 254)}"
    human = event[11] == 0
    mobile = event[6] == "Mobile"
    engaged = scroll = None
    if engagement and human and rng.random() < 0.7:
        engaged = int(rng.lognormvariate(10.3, 0.9))          # median ~30 s
        scroll = min(100, int(rng.betavariate(2, 1.6) * 100))
    return event + (ip if event[1] > ts_now - 30 * 86400 else None, f"{net}.x",
                    int(app and human and mobile), engaged, scroll)


def ensure_admin(db_path: str, email: str, password: str) -> None:
    if not (email and password):
        return
    conn = connect(db_path)
    try:
        # The environment is the source of truth: an existing account is promoted and gets this password.
        conn.execute("""INSERT INTO users (email, password_hash, role, created_at) VALUES (?, ?, 'admin', ?)
                        ON CONFLICT(email) DO UPDATE SET role = 'admin', password_hash = excluded.password_hash""",
                     (email.lower(), generate_password_hash(password), now()))
    finally:
        conn.close()


def seed_demo(db_path: str, engine, *, admin_email: str = "", admin_password: str = "",
              demo_password: str = "") -> dict | None:
    conn = connect(db_path)
    try:
        if conn.execute("SELECT COUNT(*) FROM links").fetchone()[0]:
            log.info("database already has links; skipping seed")
            return None
        rng = random.Random(2026)
        ts = now()
        creds = {
            "admin_email": (admin_email or "admin@shortpulse.local").lower(),
            "admin_password": admin_password or secrets.token_urlsafe(9),
            "user_email": "demo@shortpulse.local",
            "user_password": demo_password or secrets.token_urlsafe(9),
            "vip_link_password": "pulse2026",
        }
        with transaction(conn):
            for email, pw, role in ((creds["admin_email"], creds["admin_password"], "admin"),
                                    (creds["user_email"], creds["user_password"], "user")):
                conn.execute("""INSERT INTO users (email, password_hash, role, created_at) VALUES (?, ?, ?, ?)
                                ON CONFLICT(email) DO UPDATE SET password_hash = excluded.password_hash,
                                                                 role = excluded.role""",
                             (email, generate_password_hash(pw), role, ts - 40 * 86400))
            admin_id = conn.execute("SELECT id FROM users WHERE email = ?", (creds["admin_email"],)).fetchone()[0]
            user_id = conn.execute("SELECT id FROM users WHERE email = ?", (creds["user_email"],)).fetchone()[0]

            camp = {}
            for name, desc in (("Tech Fest 2026", "Posters, registrations and the schedule"),
                               ("Diwali Sale", "Campus store festive offers"),
                               ("Newsletter", "Monthly learning digest")):
                camp[name] = conn.execute("INSERT INTO campaigns (user_id, name, description, created_at) "
                                          "VALUES (?, ?, ?, ?)", (user_id, name, desc, ts - 35 * 86400)).lastrowid

            conn.execute("INSERT INTO blocklist (domain, reason, added_by, created_at) VALUES (?, ?, ?, ?)",
                         ("sbi-kyc-verify.invalid", "Phishing reported by students", admin_id, ts - 2 * 86400))

            # code, destination, title, campaign, tags, status, expires_at, max_clicks, password, extra
            specs = [
                ("fest26", "https://en.wikipedia.org/wiki/Hackathon", "Tech Fest registration", "Tech Fest 2026",
                 ["event", "students"], "active", ts + 20 * 86400, None, None, {"per_day": 18, "qr": 0.35, "spike": 42}),
                (None, "https://docs.python.org/3/tutorial/", "Python tutorial", "Newsletter",
                 ["learning"], "active", None, None, None, {"per_day": 9}),
                (None, "https://scikit-learn.org/stable/getting_started.html", "scikit-learn: getting started",
                 "Newsletter", ["learning", "ml"], "active", None, None, None, {"per_day": 5}),
                ("diwali-offer", "https://example.com/diwali?utm_source=whatsapp", "Diwali offer (100 coupons)",
                 "Diwali Sale", ["offer"], "active", ts + 10 * 86400, 100, None, {"exact": 92}),
                ("first50", "https://example.org/workshop-seat", "Workshop: first 50 seats", "Tech Fest 2026",
                 ["event"], "active", None, 50, None, {"exact": 50}),
                ("early-bird", "https://example.com/early-bird", "Early-bird passes (expired)", "Tech Fest 2026",
                 ["event"], "active", ts - 86400, None, None, {"per_day": 14, "days": 12, "offset": 86400}),
                ("vip-preview", "https://www.chartjs.org/docs/latest/", "Sponsor preview (password)", "Tech Fest 2026",
                 ["sponsors"], "active", None, None, creds["vip_link_password"], {"per_day": 2}),
                ("free-recharge", "http://sbi-kyc-verify.invalid/login/confirm?id=4471", "Free recharge",
                 None, [], "disabled", None, None, None, {"per_day": 6, "days": 4, "bots": 0.4}),
                (None, "http://203.0.113.45/secure/account/verify.php", "Account verification", None,
                 [], "active", None, None, None, {"per_day": 1, "days": 2, "flag": True}),
                ("store", "https://getbootstrap.com/", "Campus store", "Diwali Sale",
                 ["offer"], "paused", None, None, None, {"per_day": 4}),
                ("fest-info", "https://en.wikipedia.org/wiki/Hackathon", "Tech Fest info page (landing page)",
                 "Tech Fest 2026", ["event"], "active", None, None, None, {"per_day": 9, "landing": True}),
                ("yt-talk", "https://www.youtube.com/@GoogleDevelopers", "Keynote video (opens the YouTube app)",
                 "Tech Fest 2026", ["video"], "active", None, None, None, {"per_day": 7, "app": True}),
            ]
            summary = []
            for code, dest, title, cname, tags, status, exp, cap, pw, extra in specs:
                custom = code is not None
                code = code or engine.generate_code(7)
                flagged = extra.get("flag", False)
                link_id = conn.execute(
                    """INSERT INTO links (user_id, campaign_id, code, is_custom_alias, destination, title, status,
                                          risk_score, risk_reasons, flagged, disabled_reason, disabled_by, disabled_at,
                                          created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (user_id, camp.get(cname), code, int(custom),
                     dest, title, status,
                     0.93 if (flagged or status == "disabled") else 0.04,
                     "raw IP address instead of a domain; words like login/verify/account" if flagged else "",
                     int(flagged), "Phishing: fake KYC page (demo)" if status == "disabled" else None,
                     admin_id if status == "disabled" else None, ts - 86400 if status == "disabled" else None,
                     ts - 30 * 86400, ts - 86400)).lastrowid
                conn.execute("""INSERT INTO rules (link_id, expires_at, max_clicks, password_hash, open_in_app,
                                                   track_engagement) VALUES (?, ?, ?, ?, 1, ?)""",
                             (link_id, exp, cap, generate_password_hash(pw) if pw else None,
                              int(code == "diwali-offer")))
                for t in tags:
                    conn.execute("INSERT INTO tags (user_id, name) VALUES (?, ?) ON CONFLICT DO NOTHING", (user_id, t))
                    tid = conn.execute("SELECT id FROM tags WHERE user_id = ? AND name = ?", (user_id, t)).fetchone()[0]
                    conn.execute("INSERT INTO link_tags (link_id, tag_id) VALUES (?, ?)", (link_id, tid))

                if "exact" in extra:
                    visitors = [secrets.token_hex(16) for _ in range(extra["exact"])]
                    events = [_human_event(rng, link_id, ts - rng.randint(3600, 9 * 86400), visitors, 0.1)
                              for _ in range(extra["exact"])]
                else:
                    events = _history(rng, link_id, ts, days=extra.get("days", 30), per_day=extra["per_day"],
                                      bot_share=extra.get("bots", 0.06), qr_share=extra.get("qr", 0.0),
                                      start_offset=extra.get("offset", 0))
                if extra.get("spike"):  # a burst in the last hour -> spike alert on the dashboard
                    visitors = [secrets.token_hex(16) for _ in range(extra["spike"])]
                    events += [_human_event(rng, link_id, ts - rng.randint(60, 3500), visitors, 0.6)
                               for _ in range(extra["spike"])]
                events = [_extra(rng, e, ts, engagement=code in ("diwali-offer", "fest-info"), app=extra.get("app", False))
                          for e in events]
                if extra.get("landing"):
                    conn.execute("""INSERT INTO landing_pages (link_id, enabled, heading, message, button_label, extra_links,
                                                               theme, updated_at) VALUES (?, 1, ?, ?, ?, ?, 'sunrise', ?)""",
                                 (link_id, "Tech Fest 2026 is here",
                                  "Three days of hackathons, talks and workshops on campus.\n\n"
                                  "Registration closes on Friday. Bring your team of up to five.",
                                  "Read about hackathons", json.dumps([{"label": "Python tutorial", "url": "https://docs.python.org/3/tutorial/"}]), ts))
                conn.executemany(INSERT_EVENT, events)
                if extra.get("landing"):  # demo: most landing-page visitors pressed a button
                    conn.execute("""UPDATE click_events SET landing = 1, outbound = CASE
                                      WHEN abs(random()) % 100 < 60 THEN 'Read about hackathons'
                                      WHEN abs(random()) % 100 < 20 THEN 'Python tutorial' END
                                    WHERE link_id = ? AND is_bot = 0""", (link_id,))
                served = sum(1 for e in events if not e[13].startswith("declared"))
                conn.execute("UPDATE links SET click_count = ? WHERE id = ?", (served, link_id))
                summary.append((code, title))
        for code, _ in summary:
            engine.alias_add(code)
        creds["links"] = summary
        return creds
    finally:
        conn.close()
