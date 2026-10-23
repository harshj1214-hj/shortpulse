"""Bulk import from CSV (stdlib csv module).

Flow: upload -> preview (every row validated, duplicates found) -> user fixes
aliases in the table -> validate again -> commit. Commit re-validates on the
server and imports only valid rows; invalid rows are reported, not dropped
silently.

Columns (header names are case-insensitive):
  destination (or url)   required
  alias                  optional custom alias
  title, campaign        optional; campaign is created if it doesn't exist
  tags                   optional, separated by ; or |
  expires_at             optional ISO date/time, e.g. 2026-12-31 or 2026-12-31T18:00
  max_clicks             optional whole number
Passwords are deliberately not importable: a CSV of plain-text passwords is a liability.
"""
from __future__ import annotations

import csv
import io
from datetime import datetime, timedelta, timezone

from flask import Blueprint, current_app, g, jsonify, request

from . import services as S
from .db import get_db, now
from .security import login_required, rate_limited, too_many
from .validation import check_options, clean_tags, issue

bp = Blueprint("importer", __name__, url_prefix="/api/import")

HEADER_ALIASES = {
    "destination": "destination", "url": "destination", "long_url": "destination", "target": "destination",
    "alias": "alias", "slug": "alias", "code": "alias", "custom_alias": "alias",
    "title": "title", "name": "title",
    "campaign": "campaign", "tags": "tags",
    "expires_at": "expires_at", "expiry": "expires_at", "expires": "expires_at",
    "max_clicks": "max_clicks", "click_limit": "max_clicks",
}
FIELDS = ("destination", "alias", "title", "campaign", "tags", "expires_at", "max_clicks")


def parse_csv(text: str, max_rows: int) -> tuple[list[dict], list[str]]:
    reader = csv.reader(io.StringIO(text))
    try:
        header = next(reader)
    except StopIteration:
        return [], ["The file is empty."]
    cols = [HEADER_ALIASES.get(h.strip().lower().replace(" ", "_")) for h in header]
    if "destination" not in cols:
        return [], ["Add a header row with a 'destination' (or 'url') column."]
    rows, problems = [], []
    for line_no, values in enumerate(reader, start=2):
        if not any(v.strip() for v in values):
            continue
        if len(rows) >= max_rows:
            problems.append(f"Only the first {max_rows} rows were read.")
            break
        row = {f: "" for f in FIELDS}
        for col, val in zip(cols, values):
            if col:
                row[col] = val.strip()
        row["row"] = line_no
        rows.append(row)
    return rows, problems


def parse_expiry(value: str, tz_minutes: int) -> int | None:
    """ISO date/time -> epoch seconds. Naive values use the uploader's time zone.
    A bare date means 'valid through that day' (expires at the next midnight)."""
    value = (value or "").strip()
    if not value:
        return None
    tz = timezone(timedelta(minutes=tz_minutes))
    dt = datetime.fromisoformat(value.replace(" ", "T") if len(value) > 10 else value)
    if len(value) == 10:
        dt = dt + timedelta(days=1)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz)
    return int(dt.timestamp())


def _to_payload(row: dict, tz_minutes: int) -> tuple[dict, list[dict]]:
    errors = []
    payload = {"destination": row.get("destination", ""), "alias": row.get("alias") or None,
               "title": row.get("title", ""), "tags": row.get("tags", ""),
               "max_clicks": row.get("max_clicks") or None}
    try:
        payload["expires_at"] = parse_expiry(row.get("expires_at", ""), tz_minutes)
    except ValueError:
        errors.append(issue("expires_at", "expires_invalid", "Use a date like 2026-12-31 or 2026-12-31T18:00."))
        payload["expires_at"] = None
    return payload, errors


