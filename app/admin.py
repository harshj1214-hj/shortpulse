"""Admin-only API. A takedown is a single UPDATE; the redirect route reads the
status on every request (no cache), so the very next visit is blocked."""
from __future__ import annotations

import re
import secrets
import sqlite3

from flask import Blueprint, abort, g, jsonify, request
from werkzeug.security import generate_password_hash

from . import services as S
from .db import get_db, now, transaction
from .security import admin_required
from .validation import normalize_url

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def audit(db, action: str, target_type: str, target_id, detail: str = "") -> None:
    """Record an admin action. Never pass passwords or tokens in `detail`."""
    db.execute("INSERT INTO admin_actions (admin_id, action, target_type, target_id, detail, created_at) "
               "VALUES (?, ?, ?, ?, ?, ?)", (g.user["id"], action, target_type, target_id, detail[:300], now()))

bp = Blueprint("admin", __name__, url_prefix="/api/admin")


def _json() -> dict:
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


def _disable(db, link_ids: list[int], reason: str) -> int:
    if not link_ids:
        return 0
    marks = ",".join("?" * len(link_ids))
    return db.execute(f"""UPDATE links SET status = 'disabled', flagged = 0, disabled_reason = ?,
                          disabled_by = ?, disabled_at = ?, updated_at = ?
                          WHERE id IN ({marks}) AND status != 'disabled'""",
                      (reason, g.user["id"], now(), now(), *link_ids)).rowcount


@bp.post("/links/<int:link_id>/disable")
@admin_required
def disable_link(link_id: int):
    reason = str(_json().get("reason", "")).strip()[:200] or "Violates platform policy"
    db = get_db()
    if not db.execute("SELECT 1 FROM links WHERE id = ?", (link_id,)).fetchone():
        abort(404)
    code = db.execute("SELECT code FROM links WHERE id = ?", (link_id,)).fetchone()[0]
    with transaction(db):
        _disable(db, [link_id], reason)
        audit(db, "link_disabled", "link", link_id, f"/{code}: {reason}")
    return jsonify(ok=True)


@bp.post("/links/<int:link_id>/enable")
@admin_required
def enable_link(link_id: int):
    db = get_db()
    with transaction(db):
        n = db.execute("""UPDATE links SET status = 'active', flagged = 0, disabled_reason = NULL,
                          disabled_by = NULL, disabled_at = NULL, updated_at = ? WHERE id = ?""",
                       (now(), link_id)).rowcount
        if n:
            audit(db, "link_restored", "link", link_id, "/" + db.execute("SELECT code FROM links WHERE id = ?", (link_id,)).fetchone()[0])
    if not n:
        abort(404)
    return jsonify(ok=True)


@bp.post("/links/<int:link_id>/dismiss-flag")
@admin_required
def dismiss_flag(link_id: int):
    db = get_db()
    n = db.execute("UPDATE links SET flagged = 0, updated_at = ? WHERE id = ?", (now(), link_id)).rowcount
    if not n:
        abort(404)
    audit(db, "flag_dismissed", "link", link_id, "Marked safe")
    return jsonify(ok=True)


# ------------------------------------------------------------------ blocklist
@bp.get("/blocklist")
@admin_required
def blocklist():
    rows = get_db().execute("""SELECT b.id, b.domain, b.reason, b.created_at, u.email AS added_by
                               FROM blocklist b LEFT JOIN users u ON u.id = b.added_by
                               ORDER BY b.created_at DESC""").fetchall()
    return jsonify(domains=[dict(r) for r in rows])


@bp.post("/blocklist")
@admin_required
def add_blocked_domain():
    data = _json()
    raw = str(data.get("domain", "")).strip().lower()
    _, host, errors, _ = normalize_url(raw if "://" in raw else "https://" + raw)
    if errors or not host:
        return jsonify(error="domain", message="Enter a domain like example.com."), 422
    host = host[4:] if host.startswith("www.") else host
    db = get_db()
    disabled = 0
    try:
        with transaction(db):
            db.execute("INSERT INTO blocklist (domain, reason, added_by, created_at) VALUES (?, ?, ?, ?)",
                       (host, str(data.get("reason", ""))[:200], g.user["id"], now()))
            if data.get("disable_existing"):
                ids = []
                for r in db.execute("SELECT id, destination FROM links WHERE status != 'disabled'"):
                    _, h, _, _ = normalize_url(r["destination"])
                    if h and (h == host or h.endswith("." + host)):
                        ids.append(r["id"])
                disabled = _disable(db, ids, f"Domain {host} is blocklisted")
            audit(db, "domain_blocked", "blocklist", None, f"{host} ({disabled} links disabled)")
    except sqlite3.IntegrityError:
        return jsonify(error="duplicate", message=f"{host} is already blocklisted."), 422
    return jsonify(ok=True, domain=host, links_disabled=disabled), 201


