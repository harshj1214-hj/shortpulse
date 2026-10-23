from __future__ import annotations

import sqlite3

from flask import Blueprint, abort, current_app, g, jsonify, request

from . import services as S
from .db import get_db, now, transaction
from .security import is_admin, login_required, rate_limited, too_many

bp = Blueprint("links_api", __name__, url_prefix="/api")


def _json() -> dict:
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        abort(400)
    return data


def _link_or_404(link_id: int):
    row = S.get_link_row(get_db(), link_id, g.user)
    if row is None:
        abort(404)
    return row


# ---------------------------------------------------------------- wizard
@bp.post("/links/validate")
@login_required
def validate():
    """Step 1 of the wizard calls this while the user types (debounced)."""
    data = _json()
    result = S.validate_link(get_db(), data.get("destination", ""), data.get("alias"))
    result.pop("host", None)
    return jsonify(result)


@bp.post("/links")
@login_required
def create():
    wait = rate_limited(f"create:{g.user['id']}", current_app.config["RATE_CREATE"])
    if wait:
        return too_many(wait, "You're creating links too quickly.")
    db = get_db()
    result = S.create_link(db, g.user["id"], _json())
    if not result["ok"]:
        return jsonify(result), 422
    row = S.get_link_row(db, result["link_id"], g.user)
    return jsonify(ok=True, link=S.link_to_dict(row, S.base_url()), warnings=result["warnings"],
                   risk=result["risk"]), 201


# ---------------------------------------------------------------- links
@bp.get("/links")
@login_required
def list_links():
    a = request.args
    rows = S.list_links(get_db(), g.user, scope_all=a.get("scope") == "all", q=a.get("q", "").strip(),
                        status=a.get("status", ""), campaign_id=a.get("campaign_id") or None,
                        tag=a.get("tag", ""), flagged=a.get("flagged") == "1",
                        owner_id=a.get("owner_id", type=int))
    base, ts = S.base_url(), now()
    admin = g.user["role"] == "admin"
    return jsonify(links=[S.link_to_dict(r, base, ts, include_owner=admin) for r in rows])


@bp.get("/links/<int:link_id>")
@login_required
def get_link(link_id: int):
    return jsonify(link=S.link_to_dict(_link_or_404(link_id), S.base_url(),
                                       include_owner=g.user["role"] == "admin"))


@bp.patch("/links/<int:link_id>")
@login_required
def update_link(link_id: int):
    db = get_db()
    row = _link_or_404(link_id)
    result = S.update_link(db, row, g.user, _json())
    if not result["ok"]:
        return jsonify(result), 422
    return jsonify(ok=True, link=S.link_to_dict(S.get_link_row(db, link_id, g.user), S.base_url()),
                   warnings=result["warnings"])


@bp.get("/links/<int:link_id>/clicks")
@login_required
def recent_clicks(link_id: int):
    _link_or_404(link_id)
    limit = min(max(request.args.get("limit", 50, type=int), 1), 200)
    rows = get_db().execute(
        """SELECT ts, source, referrer_domain, device, browser, os, country, city, is_bot, bot_score, bot_reason,
                  ip_masked, ip_address, app_switch, engaged_ms, scroll_pct, outbound
           FROM click_events WHERE link_id = ? ORDER BY ts DESC, id DESC LIMIT ?""", (link_id, limit)).fetchall()
    full_ip = is_admin() or current_app.config["OWNER_SEES_FULL_IP"]
    clicks = []
    for r in rows:
        d = dict(r)
        full = d.pop("ip_address")
        d["ip"] = (full or d["ip_masked"]) if full_ip else d["ip_masked"]
        clicks.append(d)
    return jsonify(clicks=clicks, full_ip=full_ip)


# ---------------------------------------------------------------- campaigns
@bp.get("/campaigns")
@login_required
def campaigns():
    since = now() - 30 * 86400
    rows = get_db().execute(
        """SELECT c.id, c.name, c.description, c.created_at,
                  COUNT(DISTINCT l.id) AS links,
                  COALESCE(SUM(e.is_bot = 0), 0) AS clicks_30d
           FROM campaigns c
           LEFT JOIN links l ON l.campaign_id = c.id
           LEFT JOIN click_events e ON e.link_id = l.id AND e.ts >= ?
           WHERE c.user_id = ?
           GROUP BY c.id ORDER BY c.created_at DESC""", (since, g.user["id"])).fetchall()
    top = {}
    for r in get_db().execute(
            """SELECT l.campaign_id, l.code, l.title, COUNT(e.id) AS clicks
               FROM links l JOIN click_events e ON e.link_id = l.id AND e.is_bot = 0 AND e.ts >= ?
               WHERE l.user_id = ? AND l.campaign_id IS NOT NULL
               GROUP BY l.id ORDER BY clicks DESC""", (since, g.user["id"])):
        top.setdefault(r["campaign_id"], {"code": r["code"], "title": r["title"], "clicks": r["clicks"]})
    return jsonify(campaigns=[dict(r) | {"top_link": top.get(r["id"])} for r in rows])


@bp.post("/campaigns")
@login_required
def create_campaign():
    data = _json()
    name = str(data.get("name", "")).strip()
    if not 1 <= len(name) <= 60:
        return jsonify(error="name", message="Campaign names must be 1–60 characters."), 422
    try:
        cur = get_db().execute("INSERT INTO campaigns (user_id, name, description, created_at) VALUES (?, ?, ?, ?)",
                               (g.user["id"], name, str(data.get("description", ""))[:300], now()))
    except sqlite3.IntegrityError:
        return jsonify(error="duplicate", message="You already have a campaign with this name."), 422
    return jsonify(ok=True, id=cur.lastrowid), 201


@bp.delete("/campaigns/<int:campaign_id>")
@login_required
def delete_campaign(campaign_id: int):
    db = get_db()
    with transaction(db):  # links stay; their campaign_id becomes NULL (ON DELETE SET NULL)
        n = db.execute("DELETE FROM campaigns WHERE id = ? AND user_id = ?", (campaign_id, g.user["id"])).rowcount
    if not n:
        abort(404)
    return jsonify(ok=True)


@bp.get("/tags")
@login_required
def tags():
    rows = get_db().execute(
        """SELECT t.name, COUNT(lt.link_id) AS links FROM tags t
           LEFT JOIN link_tags lt ON lt.tag_id = t.id WHERE t.user_id = ?
           GROUP BY t.id HAVING links > 0 ORDER BY t.name""", (g.user["id"],)).fetchall()
    return jsonify(tags=[dict(r) for r in rows])
