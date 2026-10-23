"""Dashboard and per-link analytics, computed live from click_events.

"Human clicks" excludes anything the bot filter flagged; bot clicks are still
stored and reported separately, so the filter's effect is visible and
reversible. Disabled and expired links keep all their history.
"""
from __future__ import annotations

import statistics

from flask import Blueprint, abort, current_app, g, jsonify, request

from . import services as S
from .db import get_db, now
from .security import login_required
from .spikes import compute_alerts

bp = Blueprint("analytics", __name__, url_prefix="/api/analytics")

PERIODS = {  # span seconds, bucket seconds
    "24h": (86400, 3600),
    "7d": (7 * 86400, 86400),
    "30d": (30 * 86400, 86400),
    "90d": (90 * 86400, 86400),
}
DIMENSIONS = {"device": "device", "browser": "browser", "os": "os", "country": "country",
              "referrer": "referrer_domain", "source": "source"}


def _scope(link_id: int | None = None) -> tuple[str, list]:
    a = request.args
    where, params = [], []
    if link_id is not None:
        where.append("l.id = ?")
        params.append(link_id)
    # Admins see platform-wide data with ?scope=all, and any single link they open.
    if not (g.user["role"] == "admin" and (a.get("scope") == "all" or link_id is not None)):
        where.append("l.user_id = ?")
        params.append(g.user["id"])
    cid = a.get("campaign_id")
    if cid == "none":
        where.append("l.campaign_id IS NULL")
    elif cid:
        try:
            params.append(int(cid))
        except ValueError:
            abort(400)
        where.append("l.campaign_id = ?")
    return " AND ".join(where) or "1=1", params


def _tz_offset_seconds() -> int:
    """Browser sends minutes east of UTC (IST = 330) so day buckets match local midnight."""
    minutes = request.args.get("tz", 0, type=int)
    return max(-840, min(840, minutes)) * 60