@bp.delete("/blocklist/<int:entry_id>")
@admin_required
def remove_blocked_domain(entry_id: int):
    db = get_db()
    row = db.execute("SELECT domain FROM blocklist WHERE id = ?", (entry_id,)).fetchone()
    if not row:
        abort(404)
    db.execute("DELETE FROM blocklist WHERE id = ?", (entry_id,))
    audit(db, "domain_unblocked", "blocklist", entry_id, row["domain"])
    return jsonify(ok=True)


# ---------------------------------------------------------------------- users
@bp.get("/users")
@admin_required
def users():
    show_deleted = request.args.get("deleted") == "1"
    rows = get_db().execute("""
        SELECT u.id, u.email, u.role, u.status, u.created_at, u.last_login_at,
               (SELECT COUNT(*) FROM links l WHERE l.user_id = u.id) AS links,
               (SELECT COUNT(*) FROM click_events e JOIN links l ON l.id = e.link_id
                 WHERE l.user_id = u.id AND e.is_bot = 0) AS human_clicks
        FROM users u WHERE ? OR u.status != 'deleted'
        ORDER BY u.created_at""", (int(show_deleted),)).fetchall()
    return jsonify(users=[dict(r) for r in rows])


def _user_or_404(db, user_id: int):
    u = db.execute("SELECT id, email, role, status, created_at, last_login_at FROM users WHERE id = ?",
                   (user_id,)).fetchone()
    if u is None:
        abort(404)
    return u


def _other_active_admins(db, user_id: int) -> int:
    return db.execute("SELECT COUNT(*) FROM users WHERE role = 'admin' AND status = 'active' AND id != ?",
                      (user_id,)).fetchone()[0]


def _err(message: str, code: str = "invalid", status: int = 422):
    return jsonify(error=code, message=message), status


@bp.get("/users/<int:user_id>")
@admin_required
def user_detail(user_id: int):
    """Everything about one account: profile, stats, links, campaigns, recent visits, admin history."""
    db = get_db()
    u = _user_or_404(db, user_id)
    ts = now()
    link_stats = db.execute("""SELECT COUNT(*) AS total, COALESCE(SUM(status = 'active'), 0) AS active,
                                      COALESCE(SUM(status = 'paused'), 0) AS paused,
                                      COALESCE(SUM(status = 'disabled'), 0) AS disabled
                               FROM links WHERE user_id = ?""", (user_id,)).fetchone()
    clicks = db.execute("""SELECT COALESCE(SUM(e.is_bot = 0), 0) AS human, COALESCE(SUM(e.is_bot = 1), 0) AS bots,
                                  COALESCE(SUM(e.is_bot = 0 AND e.ts >= ?), 0) AS human_30d,
                                  COUNT(DISTINCT CASE WHEN e.is_bot = 0 THEN e.visitor_hash END) AS unique_visitors
                           FROM click_events e JOIN links l ON l.id = e.link_id WHERE l.user_id = ?""",
                        (ts - 30 * 86400, user_id)).fetchone()
    campaigns = db.execute("""SELECT c.id, c.name, c.description, c.created_at, COUNT(l.id) AS links
                              FROM campaigns c LEFT JOIN links l ON l.campaign_id = c.id
                              WHERE c.user_id = ? GROUP BY c.id ORDER BY c.created_at DESC""", (user_id,)).fetchall()
    links = S.list_links(db, g.user, owner_id=user_id)
    visits = db.execute("""SELECT e.ts, l.id AS link_id, l.code, e.device, e.browser, e.os, e.country, e.city,
                                  COALESCE(e.ip_address, e.ip_masked) AS ip, e.referrer_domain, e.source,
                                  e.is_bot, e.bot_reason
                           FROM click_events e JOIN links l ON l.id = e.link_id
                           WHERE l.user_id = ? ORDER BY e.ts DESC, e.id DESC LIMIT 25""", (user_id,)).fetchall()
    history = db.execute("""SELECT a.action, a.detail, a.created_at, u.email AS admin_email
                            FROM admin_actions a LEFT JOIN users u ON u.id = a.admin_id
                            WHERE a.target_type = 'user' AND a.target_id = ?
                            ORDER BY a.id DESC LIMIT 30""", (user_id,)).fetchall()
    base = S.base_url()
    return jsonify(user=dict(u), is_self=user_id == g.user["id"], links_summary=dict(link_stats), clicks=dict(clicks),
                   campaigns=[dict(r) for r in campaigns],
                   links=[S.link_to_dict(r, base, ts, include_owner=True) for r in links],
                   visits=[dict(r) for r in visits], history=[dict(r) for r in history])