def validate_rows(db, rows: list[dict], tz_minutes: int) -> list[dict]:
    """Validate every row like the wizard does, plus duplicates inside the file."""
    seen_alias: dict[str, int] = {}
    seen_dest: dict[str, int] = {}
    existing = {r["destination"]: r["code"] for r in db.execute(
        "SELECT destination, code FROM links WHERE user_id = ?", (g.user["id"],))}
    ts = now()
    out = []
    for row in rows:
        payload, errors = _to_payload(row, tz_minutes)
        v = S.validate_link(db, payload["destination"], payload["alias"], taken_in_batch=seen_alias)
        _, opt_errors = check_options(db, g.user["id"], payload | {"campaign_id": None}, ts)
        errors += v["errors"] + opt_errors
        warnings = list(v["warnings"])
        campaign = (row.get("campaign") or "").strip()
        if len(campaign) > 60:
            errors.append(issue("campaign", "campaign_too_long", "Campaign names can be at most 60 characters."))
        if v["alias"] and v["alias"] not in seen_alias:
            seen_alias[v["alias"]] = row["row"]
        dest = v["destination"]
        if dest:
            if dest in seen_dest:
                warnings.append(issue("destination", "duplicate_destination",
                                      f"Same destination as row {seen_dest[dest]}."))
            else:
                seen_dest[dest] = row["row"]
            if dest in existing:
                warnings.append(issue("destination", "already_shortened",
                                      f"You already have a short link for this URL (/{existing[dest]})."))
        status = "error" if errors else ("warning" if warnings else "ok")
        out.append({**{f: row.get(f, "") for f in FIELDS}, "row": row["row"], "status": status,
                    "normalized_destination": dest, "errors": errors, "warnings": warnings,
                    "risk": v["risk"]})
    return out


def _summary(rows: list[dict]) -> dict:
    return {k: sum(r["status"] == k for r in rows) for k in ("ok", "warning", "error")} | {"total": len(rows)}


def _rows_from_json() -> tuple[list[dict] | None, int]:
    data = request.get_json(silent=True) or {}
    rows = data.get("rows")
    if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
        return None, 0
    clean = []
    for i, r in enumerate(rows[: current_app.config["IMPORT_MAX_ROWS"]]):
        clean.append({f: str(r.get(f) or "").strip() for f in FIELDS} | {"row": int(r.get("row") or i + 2)})
    return clean, int(data.get("tz", 0) or 0)


@bp.post("/preview")
@login_required
def preview():
    file = request.files.get("file")
    if file is None or not file.filename:
        return jsonify(error="file", message="Choose a CSV file to upload."), 422
    raw = file.read()
    try:
        text = raw.decode("utf-8-sig")  # handles the BOM Excel adds
    except UnicodeDecodeError:
        return jsonify(error="encoding", message="Save the file as UTF-8 CSV and upload it again."), 422
    rows, problems = parse_csv(text, current_app.config["IMPORT_MAX_ROWS"])
    if not rows and problems:
        return jsonify(error="format", message=problems[0]), 422
    validated = validate_rows(get_db(), rows, request.form.get("tz", 0, type=int))
    return jsonify(rows=validated, summary=_summary(validated), notes=problems)


@bp.post("/validate")
@login_required
def revalidate():
    rows, tz = _rows_from_json()
    if rows is None:
        return jsonify(error="rows", message="Send the rows to validate."), 400
    validated = validate_rows(get_db(), rows, tz)
    return jsonify(rows=validated, summary=_summary(validated))


@bp.post("/commit")
@login_required
def commit():
    wait = rate_limited(f"import:{g.user['id']}", current_app.config["RATE_IMPORT"])
    if wait:
        return too_many(wait, "Imports are limited to 3 every 5 minutes.")
    rows, tz = _rows_from_json()
    if rows is None:
        return jsonify(error="rows", message="Send the rows to import."), 400
    acknowledge = bool((request.get_json(silent=True) or {}).get("acknowledge_risk"))
    db = get_db()
    validated = validate_rows(db, rows, tz)
    campaign_ids: dict[str, int] = {}
    results = []
    for row in validated:
        if row["status"] == "error":
            results.append({"row": row["row"], "ok": False, "errors": row["errors"]})
            continue
        payload, _ = _to_payload(row, tz)
        name = (row.get("campaign") or "").strip()
        if name:
            if name not in campaign_ids:
                db.execute("INSERT INTO campaigns (user_id, name, created_at) VALUES (?, ?, ?) "
                           "ON CONFLICT(user_id, name) DO NOTHING", (g.user["id"], name, now()))
                campaign_ids[name] = db.execute("SELECT id FROM campaigns WHERE user_id = ? AND name = ?",
                                                (g.user["id"], name)).fetchone()[0]
            payload["campaign_id"] = campaign_ids[name]
        payload["tags"], _ = clean_tags(payload["tags"])
        payload["acknowledge_risk"] = acknowledge
        res = S.create_link(db, g.user["id"], payload)
        results.append({"row": row["row"], "ok": res["ok"], "code": res.get("code"),
                        "errors": res.get("errors", [])})
    imported = sum(r["ok"] for r in results)
    return jsonify(ok=True, imported=imported, failed=len(results) - imported, results=results)
