"""Business logic for links, shared by the JSON API, CSV import and seeding."""
from __future__ import annotations

import sqlite3
from urllib.parse import urlsplit

from flask import current_app, request
from werkzeug.security import generate_password_hash

from . import deeplink
from .db import now, transaction
from .validation import check_alias, check_destination, check_options, issue


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def base_url() -> str:
    return current_app.config["BASE_URL"] or request.host_url.rstrip("/")


def self_hosts() -> set[str]:
    hosts = {request.host.split(":")[0].lower()}
    if current_app.config["BASE_URL"]:
        hosts.add((urlsplit(current_app.config["BASE_URL"]).hostname or "").lower())
    return hosts


def engine():
    return current_app.extensions["engine"]


def models():
    return current_app.extensions["models"]


def validate_link(db, destination: str, alias: str | None, taken_in_batch=None) -> dict:
    alias = (alias or "").strip() or None
    dest = check_destination(db, models(), destination, self_hosts(), current_app.config["URL_HIGH_RISK"])
    errors = dest["errors"] + check_alias(engine(), alias, taken_in_batch)
    return {"ok": not errors, "destination": dest["destination"], "host": dest["host"], "alias": alias,
            "errors": errors, "warnings": dest["warnings"], "risk": dest["risk"],
            "app": deeplink.describe(dest["destination"])}


def _set_tags(db, user_id: int, link_id: int, tags: list[str]) -> None:
    db.execute("DELETE FROM link_tags WHERE link_id = ?", (link_id,))
    for name in tags:
        db.execute("INSERT INTO tags (user_id, name) VALUES (?, ?) ON CONFLICT(user_id, name) DO NOTHING",
                   (user_id, name))
        tag_id = db.execute("SELECT id FROM tags WHERE user_id = ? AND name = ?", (user_id, name)).fetchone()[0]
        db.execute("INSERT OR IGNORE INTO link_tags (link_id, tag_id) VALUES (?, ?)", (link_id, tag_id))