@bp.patch("/users/<int:user_id>")
@admin_required
def update_user(user_id: int):
    """Change email, role and/or status (active | suspended)."""
    db = get_db()
    u = _user_or_404(db, user_id)
    if u["status"] == "deleted":
        return _err("This account was deleted.")
    data = request.get_json(silent=True) or {}
    changes, notes = {}, []
    if "email" in data:
        email = str(data["email"]).strip().lower()
        if not EMAIL_RE.match(email):
            return _err("Enter a valid email address.")
        if email != u["email"]:
            changes["email"] = email
            notes.append(f"email {u['email']} -> {email}")
    if "role" in data:
        if data["role"] not in ("user", "admin"):
            return _err("Role must be user or admin.")
        if data["role"] != u["role"]:
            if user_id == g.user["id"]:
                return _err("You can't change your own role.")
            if data["role"] == "user" and _other_active_admins(db, user_id) == 0:
                return _err("The platform needs at least one admin.", "last_admin")
            changes["role"] = data["role"]
            notes.append(f"role {u['role']} -> {data['role']}")
    if "status" in data:
        if data["status"] not in ("active", "suspended"):
            return _err("Status must be active or suspended.")
        if data["status"] != u["status"]:
            if user_id == g.user["id"]:
                return _err("You can't suspend your own account.")
            if data["status"] == "suspended" and u["role"] == "admin" and _other_active_admins(db, user_id) == 0:
                return _err("The platform needs at least one active admin.", "last_admin")
            changes["status"] = data["status"]
            notes.append(f"status {u['status']} -> {data['status']}")
    if not changes:
        return jsonify(ok=True, changed=False)
    try:
        with transaction(db):
            cols = ", ".join(f"{k} = ?" for k in changes)  # keys come from the fixed set above
            db.execute(f"UPDATE users SET {cols} WHERE id = ?", (*changes.values(), user_id))
            disabled = 0
            if changes.get("status") == "suspended" and data.get("disable_links"):
                ids = [r[0] for r in db.execute("SELECT id FROM links WHERE user_id = ?", (user_id,))]
                disabled = _disable(db, ids, "Owner account suspended")
                notes.append(f"{disabled} links disabled")
            audit(db, "user_updated", "user", user_id, "; ".join(notes))
    except sqlite3.IntegrityError:
        return _err("Another account already uses this email.", "duplicate")
    return jsonify(ok=True, changed=True)


@bp.post("/users/<int:user_id>/password")
@admin_required
def set_password(user_id: int):
    db = get_db()
    u = _user_or_404(db, user_id)
    if u["status"] == "deleted":
        return _err("This account was deleted.")
    password = str((request.get_json(silent=True) or {}).get("password", ""))
    if len(password) < 8:
        return _err("Use at least 8 characters.")
    with transaction(db):
        db.execute("UPDATE users SET password_hash = ? WHERE id = ?", (generate_password_hash(password), user_id))
        audit(db, "password_set", "user", user_id, "New password set by admin")
    return jsonify(ok=True)


@bp.post("/users/<int:user_id>/disable-links")
@admin_required
def disable_user_links(user_id: int):
    db = get_db()
    _user_or_404(db, user_id)
    reason = str((request.get_json(silent=True) or {}).get("reason", "")).strip()[:200] or "Disabled by an admin"
    with transaction(db):
        ids = [r[0] for r in db.execute("SELECT id FROM links WHERE user_id = ?", (user_id,))]
        n = _disable(db, ids, reason)
        audit(db, "links_disabled", "user", user_id, f"{n} links: {reason}")
    return jsonify(ok=True, links_disabled=n)


