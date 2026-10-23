"""Landing pages ("link-in-bio" style): an optional page ShortPulse shows before
the destination. Because ShortPulse hosts it, time on page, scroll depth and
button clicks can be measured for every visit, unlike third-party websites,
which no shortener can see into."""
from __future__ import annotations

import json
import re

from flask import Blueprint, abort, g, jsonify, request

from . import services as S
from .db import get_db, now
from .security import login_required
from .validation import blocklist_match, normalize_url

bp = Blueprint("landing", __name__, url_prefix="/api/links")

THEMES = ("ink", "sunrise", "mint", "violet")
DEFAULTS = {"enabled": False, "heading": "", "message": "", "button_label": "Continue", "theme": "ink", "links": []}
CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _text(v, limit: int) -> str:
    return CTRL_RE.sub("", str(v or "")).strip()[:limit]


def clean_landing(db, data: dict, self_hosts: set[str]) -> tuple[dict, list[dict]]:
    errors: list[dict] = []
    out = dict(DEFAULTS)
    out["enabled"] = bool(data.get("enabled"))
    for key, limit in (("heading", 80), ("message", 1000), ("button_label", 40)):
        if key in data:
            if len(str(data[key] or "")) > limit:
                errors.append({"field": key, "code": f"{key}_too_long", "message": f"Keep the {key.replace('_', ' ')} under {limit} characters."})
            out[key] = _text(data[key], limit)
    out["button_label"] = out["button_label"] or "Continue"
    theme = data.get("theme", "ink")
    out["theme"] = theme if theme in THEMES else "ink"
    links = data.get("links") or []
    if not isinstance(links, list) or len(links) > 5:
        errors.append({"field": "links", "code": "links_invalid", "message": "Add at most 5 extra buttons."})
        links = []
    for i, item in enumerate(links, start=1):
        if not isinstance(item, dict):
            continue
        label, raw = _text(item.get("label"), 40), str(item.get("url") or "").strip()
        if not label and not raw:
            continue
        if not label:
            errors.append({"field": "links", "code": "link_label", "message": f"Button {i} needs a label."})
            continue
        url, host, url_errors, _ = normalize_url(raw)
        if url_errors:
            errors.append({"field": "links", "code": "link_url", "message": f"Button {i}: {url_errors[0]['message']}"})
        elif host in self_hosts:
            errors.append({"field": "links", "code": "link_self", "message": f"Button {i} points back to ShortPulse."})
        elif blocklist_match(db, host):
            errors.append({"field": "links", "code": "link_blocked", "message": f"Button {i} goes to a blocklisted domain."})
        else:
            out["links"].append({"label": label, "url": url})
    return out, errors


def load(db, link_id: int) -> dict:
    r = db.execute("SELECT * FROM landing_pages WHERE link_id = ?", (link_id,)).fetchone()
    if not r:
        return dict(DEFAULTS)
    return {"enabled": bool(r["enabled"]), "heading": r["heading"], "message": r["message"],
            "button_label": r["button_label"], "theme": r["theme"], "links": json.loads(r["extra_links"] or "[]")}


def save(db, link_id: int, page: dict) -> None:
    db.execute("""INSERT INTO landing_pages (link_id, enabled, heading, message, button_label, extra_links, theme, updated_at)
                  VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                  ON CONFLICT(link_id) DO UPDATE SET enabled = excluded.enabled, heading = excluded.heading,
                    message = excluded.message, button_label = excluded.button_label,
                    extra_links = excluded.extra_links, theme = excluded.theme, updated_at = excluded.updated_at""",
               (link_id, int(page["enabled"]), page["heading"], page["message"], page["button_label"],
                json.dumps(page["links"]), page["theme"], now()))


@bp.get("/<int:link_id>/landing")
@login_required
def get_page(link_id: int):
    if S.get_link_row(get_db(), link_id, g.user) is None:
        abort(404)
    return jsonify(page=load(get_db(), link_id), themes=THEMES)


@bp.put("/<int:link_id>/landing")
@login_required
def put_page(link_id: int):
    db = get_db()
    if S.get_link_row(db, link_id, g.user) is None:
        abort(404)
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        abort(400)
    page, errors = clean_landing(db, data, S.self_hosts())
    if errors:
        return jsonify(ok=False, errors=errors), 422
    save(db, link_id, page)
    return jsonify(ok=True, page=page)