# ---------------------------------------------------------------------------
# create
# ---------------------------------------------------------------------------
def create_link(db, user_id: int, payload: dict, taken_in_batch=None) -> dict:
    """Validate everything again and insert. Returns {"ok": bool, ...}."""
    ts = now()
    v = validate_link(db, payload.get("destination", ""), payload.get("alias"), taken_in_batch)
    opts, opt_errors = check_options(db, user_id, payload, ts)
    errors = v["errors"] + opt_errors
    high_risk = v["risk"]["level"] == "high"
    if high_risk and not payload.get("acknowledge_risk"):
        errors.append(issue("destination", "risk_unacknowledged",
                            "Confirm that you trust this destination before publishing."))
    page = None
    if isinstance(payload.get("landing"), dict) and payload["landing"].get("enabled"):
        from .landing import clean_landing
        page, page_errors = clean_landing(db, payload["landing"], self_hosts())
        errors += page_errors
    if errors:
        return {"ok": False, "errors": errors, "warnings": v["warnings"], "risk": v["risk"]}

    alias = v["alias"]
    pw_hash = generate_password_hash(opts["password"]) if opts.get("password") else None
    for attempt in range(3):
        code = alias or engine().generate_code(current_app.config["CODE_LENGTH"])
        try:
            with transaction(db):
                cur = db.execute(
                    """INSERT INTO links (user_id, campaign_id, code, is_custom_alias, destination, title,
                                          risk_score, risk_reasons, flagged, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (user_id, opts.get("campaign_id"), code, int(bool(alias)), v["destination"],
                     opts.get("title", ""), v["risk"]["score"], "; ".join(v["risk"]["reasons"]),
                     int(high_risk), ts, ts))
                link_id = cur.lastrowid
                db.execute("""INSERT INTO rules (link_id, expires_at, max_clicks, password_hash, open_in_app,
                                                 track_engagement) VALUES (?, ?, ?, ?, ?, ?)""",
                           (link_id, opts.get("expires_at"), opts.get("max_clicks"), pw_hash,
                            int(opts.get("open_in_app", True)), int(opts.get("track_engagement", False))))
                _set_tags(db, user_id, link_id, opts.get("tags", []))
                if page:
                    from .landing import save as save_landing
                    save_landing(db, link_id, page)
            break
        except sqlite3.IntegrityError as exc:
            if "links.code" not in str(exc):
                raise
            if alias:  # another process/request took it after our hash-table check
                return {"ok": False, "errors": [issue("alias", "alias_taken", "This alias is already in use.")],
                        "warnings": v["warnings"], "risk": v["risk"]}
    else:
        return {"ok": False, "errors": [issue("alias", "code_exhausted", "Could not allocate a short code.")],
                "warnings": v["warnings"], "risk": v["risk"]}
    engine().alias_add(code)
    return {"ok": True, "link_id": link_id, "code": code, "warnings": v["warnings"], "risk": v["risk"]}


# ---------------------------------------------------------------------------
# read
# ---------------------------------------------------------------------------
LINK_SELECT = """
SELECT l.id, l.user_id, l.campaign_id, l.code, l.is_custom_alias, l.destination, l.title, l.status,
       l.click_count, l.risk_score, l.risk_reasons, l.flagged, l.disabled_reason, l.disabled_at,
       l.created_at, l.updated_at,
       r.expires_at, r.max_clicks, (r.password_hash IS NOT NULL) AS has_password,
       r.open_in_app, r.track_engagement,
       (SELECT enabled FROM landing_pages WHERE link_id = l.id) AS landing_enabled,
       c.name AS campaign_name, u.email AS owner_email,
       COALESCE(s.human, 0) AS human_clicks, COALESCE(s.bots, 0) AS bot_clicks,
       (SELECT group_concat(t.name, ',') FROM link_tags lt JOIN tags t ON t.id = lt.tag_id
         WHERE lt.link_id = l.id) AS tag_list
FROM links l
LEFT JOIN rules r ON r.link_id = l.id
LEFT JOIN campaigns c ON c.id = l.campaign_id
JOIN users u ON u.id = l.user_id
LEFT JOIN (SELECT link_id, SUM(is_bot = 0) AS human, SUM(is_bot = 1) AS bots
           FROM click_events GROUP BY link_id) s ON s.link_id = l.id
"""


def effective_status(row, ts: int) -> str:
    if row["status"] != "active":
        return row["status"]
    if row["expires_at"] and ts >= row["expires_at"]:
        return "expired"
    if row["max_clicks"] and row["click_count"] >= row["max_clicks"]:
        return "limit_reached"
    return "active"


def link_to_dict(row, base: str, ts: int | None = None, include_owner: bool = False) -> dict:
    ts = ts or now()
    d = {
        "id": row["id"], "code": row["code"], "short_url": f"{base}/{row['code']}",
        "destination": row["destination"], "title": row["title"], "status": row["status"],
        "effective_status": effective_status(row, ts), "is_custom_alias": bool(row["is_custom_alias"]),
        "click_count": row["click_count"], "human_clicks": row["human_clicks"], "bot_clicks": row["bot_clicks"],
        "campaign_id": row["campaign_id"], "campaign_name": row["campaign_name"],
        "tags": [t for t in (row["tag_list"] or "").split(",") if t],
        "expires_at": row["expires_at"], "max_clicks": row["max_clicks"], "has_password": bool(row["has_password"]),
        "risk_score": row["risk_score"], "risk_reasons": row["risk_reasons"], "flagged": bool(row["flagged"]),
        "disabled_reason": row["disabled_reason"], "disabled_at": row["disabled_at"],
        "open_in_app": bool(row["open_in_app"]), "track_engagement": bool(row["track_engagement"]),
        "landing_enabled": bool(row["landing_enabled"]),
        "app": deeplink.describe(row["destination"]),
        "created_at": row["created_at"], "updated_at": row["updated_at"],
    }
    if include_owner:
        d["owner_email"] = row["owner_email"]
    return d


def get_link_row(db, link_id: int, user) -> sqlite3.Row | None:
    """Ownership check lives here: users only ever see their own links."""
    if user["role"] == "admin":
        return db.execute(LINK_SELECT + " WHERE l.id = ?", (link_id,)).fetchone()
    return db.execute(LINK_SELECT + " WHERE l.id = ? AND l.user_id = ?", (link_id, user["id"])).fetchone()


def list_links(db, user, *, scope_all=False, q="", status="", campaign_id=None, tag="",
               flagged=False, owner_id=None, limit=500) -> list[sqlite3.Row]:
    where, params = [], []
    if user["role"] == "admin" and owner_id:
        where.append("l.user_id = ?")
        params.append(int(owner_id))
    elif not (scope_all and user["role"] == "admin"):
        where.append("l.user_id = ?")
        params.append(user["id"])
    if q:
        where.append("(l.code LIKE ? OR l.destination LIKE ? OR l.title LIKE ?)")
        params += [f"%{q}%"] * 3
    if status in ("active", "paused", "disabled"):
        where.append("l.status = ?")
        params.append(status)
    if campaign_id == "none":
        where.append("l.campaign_id IS NULL")
    elif campaign_id:
        where.append("l.campaign_id = ?")
        params.append(int(campaign_id))
    if tag:
        where.append("EXISTS (SELECT 1 FROM link_tags lt JOIN tags t ON t.id = lt.tag_id "
                     "WHERE lt.link_id = l.id AND t.name = ?)")
        params.append(tag.lower())
    if flagged:
        where.append("l.flagged = 1")
    sql = LINK_SELECT + (" WHERE " + " AND ".join(where) if where else "") + \
        " ORDER BY l.created_at DESC, l.id DESC LIMIT ?"
    return db.execute(sql, (*params, limit)).fetchall()


# ---------------------------------------------------------------------------
# update
# ---------------------------------------------------------------------------
def update_link(db, row, user, data: dict) -> dict:
    ts = now()
    opts, errors = check_options(db, row["user_id"], data, ts, partial=True)
    warnings, risk = [], None
    link_updates: dict = {}

    if "destination" in data:
        v = validate_link(db, data["destination"], None)
        errors += v["errors"]
        warnings += v["warnings"]
        risk = v["risk"]
        if not v["errors"]:
            if risk["level"] == "high" and not data.get("acknowledge_risk"):
                errors.append(issue("destination", "risk_unacknowledged",
                                    "Confirm that you trust this destination before saving."))
            link_updates.update(destination=v["destination"], risk_score=risk["score"],
                                risk_reasons="; ".join(risk["reasons"]))
            if risk["level"] == "high":
                link_updates["flagged"] = 1  # changing a destination after sharing is a known abuse trick

    if "status" in data:
        new = data["status"]
        if new not in ("active", "paused"):
            errors.append(issue("status", "status_invalid", "Status must be active or paused."))
        elif row["status"] == "disabled":
            errors.append(issue("status", "status_locked",
                                "An admin disabled this link. Contact an admin to restore it."))
        else:
            link_updates["status"] = new

    if errors:
        return {"ok": False, "errors": errors, "warnings": warnings, "risk": risk}

    if "title" in opts:
        link_updates["title"] = opts["title"]
    if "campaign_id" in opts:
        link_updates["campaign_id"] = opts["campaign_id"]
    rule_updates = {k: opts[k] for k in ("expires_at", "max_clicks") if k in opts}
    rule_updates.update({k: int(opts[k]) for k in ("open_in_app", "track_engagement") if k in opts})
    if opts.get("password"):
        rule_updates["password_hash"] = generate_password_hash(opts["password"])
    elif data.get("remove_password"):
        rule_updates["password_hash"] = None

    with transaction(db):
        if link_updates:
            link_updates["updated_at"] = ts
            cols = ", ".join(f"{k} = ?" for k in link_updates)
            db.execute(f"UPDATE links SET {cols} WHERE id = ?", (*link_updates.values(), row["id"]))
        if rule_updates:
            cols = ", ".join(f"{k} = ?" for k in rule_updates)
            db.execute(f"UPDATE rules SET {cols} WHERE link_id = ?", (*rule_updates.values(), row["id"]))
        if "tags" in opts:
            _set_tags(db, row["user_id"], row["id"], opts["tags"])
    return {"ok": True, "warnings": warnings, "risk": risk}
