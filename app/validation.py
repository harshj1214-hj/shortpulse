"""Validation used by the wizard, the edit form and CSV import.

Errors block publishing; warnings are shown in step 2 of the wizard and the
user decides. Every check runs again on the server at publish time, because
the browser's earlier answer can't be trusted (and may be stale).
"""
from __future__ import annotations

import ipaddress
import re
import sqlite3
from urllib.parse import urlsplit, urlunsplit

from .engine import ALIAS_MESSAGES

MAX_URL_LENGTH = 2048
KNOWN_SHORTENERS = {
    "bit.ly", "tinyurl.com", "t.co", "goo.gl", "ow.ly", "is.gd", "buff.ly", "rebrand.ly",
    "cutt.ly", "shorturl.at", "rb.gy", "t.ly", "tiny.cc", "s.id",
}
PRIVATE_SUFFIXES = (".localhost", ".local", ".internal", ".lan", ".home.arpa")
LABEL_RE = re.compile(r"^[a-z0-9_](?:[a-z0-9_-]{0,61}[a-z0-9_])?$")
TAG_RE = re.compile(r"^[a-z0-9][a-z0-9 _-]{0,31}$")


def issue(field: str, code: str, message: str) -> dict:
    return {"field": field, "code": code, "message": message}


# ---------------------------------------------------------------------------
# URL
# ---------------------------------------------------------------------------
def normalize_url(raw: str) -> tuple[str | None, str | None, list[dict], list[dict]]:
    """Return (normalized_url, ascii_host, errors, warnings)."""
    errors: list[dict] = []
    warnings: list[dict] = []
    url = (raw or "").strip()
    if not url:
        return None, None, [issue("destination", "url_required", "Enter the destination URL.")], []
    if len(url) > MAX_URL_LENGTH:
        return None, None, [issue("destination", "url_too_long",
                                  f"URLs can be at most {MAX_URL_LENGTH} characters.")], []
    if any(ch.isspace() for ch in url):
        return None, None, [issue("destination", "url_spaces",
                                  "The URL contains spaces. Encode them as %20 or remove them.")], []
    if "://" not in url:
        if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:(?!\d)", url):  # javascript:, mailto:, data:
            return None, None, [issue("destination", "url_scheme",
                                      "Only http:// and https:// links can be shortened.")], []
        url = "https://" + url
        warnings.append(issue("destination", "url_scheme_added",
                              "No http:// or https:// given, so https:// was added."))
    try:
        parts = urlsplit(url)
        host = parts.hostname or ""
        port = parts.port
    except ValueError:
        return None, None, [issue("destination", "url_invalid", "This doesn't look like a valid URL.")], warnings

    scheme = parts.scheme.lower()
    if scheme not in ("http", "https"):
        return None, None, [issue("destination", "url_scheme",
                                  "Only http:// and https:// links can be shortened.")], warnings
    if not host:
        return None, None, [issue("destination", "url_no_host", "The URL has no domain name.")], warnings
    if parts.username is not None or parts.password is not None:
        return None, None, [issue("destination", "url_credentials",
                                  "Remove the 'name@' part before the domain. It is a common way to "
                                  "disguise where a link really goes.")], warnings

    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None

    if ip is not None:
        if not ip.is_global:
            return None, None, [issue("destination", "url_private_ip",
                                      "Links to private, local or reserved IP addresses aren't allowed.")], warnings
        ascii_host = host
    else:
        try:
            ascii_host = host.encode("idna").decode("ascii").lower()
        except UnicodeError:
            return None, None, [issue("destination", "url_bad_host", "The domain name is not valid.")], warnings
        labels = ascii_host.rstrip(".").split(".")
        if ascii_host == "localhost" or ascii_host.endswith(PRIVATE_SUFFIXES):
            return None, None, [issue("destination", "url_private_host",
                                      "Links to local network names aren't allowed.")], warnings
        if len(labels) < 2 or not all(LABEL_RE.match(lab) for lab in labels) \
                or not re.match(r"^([a-z]{2,63}|xn--[a-z0-9-]+)$", labels[-1]):
            return None, None, [issue("destination", "url_bad_host",
                                      "Enter a full domain name, like example.com.")], warnings
        ascii_host = ".".join(labels)

    netloc = f"[{ascii_host}]" if ":" in ascii_host else ascii_host
    if port is not None:
        netloc += f":{port}"
    normalized = urlunsplit((scheme, netloc, parts.path, parts.query, parts.fragment))
    return normalized, ascii_host, errors, warnings


def host_suffixes(host: str) -> list[str]:
    labels = host.split(".")
    return [".".join(labels[i:]) for i in range(len(labels))]


def blocklist_match(db: sqlite3.Connection, host: str) -> sqlite3.Row | None:
    """Exact domain or any parent domain on the blocklist (evil.com blocks a.evil.com)."""
    suffixes = host_suffixes(host)
    marks = ",".join("?" * len(suffixes))
    return db.execute(f"SELECT domain, reason FROM blocklist WHERE domain IN ({marks}) LIMIT 1",
                      suffixes).fetchone()


def risk_level(score: float, warn_threshold: float, high_threshold: float) -> str:
    if score >= max(high_threshold, warn_threshold):
        return "high"
    if score >= warn_threshold:
        return "elevated"
    return "low"


