"""Roles, CSRF and rate-limit helpers.

Roles: Visitor (anonymous, may only open links), User (own links only),
Admin (everything). Ownership is enforced in SQL on every query, not in the UI.
"""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import secrets
from functools import wraps

from flask import abort, current_app, g, jsonify, redirect, request, session, url_for

from .db import get_db

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
# Anonymous password form and the cross-site engagement beacon: neither uses session privileges.
CSRF_EXEMPT_ENDPOINTS = {"redirect.follow", "engage.beacon"}


def load_user() -> None:
    g.user = None
    uid = session.get("user_id")
    if uid is not None:
        g.user = get_db().execute("SELECT id, email, role, status FROM users WHERE id = ?", (uid,)).fetchone()
        if g.user is None or g.user["status"] != "active":  # suspension/deletion ends existing sessions
            g.user = None
            session.clear()


def is_admin() -> bool:
    return bool(g.get("user")) and g.user["role"] == "admin"


def csrf_token() -> str:
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(32)
    return session["csrf"]


def check_csrf() -> None:
    if request.method in SAFE_METHODS or request.endpoint in CSRF_EXEMPT_ENDPOINTS:
        return
    if current_app.config.get("TESTING") and current_app.config.get("CSRF_DISABLED"):
        return
    sent = request.headers.get("X-CSRF-Token") or request.form.get("csrf_token") or ""
    expected = session.get("csrf", "")
    if not expected or not hmac.compare_digest(sent, expected):
        if request.path.startswith("/api/"):
            resp = jsonify(error="csrf", message="Your session expired. Reload the page and try again.")
            resp.status_code = 403
            abort(resp)
        abort(403)


def _wants_json() -> bool:
    return request.path.startswith("/api/")


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if g.get("user") is None:
            if _wants_json():
                return jsonify(error="auth", message="Sign in to continue."), 401
            return redirect(url_for("auth.login", next=request.full_path))
        return view(*args, **kwargs)
    return wrapped


def admin_required(view):
    @wraps(view)
    @login_required
    def wrapped(*args, **kwargs):
        if not is_admin():
            if _wants_json():
                return jsonify(error="forbidden", message="Admins only."), 403
            abort(403)
        return view(*args, **kwargs)
    return wrapped


def client_ip() -> str:
    # ProxyFix (configured in create_app) has already applied X-Forwarded-For
    # from the trusted proxy count, so remote_addr is the real client.
    return request.remote_addr or "0.0.0.0"


def rate_limited(key: str, bucket: tuple[float, float]) -> int:
    """Take a token from the C token bucket. Returns seconds to wait (0 = allowed)."""
    engine = current_app.extensions["engine"]
    wait_ms = engine.rate_take(key, bucket[0], bucket[1])
    return 0 if wait_ms == 0 else max(1, -(-wait_ms // 1000))


def too_many(retry_after: int, message: str = "Too many requests."):
    resp = jsonify(error="rate_limited", message=f"{message} Try again in {retry_after}s.",
                   retry_after=retry_after)
    resp.status_code = 429
    resp.headers["Retry-After"] = str(retry_after)
    return resp


def mask_ip(ip: str) -> str:
    """IPv4 -> 103.21.45.x, IPv6 -> its /48 network. Enough for analytics, not for identifying a person."""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return ""
    if addr.version == 4:
        a, b, c, _ = str(addr).split(".")
        return f"{a}.{b}.{c}.x"
    return f"{ipaddress.ip_network(f'{addr}/48', strict=False).network_address}/48"


def visitor_hash(ip: str, user_agent: str, salt: str) -> str:
    """Pseudonymous visitor id for unique-visitor counts. The raw IP never hits the database."""
    return hashlib.sha256(f"{salt}|{ip}|{user_agent}".encode()).hexdigest()[:32]
