"""Engagement beacon (time on page, scroll depth) and click export.

Why not an iframe wrapper: most large sites refuse to be framed, and browsers
never let a page read scroll position inside another site's frame. So depth
and time can only be measured by the destination itself. Link owners who turn
on "measure engagement" add /static/lp-beacon.js to their own site; ShortPulse
appends ?lp_cid=<random id> to the destination, and the script reports back
with navigator.sendBeacon when the visitor leaves.
"""
from __future__ import annotations

import csv
import io
import json
import re
from datetime import datetime, timezone

from flask import Blueprint, Response, abort, current_app, g, request

from . import services as S
from .db import get_db, now
from .security import client_ip, is_admin, login_required, rate_limited

bp = Blueprint("engage", __name__)
CID_RE = re.compile(r"^[A-Za-z0-9_-]{16}$")
MAX_MS = 4 * 3600 * 1000


def _cors(resp: Response) -> Response:
    # No cookies are read here, so allowing any origin is safe.
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return resp


@bp.route("/api/engage", methods=["POST", "OPTIONS"])
def beacon():
    if request.method == "OPTIONS":
        return _cors(Response(status=204))
    if rate_limited(f"beacon:{client_ip()}", current_app.config["RATE_BEACON"]):
        return _cors(Response(status=429))
    try:
        data = json.loads(request.get_data(as_text=True)[:2048])  # sendBeacon posts text/plain
        cid, ms, scroll = str(data["cid"]), int(data["ms"]), int(data["scroll"])
        out = data.get("out")
        out = None if out in (None, "") else re.sub(r"[\x00-\x1f\x7f]", "", str(out))[:40]
    except (ValueError, KeyError, TypeError, AttributeError):
        return _cors(Response(status=400))
    if not CID_RE.match(cid) or not 0 <= ms <= MAX_MS or not 0 <= scroll <= 100:
        return _cors(Response(status=400))
    # Keep the best values seen: the script reports every 10 s while visible and when the tab is hidden.
    # engaged_at powers "reading now"; outbound records which landing-page button was chosen.
    get_db().execute("""UPDATE click_events
                        SET engaged_ms = MAX(COALESCE(engaged_ms, 0), ?), scroll_pct = MAX(COALESCE(scroll_pct, 0), ?),
                            engaged_at = ?, outbound = COALESCE(?, outbound)
                        WHERE click_uid = ? AND ts >= ?""", (ms, scroll, now(), out, cid, now() - 86400))
    return _cors(Response(status=204))


def _cell(v):
    # Stop spreadsheet apps from executing a cell as a formula (CSV injection).
    if isinstance(v, str) and v[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + v
    return v


@bp.get("/api/links/<int:link_id>/clicks.csv")
@login_required
def export_clicks(link_id: int):
    db = get_db()
    row = S.get_link_row(db, link_id, g.user)
    if row is None:
        abort(404)
    full_ip = is_admin() or current_app.config["OWNER_SEES_FULL_IP"]
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["time_utc", "source", "referrer", "device", "browser", "os", "country", "city",
                "ip", "user_agent", "is_bot", "bot_reason", "opened_in_app", "engaged_seconds", "scroll_percent"])
    for r in db.execute("""SELECT ts, source, referrer_domain, device, browser, os, country, city, ip_masked,
                                  ip_address, user_agent, is_bot, bot_reason, app_switch, engaged_ms, scroll_pct
                           FROM click_events WHERE link_id = ? ORDER BY ts""", (link_id,)):
        ip = (r["ip_address"] or r["ip_masked"]) if full_ip else r["ip_masked"]
        w.writerow([_cell(v) for v in (
            datetime.fromtimestamp(r["ts"], timezone.utc).strftime("%Y-%m-%d %H:%M:%S"), r["source"],
            r["referrer_domain"], r["device"], r["browser"], r["os"], r["country"], r["city"], ip,
            r["user_agent"], r["is_bot"], r["bot_reason"], r["app_switch"],
            "" if r["engaged_ms"] is None else round(r["engaged_ms"] / 1000, 1),
            "" if r["scroll_pct"] is None else r["scroll_pct"])])
    resp = Response(out.getvalue(), mimetype="text/csv")
    resp.headers["Content-Disposition"] = f'attachment; filename="shortpulse-{row["code"]}-clicks.csv"'
    return resp