def build_report(db, where: str, params: list, *, include_alerts: bool = True) -> dict:
    period = request.args.get("period", "7d")
    if period not in PERIODS:
        period = "7d"
    span, bucket = PERIODS[period]
    off = _tz_offset_seconds()
    ts = now()
    since = ts - span
    ev = f"FROM click_events e JOIN links l ON l.id = e.link_id WHERE {where}"

    link_counts = db.execute(f"""
        SELECT COUNT(*) AS total,
               SUM(l.status = 'active' AND (r.expires_at IS NULL OR r.expires_at > ?)
                   AND (r.max_clicks IS NULL OR l.click_count < r.max_clicks)) AS active,
               SUM(l.status = 'paused') AS paused,
               SUM(l.status = 'disabled') AS disabled,
               SUM(l.status = 'active' AND r.expires_at IS NOT NULL AND r.expires_at <= ?) AS expired,
               SUM(l.status = 'active' AND (r.expires_at IS NULL OR r.expires_at > ?)
                   AND r.max_clicks IS NOT NULL AND l.click_count >= r.max_clicks) AS limit_reached
        FROM links l LEFT JOIN rules r ON r.link_id = l.id WHERE {where}""",
        (ts, ts, ts, *params)).fetchone()

    def click_totals(start: int, end: int):
        return db.execute(f"""
            SELECT COALESCE(SUM(e.is_bot = 0), 0) AS human,
                   COALESCE(SUM(e.is_bot = 1), 0) AS bots,
                   COUNT(DISTINCT CASE WHEN e.is_bot = 0 THEN e.visitor_hash END) AS unique_visitors,
                   COALESCE(SUM(e.is_bot = 0 AND e.source = 'qr'), 0) AS qr_scans,
                   COALESCE(SUM(e.is_bot = 0 AND e.app_switch = 1), 0) AS app_opens
            {ev} AND e.ts >= ? AND e.ts < ?""", (*params, start, end)).fetchone()

    cur = click_totals(since, ts + 1)
    prev = click_totals(since - span, since)

    # time series with empty buckets filled in
    raw = {r["b"]: r for r in db.execute(f"""
        SELECT ((e.ts + ?) / ?) * ? - ? AS b, SUM(e.is_bot = 0) AS human, SUM(e.is_bot = 1) AS bots
        {ev} AND e.ts >= ? GROUP BY b ORDER BY b""", (off, bucket, bucket, off, *params, since))}
    first = ((since + off) // bucket) * bucket - off
    last = ((ts + off) // bucket) * bucket - off
    series = [{"t": b, "human": raw[b]["human"] if b in raw else 0, "bots": raw[b]["bots"] if b in raw else 0}
              for b in range(first, last + 1, bucket)]

    campaigns = [dict(r) for r in db.execute(f"""
        SELECT l.campaign_id AS id, COALESCE(c.name, 'No campaign') AS name,
               COUNT(DISTINCT l.id) AS links,
               COALESCE(SUM(e.is_bot = 0), 0) AS clicks,
               COUNT(DISTINCT CASE WHEN e.is_bot = 0 THEN e.visitor_hash END) AS unique_visitors
        FROM links l
        LEFT JOIN campaigns c ON c.id = l.campaign_id
        LEFT JOIN click_events e ON e.link_id = l.id AND e.ts >= ?
        WHERE {where} GROUP BY l.campaign_id ORDER BY clicks DESC""", (since, *params))]

    top_links = [dict(r) for r in db.execute(f"""
        SELECT l.id, l.code, l.title, l.destination, l.status,
               SUM(e.is_bot = 0) AS clicks,
               COUNT(DISTINCT CASE WHEN e.is_bot = 0 THEN e.visitor_hash END) AS unique_visitors
        {ev} AND e.ts >= ? GROUP BY l.id HAVING clicks > 0 ORDER BY clicks DESC LIMIT 5""",
        (*params, since))]

    breakdowns = {}
    for name, col in DIMENSIONS.items():
        rows = db.execute(f"""SELECT e.{col} AS label, COUNT(*) AS n {ev} AND e.is_bot = 0 AND e.ts >= ?
                              GROUP BY e.{col} ORDER BY n DESC LIMIT 8""", (*params, since)).fetchall()
        breakdowns[name] = [{"label": (r["label"] or ("Direct" if name == "referrer" else "Unknown")),
                             "n": r["n"]} for r in rows]

    eng = db.execute(f"""SELECT e.engaged_ms, e.scroll_pct {ev} AND e.is_bot = 0 AND e.ts >= ?
                          AND e.engaged_ms IS NOT NULL""", (*params, since)).fetchall()
    landing_row = db.execute(f"""SELECT COUNT(*) AS views, COALESCE(SUM(e.outbound IS NOT NULL), 0) AS continued
                                 {ev} AND e.is_bot = 0 AND e.landing = 1 AND e.ts >= ?""", (*params, since)).fetchone()
    reading_now = db.execute(f"SELECT COUNT(*) {ev} AND e.engaged_at >= ?", (*params, ts - 25)).fetchone()[0]
    engagement = {"visits": len(eng), "median_seconds": None, "avg_seconds": None, "avg_scroll": None,
                  "reached_75_pct": None, "funnel": [], "reading_now": reading_now,
                  "landing_views": landing_row["views"],
                  "continued_pct": round(100 * landing_row["continued"] / landing_row["views"]) if landing_row["views"] else None}
    if eng:
        depths = [r["scroll_pct"] or 0 for r in eng]
        engagement.update(
            median_seconds=round(statistics.median(r["engaged_ms"] for r in eng) / 1000, 1),
            avg_seconds=round(statistics.mean(r["engaged_ms"] for r in eng) / 1000, 1),
            avg_scroll=round(statistics.mean(depths)),
            reached_75_pct=round(100 * sum(d >= 75 for d in depths) / len(depths)),
            funnel=[{"depth": d, "pct": round(100 * sum(x >= d for x in depths) / len(depths))} for d in (25, 50, 75, 100)])

    def pct(a, b):
        return None if not b else round((a - b) / b * 100, 1)

    report = {
        "period": period, "bucket": bucket, "generated_at": ts,
        "links": {k: (link_counts[k] or 0) for k in link_counts.keys()},
        "clicks": dict(cur) | {"human_change_pct": pct(cur["human"], prev["human"])},
        "series": series, "campaigns": campaigns, "top_links": top_links, "breakdowns": breakdowns,
        "engagement": engagement,
    }
    if include_alerts:
        report["alerts"] = compute_alerts(db, where, params, ts)
    report["filters"] = _filter_status()
    return report


def _filter_status() -> dict:
    m = current_app.extensions["models"].status()
    geo = current_app.extensions["geo"]
    return {
        "bot_filter": f"Random Forest ({m['bot_model'].get('trained_on')})" if m["bot_model"]["loaded"]
        else "rule-based fallback",
        "url_checker": f"Logistic Regression ({m['url_model'].get('trained_on')})" if m["url_model"]["loaded"]
        else "rule-based fallback",
        "geo": "GeoLite2" if geo.enabled else "off (set GEOLITE2_PATH)",
    }


@bp.get("/overview")
@login_required
def overview():
    where, params = _scope()
    return jsonify(build_report(get_db(), where, params))


@bp.get("/link/<int:link_id>")
@login_required
def link_report(link_id: int):
    if S.get_link_row(get_db(), link_id, g.user) is None:
        abort(404)
    where, params = _scope(link_id)
    return jsonify(build_report(get_db(), where, params))