@bp.delete("/users/<int:user_id>")
@admin_required
def delete_user(user_id: int):
    """Soft delete: the account can't sign in and its email is freed, but its links stay
    (disabled) so their analytics remain traceable."""
    db = get_db()
    u = _user_or_404(db, user_id)
    if user_id == g.user["id"]:
        return _err("You can't delete your own account.")
    if u["status"] == "deleted":
        return _err("This account is already deleted.")
    if u["role"] == "admin" and _other_active_admins(db, user_id) == 0:
        return _err("The platform needs at least one admin.", "last_admin")
    with transaction(db):
        db.execute("UPDATE users SET status = 'deleted', role = 'user', email = ?, password_hash = ? WHERE id = ?",
                   (f"deleted-{user_id}-{now()}@deleted.invalid", generate_password_hash(secrets.token_urlsafe(32)), user_id))
        ids = [r[0] for r in db.execute("SELECT id FROM links WHERE user_id = ?", (user_id,))]
        n = _disable(db, ids, "Owner account was deleted")
        audit(db, "user_deleted", "user", user_id, f"{u['email']} deleted; {n} links disabled")
    return jsonify(ok=True, links_disabled=n)


@bp.patch("/campaigns/<int:campaign_id>")
@admin_required
def update_campaign(campaign_id: int):
    db = get_db()
    c = db.execute("SELECT id, user_id, name FROM campaigns WHERE id = ?", (campaign_id,)).fetchone()
    if not c:
        abort(404)
    data = request.get_json(silent=True) or {}
    name = str(data.get("name", c["name"])).strip()
    if not 1 <= len(name) <= 60:
        return _err("Campaign names must be 1-60 characters.")
    try:
        with transaction(db):
            db.execute("UPDATE campaigns SET name = ?, description = COALESCE(?, description) WHERE id = ?",
                       (name, str(data["description"])[:300] if "description" in data else None, campaign_id))
            audit(db, "campaign_updated", "user", c["user_id"], f"campaign '{c['name']}' -> '{name}'")
    except sqlite3.IntegrityError:
        return _err("This user already has a campaign with that name.", "duplicate")
    return jsonify(ok=True)


@bp.delete("/campaigns/<int:campaign_id>")
@admin_required
def delete_campaign(campaign_id: int):
    db = get_db()
    c = db.execute("SELECT id, user_id, name FROM campaigns WHERE id = ?", (campaign_id,)).fetchone()
    if not c:
        abort(404)
    with transaction(db):  # links stay; their campaign becomes "No campaign"
        db.execute("DELETE FROM campaigns WHERE id = ?", (campaign_id,))
        audit(db, "campaign_deleted", "user", c["user_id"], f"campaign '{c['name']}'")
    return jsonify(ok=True)


@bp.get("/audit")
@admin_required
def audit_log():
    limit = min(max(request.args.get("limit", 100, type=int), 1), 500)
    rows = get_db().execute("""
        SELECT a.id, a.action, a.target_type, a.target_id, a.detail, a.created_at, u.email AS admin_email,
               CASE WHEN a.target_type = 'user' THEN (SELECT email FROM users WHERE id = a.target_id) END AS target_email
        FROM admin_actions a LEFT JOIN users u ON u.id = a.admin_id
        ORDER BY a.id DESC LIMIT ?""", (limit,)).fetchall()
    return jsonify(actions=[dict(r) for r in rows])


@bp.post("/users/<int:user_id>/role")
@admin_required
def set_role(user_id: int):
    role = _json().get("role")
    if role not in ("user", "admin"):
        return jsonify(error="role", message="Role must be user or admin."), 422
    db = get_db()
    with transaction(db):
        if role == "user":
            admins = db.execute("SELECT COUNT(*) FROM users WHERE role = 'admin' AND id != ?", (user_id,)).fetchone()[0]
            if admins == 0:
                return jsonify(error="last_admin", message="The platform needs at least one admin."), 422
        n = db.execute("UPDATE users SET role = ? WHERE id = ?", (role, user_id)).rowcount
        if n:
            audit(db, "user_updated", "user", user_id, f"role -> {role}")
    if not n:
        abort(404)
    return jsonify(ok=True)
