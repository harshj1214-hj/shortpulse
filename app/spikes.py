"""Link alerts for the dashboard, including click-spike detection.

Spike detection is a robust z-score, not a trained model: for each link we
compare human clicks in the last 60 minutes with the 24 hours before it,
using median and MAD (median absolute deviation) instead of mean and standard
deviation, so one earlier burst doesn't hide the next one. It works from a
link's first day and every alert can be explained in one sentence.
"""
from __future__ import annotations

import statistics

SPIKE_MIN_CLICKS = 10   # ignore 0 -> 3 jumps on quiet links
SPIKE_Z = 4.0
BOT_SHARE_MIN_EVENTS = 20


def robust_z(current: float, baseline: list[float]) -> tuple[float, float]:
    med = statistics.median(baseline)
    mad = statistics.median(abs(b - med) for b in baseline)
    # +1 keeps the score finite on links whose baseline is constant (often all zeros).
    return (current - med) / (1.4826 * mad + 1.0), med


def compute_alerts(db, where: str, params: list, ts: int) -> list[dict]:
    alerts: list[dict] = []
    base = f"FROM click_events e JOIN links l ON l.id = e.link_id WHERE {where}"

    # 1) click spikes ------------------------------------------------------
    hourly: dict[int, list[int]] = {}
    names: dict[int, tuple[str, str]] = {}
    for r in db.execute(f"""SELECT e.link_id, l.code, l.title, (? - e.ts) / 3600 AS h, COUNT(*) AS n
                            {base} AND e.is_bot = 0 AND e.ts > ?
                            GROUP BY e.link_id, h""", (ts, *params, ts - 25 * 3600)):
        if 0 <= r["h"] <= 24:
            hourly.setdefault(r["link_id"], [0] * 25)[r["h"]] = r["n"]
            names[r["link_id"]] = (r["code"], r["title"])
    for link_id, counts in hourly.items():
        current, baseline = counts[0], counts[1:]
        z, med = robust_z(current, baseline)
        if current >= SPIKE_MIN_CLICKS and z >= SPIKE_Z:
            code, title = names[link_id]
            alerts.append({"kind": "spike", "severity": "high", "link_id": link_id, "code": code, "title": title,
                           "message": f"{current} human clicks in the last hour; usually "
                                      + (f"about {med:g}" if med >= 1 else "less than 1") + " per hour.",
                           "score": round(z, 1)})

    # 2) bot-heavy traffic ---------------------------------------------------
    for r in db.execute(f"""SELECT e.link_id, l.code, l.title, COUNT(*) AS n, SUM(e.is_bot) AS bots
                            {base} AND e.ts > ? GROUP BY e.link_id
                            HAVING n >= ? AND bots * 2 >= n""", (*params, ts - 86400, BOT_SHARE_MIN_EVENTS)):
        alerts.append({"kind": "bots", "severity": "medium", "link_id": r["link_id"], "code": r["code"],
                       "title": r["title"],
                       "message": f"{r['bots']} of {r['n']} visits in the last 24 hours were automated."})

    # 3) rules about to bite --------------------------------------------------
    for r in db.execute(f"""SELECT l.id, l.code, l.title, l.click_count, r.max_clicks, r.expires_at
                            FROM links l JOIN rules r ON r.link_id = l.id
                            WHERE {where} AND l.status = 'active'
                              AND ((r.max_clicks IS NOT NULL AND l.click_count * 10 >= r.max_clicks * 9)
                                   OR (r.expires_at IS NOT NULL AND r.expires_at > ? AND r.expires_at <= ?))""",
                        (*params, ts, ts + 86400)):
        if r["max_clicks"] and r["click_count"] >= r["max_clicks"]:
            alerts.append({"kind": "limit", "severity": "low", "link_id": r["id"], "code": r["code"],
                           "title": r["title"], "message": f"Reached its limit of {r['max_clicks']} clicks."})
        elif r["max_clicks"] and r["click_count"] * 10 >= r["max_clicks"] * 9:
            alerts.append({"kind": "limit", "severity": "medium", "link_id": r["id"], "code": r["code"],
                           "title": r["title"],
                           "message": f"{r['click_count']} of {r['max_clicks']} allowed clicks used."})
        if r["expires_at"] and ts < r["expires_at"] <= ts + 86400:
            alerts.append({"kind": "expiring", "severity": "low", "link_id": r["id"], "code": r["code"],
                           "title": r["title"], "message": "Expires within 24 hours.",
                           "expires_at": r["expires_at"]})

    # 4) safety --------------------------------------------------------------
    for r in db.execute(f"""SELECT l.id, l.code, l.title, l.status, l.flagged, l.risk_reasons, l.disabled_reason
                            FROM links l WHERE {where} AND (l.flagged = 1 OR l.status = 'disabled')
                            ORDER BY l.updated_at DESC LIMIT 20""", params):
        if r["status"] == "disabled":
            alerts.append({"kind": "disabled", "severity": "high", "link_id": r["id"], "code": r["code"],
                           "title": r["title"],
                           "message": f"Disabled by an admin: {r['disabled_reason'] or 'no reason given'}."})
        else:
            alerts.append({"kind": "flagged", "severity": "medium", "link_id": r["id"], "code": r["code"],
                           "title": r["title"],
                           "message": "Waiting for admin review: " + (r["risk_reasons"] or "high URL risk score") + "."})

    order = {"high": 0, "medium": 1, "low": 2}
    alerts.sort(key=lambda a: order[a["severity"]])
    return alerts