def check_destination(db, models, raw_url: str, self_hosts: set[str], high_threshold: float) -> dict:
    url, host, errors, warnings = normalize_url(raw_url)
    risk = {"score": None, "level": "unknown", "reasons": []}
    if url and host:
        if host in self_hosts:
            errors.append(issue("destination", "url_self",
                                "This URL points back to ShortPulse and would create a redirect loop."))
        blocked = blocklist_match(db, host)
        if blocked:
            reason = f" ({blocked['reason']})" if blocked["reason"] else ""
            errors.append(issue("destination", "url_blocklisted",
                                f"{blocked['domain']} is on the platform blocklist{reason}."))
        if host in KNOWN_SHORTENERS:
            warnings.append(issue("destination", "url_shortener",
                                  "This is already a short link. Chained short links hide the real "
                                  "destination; use the final URL if you can."))
        score, reasons = models.score_url(url)
        level = risk_level(score, models.url.threshold if models.url.model else 0.5, high_threshold)
        risk = {"score": round(score, 3), "level": level, "reasons": reasons}
        if level != "low":
            because = f" Signals: {', '.join(reasons)}." if reasons else ""
            warnings.append(issue(
                "destination", "url_risk_high" if level == "high" else "url_risk",
                ("This URL looks very similar to known phishing or malware links."
                 if level == "high" else "This URL has some traits common in unsafe links.") + because))
    return {"destination": url, "host": host, "errors": errors, "warnings": warnings, "risk": risk}


# ---------------------------------------------------------------------------
# Alias
# ---------------------------------------------------------------------------
def check_alias(engine, alias: str | None, taken_in_batch: dict[str, int] | None = None) -> list[dict]:
    if not alias:
        return []
    result = engine.alias_validate(alias)
    if result != 0:
        code, msg = ALIAS_MESSAGES[result]
        return [issue("alias", code, msg)]
    if taken_in_batch and alias in taken_in_batch:
        return [issue("alias", "alias_duplicate_in_file",
                      f"Row {taken_in_batch[alias]} already uses this alias.")]
    return []


# ---------------------------------------------------------------------------
# Step-2 options
# ---------------------------------------------------------------------------
def _int_or_none(value, field, label, errors, minimum=None):
    if value in (None, ""):
        return None
    try:
        n = int(value)
    except (TypeError, ValueError):
        errors.append(issue(field, f"{field}_invalid", f"{label} must be a whole number."))
        return None
    if minimum is not None and n < minimum:
        errors.append(issue(field, f"{field}_invalid", f"{label} must be at least {minimum}."))
        return None
    return n


def clean_tags(raw) -> tuple[list[str], list[dict]]:
    if raw in (None, ""):
        return [], []
    items = raw if isinstance(raw, list) else re.split(r"[;,|]", str(raw))
    tags, errors = [], []
    for t in items:
        t = str(t).strip().lower()
        if not t:
            continue
        if not TAG_RE.match(t):
            errors.append(issue("tags", "tag_invalid",
                                f"Tag '{t[:40]}' must be 1–32 letters, numbers, spaces, - or _."))
            continue
        if t not in tags:
            tags.append(t)
    if len(tags) > 10:
        errors.append(issue("tags", "too_many_tags", "Use at most 10 tags per link."))
    return tags[:10], errors


def check_options(db, user_id: int, data: dict, now: int, *, partial: bool = False) -> tuple[dict, list[dict]]:
    """Validate expiry, click limit, password, title, campaign and tags.
    With partial=True (PATCH) only keys present in `data` are validated/returned."""
    errors: list[dict] = []
    out: dict = {}

    def present(key):
        return (not partial) or key in data

    if present("title"):
        title = str(data.get("title") or "").strip()
        if len(title) > 120:
            errors.append(issue("title", "title_too_long", "Titles can be at most 120 characters."))
        out["title"] = title[:120]
    if present("expires_at"):
        exp = _int_or_none(data.get("expires_at"), "expires_at", "Expiry", errors)
        if exp is not None and exp <= now + 60:
            errors.append(issue("expires_at", "expires_in_past", "Pick an expiry time in the future."))
        out["expires_at"] = exp
    if present("max_clicks"):
        mc = _int_or_none(data.get("max_clicks"), "max_clicks", "Click limit", errors, minimum=1)
        if mc is not None and mc > 1_000_000_000:
            errors.append(issue("max_clicks", "max_clicks_invalid", "Click limit is too large."))
        out["max_clicks"] = mc
    if present("password"):
        pw = data.get("password") or ""
        if pw and not 4 <= len(pw) <= 128:
            errors.append(issue("password", "password_length", "Passwords must be 4–128 characters."))
        out["password"] = pw or None
    if present("campaign_id"):
        cid = _int_or_none(data.get("campaign_id"), "campaign_id", "Campaign", errors)
        if cid is not None and not db.execute("SELECT 1 FROM campaigns WHERE id = ? AND user_id = ?",
                                              (cid, user_id)).fetchone():
            errors.append(issue("campaign_id", "campaign_not_found", "That campaign doesn't exist."))
        out["campaign_id"] = cid
    for key, default in (("open_in_app", True), ("track_engagement", False)):
        if present(key):
            v = data.get(key, default)
            out[key] = v if isinstance(v, bool) else str(v).strip().lower() in ("1", "true", "yes", "on")
    if present("tags"):
        tags, tag_errors = clean_tags(data.get("tags"))
        errors.extend(tag_errors)
        out["tags"] = tags
    return out, errors
