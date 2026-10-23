"""GET/POST /<code> — the hot path.

1. Load the link + rules (one indexed query).
2. The C engine decides, in order: status -> expiry -> click limit -> password.
3. Capped links: reserve a click with ONE conditional UPDATE. SQLite serialises
   writers, so with N simultaneous visitors exactly `max_clicks` UPDATEs match.
   The C check alone can't guarantee this: two requests can both read
   click_count = N-1 before either writes.
4. Put the click on the queue and redirect. Enrichment, bot scoring and the
   insert happen in the worker thread.

Redirects are 302 with Cache-Control: no-store. A 301 would be cached by
browsers, so later clicks would never reach us (lost analytics) and an admin
takedown would not stop people who had already visited.
"""
from __future__ import annotations

import secrets
import sqlite3
import time
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from flask import Blueprint, current_app, make_response, redirect, render_template, request
from werkzeug.routing import BaseConverter
from werkzeug.security import check_password_hash

from . import deeplink
from . import landing as landing_pages
from . import engine as E
from .db import get_db, now
from .features import is_declared_bot
from .security import client_ip, rate_limited

bp = Blueprint("redirect", __name__)


class CodeConverter(BaseConverter):
    regex = r"[A-Za-z0-9_-]{1,64}"


LINK_SQL = """
SELECT l.id, l.code, l.destination, l.status, l.click_count, l.title,
       r.expires_at, r.max_clicks, r.password_hash, r.open_in_app, r.track_engagement,
       lp.enabled AS landing_enabled
FROM links l LEFT JOIN rules r ON r.link_id = l.id
LEFT JOIN landing_pages lp ON lp.link_id = l.id
WHERE l.code = ?
"""
RESERVE_SQL = """
UPDATE links SET click_count = click_count + 1
WHERE id = ? AND status = 'active'
  AND click_count < (SELECT max_clicks FROM rules WHERE link_id = ?)
"""

UNAVAILABLE = {
    E.DENY_DISABLED: ("disabled", 410),
    E.DENY_PAUSED: ("paused", 403),
    E.DENY_EXPIRED: ("expired", 410),
    E.DENY_LIMIT: ("limit_reached", 410),
}


def _no_store(resp, status: int | None = None):
    resp = make_response(resp)
    if status:
        resp.status_code = status
    resp.headers["Cache-Control"] = "no-store, max-age=0"
    resp.headers["X-Robots-Tag"] = "noindex"
    return resp


def add_query_param(url: str, key: str, value: str) -> str:
    p = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True) if k != key] + [(key, value)]
    return urlunsplit((p.scheme, p.netloc, p.path, urlencode(query), p.fragment))


def _unavailable(reason: str, status: int, code: str):
    return _no_store(render_template("redirect/unavailable.html", reason=reason, code=code), status)


@bp.route("/<code:code>", methods=["GET", "POST"])
def follow(code: str):
    db = get_db()
    link = db.execute(LINK_SQL, (code,)).fetchone()
    if link is None:
        return _unavailable("not_found", 404, code)

    has_password = link["password_hash"] is not None
    pw_state, error = E.PW_NOT_SUPPLIED, None
    if request.method == "POST" and has_password:
        wait = rate_limited(f"pw:{client_ip()}:{link['id']}", current_app.config["RATE_PASSWORD"])
        if wait:
            return _no_store(render_template("redirect/password.html", code=code,
                                             error=f"Too many attempts. Try again in {wait} seconds."), 429)
        ok = check_password_hash(link["password_hash"], request.form.get("password", ""))
        pw_state = E.PW_CORRECT if ok else E.PW_WRONG

    engine = current_app.extensions["engine"]
    decision = engine.check_rules(status=link["status"], expires_at=link["expires_at"], now=now(),
                                  clicks=link["click_count"], max_clicks=link["max_clicks"],
                                  has_password=has_password, password_state=pw_state)

    if decision in UNAVAILABLE:
        reason, status = UNAVAILABLE[decision]
        return _unavailable(reason, status, code)
    if decision in (E.NEED_PASSWORD, E.BAD_PASSWORD):
        if decision == E.BAD_PASSWORD:
            error = "That password isn't right."
        return _no_store(render_template("redirect/password.html", code=code, error=error),
                         401 if decision == E.BAD_PASSWORD else 200)

    # ---- decision == ALLOW -------------------------------------------------
    ua = request.headers.get("User-Agent", "")
    declared_bot = is_declared_bot(ua) or request.method == "HEAD"
    capped = link["max_clicks"] is not None

    if capped:
        if declared_bot:
            # Previews and crawlers must not use up a limited link, and must not
            # get the destination for free either (or spoofing a bot UA would
            # bypass the limit).
            return _no_store(render_template("redirect/preview.html", code=code))
        try:
            reserved = db.execute(RESERVE_SQL, (link["id"], link["id"])).rowcount == 1
        except sqlite3.OperationalError:  # database is locked beyond busy_timeout
            return _no_store("Busy, please retry.", 503)
        if not reserved:
            return _unavailable("limit_reached", 410, code)

    target, click_uid = link["destination"], None
    landing = bool(link["landing_enabled"])
    if landing:
        click_uid = secrets.token_urlsafe(12)  # the landing page reports with this id
    elif link["track_engagement"]:
        click_uid = secrets.token_urlsafe(12)  # 16 characters, unguessable
        target = add_query_param(target, "lp_cid", click_uid)
    app_target = app_url = None
    if not landing and link["open_in_app"] and not declared_bot and deeplink.is_mobile(ua):
        app_target = deeplink.detect(target)
        app_url = deeplink.app_url_for(app_target, ua) if app_target else None

    current_app.extensions["clicklog"].enqueue({
        "link_id": link["id"],
        "ts": time.time(),
        "ip": client_ip(),
        "ua": ua[:512],
        "accept_language": bool(request.headers.get("Accept-Language")),
        "referrer": request.referrer or "",
        "source": "qr" if request.args.get("src") == "qr" else "link",
        "is_head": request.method == "HEAD",
        "declared_bot": declared_bot,
        "count_in_worker": not capped,  # capped links were already counted above
        "click_uid": click_uid,
        "app_switch": bool(app_url),
        "landing": landing,
    })
    if landing:
        page = landing_pages.load(get_db(), link["id"])
        return _no_store(render_template(
            "redirect/landing.html", code=code, page=page, destination=target, cid=click_uid,
            heading=page["heading"] or link["title"] or "You're about to continue",
            paragraphs=[p.strip() for p in page["message"].split("\n\n") if p.strip()]))
    if app_url:
        return _no_store(render_template("redirect/app_switch.html", code=code, label=app_target.label,
                                         app_url=app_url, web_url=target))
    return _no_store(redirect(target, code=303 if request.method == "POST" else 302))
